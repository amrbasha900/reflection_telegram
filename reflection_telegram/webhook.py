"""Receiving side: how a scanned QR turns into a stored chat id.

Two transports, both feeding the same handler:

* **Webhook** (instant). Telegram POSTs each update to `receive` below. This is
  the default because linking then completes while the user is still looking at
  their phone.
* **Polling** (every 5 minutes, opt-in per Telegram Settings). A scheduler job
  calls getUpdates instead. Useful when the site is not reachable from the
  internet.

The two are mutually exclusive at Telegram's end: registering a webhook makes
getUpdates return nothing, so `poll` skips any bot that has one registered.
"""

import binascii
import hmac
import json
import os

import frappe
from frappe import _
from frappe.utils import cstr

from reflection_telegram import message_log, onboarding, telegram_client
from reflection_telegram.utils import site_base_url

WEBHOOK_METHOD = "reflection_telegram.webhook.receive"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def build_webhook_url(settings_name: str) -> str:
	return f"{site_base_url()}/api/method/{WEBHOOK_METHOD}?settings={frappe.utils.quoted(settings_name)}"


@frappe.whitelist()
def register(telegram_settings: str) -> dict:
	"""Point the bot at this site and store the shared secret.

	`allowed_updates` is narrowed to the two kinds that matter: `message` carries
	the payload a scan sends, and `my_chat_member` is the only status signal
	Telegram gives a bot -- it fires the moment someone blocks or unblocks it.
	Asking for less means Telegram sends less.
	"""
	frappe.only_for("System Manager")

	settings = frappe.get_doc("Telegram Settings", telegram_settings)
	token = telegram_client.get_token(telegram_settings)

	secret = settings.webhook_secret or binascii.hexlify(os.urandom(16)).decode()
	url = build_webhook_url(telegram_settings)

	telegram_client.call_api(
		token,
		"set_webhook",
		url=url,
		secret_token=secret,
		allowed_updates=["message", "my_chat_member"],
		drop_pending_updates=False,
	)

	settings.db_set(
		{"webhook_secret": secret, "webhook_url": url, "webhook_status": "Registered"},
		update_modified=False,
	)

	return {"webhook_url": url, "status": "Registered"}


@frappe.whitelist()
def unregister(telegram_settings: str) -> dict:
	frappe.only_for("System Manager")

	token = telegram_client.get_token(telegram_settings)
	telegram_client.call_api(token, "delete_webhook", drop_pending_updates=False)

	frappe.db.set_value(
		"Telegram Settings",
		telegram_settings,
		{"webhook_status": "Not Registered", "webhook_url": None},
		update_modified=False,
	)

	return {"status": "Not Registered"}


@frappe.whitelist()
def status(telegram_settings: str) -> dict:
	"""What Telegram itself thinks, rather than what we last wrote down."""
	frappe.only_for("System Manager")

	token = telegram_client.get_token(telegram_settings)
	info = telegram_client.call_api(token, "get_webhook_info")
	me = telegram_client.call_api(token, "get_me")

	registered = bool(info.url)
	frappe.db.set_value(
		"Telegram Settings",
		telegram_settings,
		{"webhook_status": "Registered" if registered else "Not Registered"},
		update_modified=False,
	)

	return {
		"bot_username": me.username,
		"webhook_url": info.url or "",
		"pending_update_count": info.pending_update_count,
		"last_error_message": getattr(info, "last_error_message", None),
		"registered": registered,
	}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive(settings: str = None, **kwargs):
	"""Telegram's callback. Never raises -- a 500 makes Telegram retry forever.

	`settings` is read off the request rather than taken from the signature:
	Frappe rebuilds form_dict from a JSON body alone, so query arguments on a
	JSON POST never reach the function. `**kwargs` absorbs the update payload,
	which Frappe otherwise tries to map onto parameters.
	"""
	frappe.set_user("Administrator")

	try:
		settings = frappe.request.args.get("settings") or settings
		telegram_settings = _authenticate(settings)
		update = json.loads(frappe.request.get_data(as_text=True) or "{}")
		handle_update(update, telegram_settings)
	except frappe.PermissionError:
		raise
	except Exception:
		frappe.log_error(
			title="Telegram webhook failed",
			message=frappe.get_traceback(with_context=True),
		)

	frappe.response["message"] = "ok"


def _authenticate(settings: str) -> str:
	"""The secret header is the only thing standing between this open endpoint
	and anyone who guesses the URL, so a mismatch is a hard stop."""
	if not settings or not frappe.db.exists("Telegram Settings", settings):
		raise frappe.PermissionError

	expected = frappe.db.get_value("Telegram Settings", settings, "webhook_secret")
	provided = frappe.get_request_header(SECRET_HEADER)

	if not expected or not provided or not hmac.compare_digest(cstr(provided), cstr(expected)):
		raise frappe.PermissionError

	return settings


def handle_update(update: dict, telegram_settings: str) -> bool:
	"""Route one Telegram update. True only when it completed a QR link."""
	update = update or {}

	if update.get("my_chat_member"):
		handle_membership(update["my_chat_member"], telegram_settings)
		return False

	return handle_message(update, telegram_settings)


def handle_message(update: dict, telegram_settings: str) -> bool:
	"""Match an inbound message to a pending QR, and log it either way."""
	message = update.get("message") or {}
	chat = message.get("chat") or {}
	text = message.get("text")

	if not chat.get("id"):
		return False

	doc = onboarding.find_by_payload(text, telegram_settings) if text else None
	linked = False

	if doc:
		chat_title = chat.get("title") or " ".join(
			filter(None, [chat.get("first_name"), chat.get("last_name")])
		)
		if onboarding.mark_linked(doc, chat["id"], chat_title):
			frappe.db.commit()
			_confirm(telegram_settings, chat["id"], doc)
			linked = True
		telegram_user = doc.name
	else:
		telegram_user = message_log.find_user_by_chat(telegram_settings, chat["id"])

	message_log.record_incoming(telegram_settings, update, telegram_user)
	frappe.db.commit()

	return linked


def handle_membership(payload: dict, telegram_settings: str):
	"""Track blocks and unblocks.

	This is the closest thing the Bot API has to a delivery status: Telegram
	reports a block the moment it happens, which is worth far more than
	discovering it 250 messages later through a 403.
	"""
	chat = payload.get("chat") or {}
	status = ((payload.get("new_chat_member") or {}).get("status") or "").lower()

	telegram_user = message_log.find_user_by_chat(telegram_settings, chat.get("id"))
	if not telegram_user:
		return

	if status == "kicked":
		chat_status = "Blocked"
	elif status == "left":
		chat_status = "Left"
	else:
		chat_status = "Active"

	frappe.db.set_value(
		"Telegram User Settings",
		telegram_user,
		{"chat_status": chat_status, "status_changed_on": frappe.utils.now_datetime()},
		update_modified=False,
	)
	frappe.db.commit()


def _confirm(telegram_settings: str, chat_id, doc):
	"""Tell the person the scan worked. Best effort -- the link is already saved."""
	try:
		telegram_client.send(
			telegram_client.get_token(telegram_settings),
			chat_id,
			_("Linked successfully to {0} {1}.").format(doc.party, doc.telegram_user),
		)
	except Exception:
		frappe.log_error(title="Telegram link confirmation failed", message=frappe.get_traceback())


def poll():
	"""Scheduler fallback for bots with `enable_polling` and no webhook.

	getUpdates only returns updates Telegram has not had confirmed yet, and
	confirmation happens by passing an offset past the last id seen -- so the
	offset is persisted per bot rather than re-reading the same backlog forever.
	"""
	for settings in frappe.get_all(
		"Telegram Settings",
		filters={"enable_polling": 1},
		fields=["name", "last_update_id", "webhook_status"],
	):
		if settings.webhook_status == "Registered":
			# Telegram refuses getUpdates while a webhook is live.
			continue

		try:
			_poll_one(settings)
		except Exception:
			frappe.log_error(
				title=f"Telegram polling failed for {settings.name}",
				message=frappe.get_traceback(),
			)


def _poll_one(settings):
	token = telegram_client.get_token(settings.name)
	offset = (settings.last_update_id or 0) + 1 if settings.last_update_id else None

	updates = telegram_client.call_api(token, "get_updates", offset=offset, limit=100, timeout=0)
	if not updates:
		return

	highest = settings.last_update_id or 0
	for update in updates:
		highest = max(highest, update.update_id)
		handle_update(update.to_dict(), settings.name)

	frappe.db.set_value(
		"Telegram Settings", settings.name, "last_update_id", highest, update_modified=False
	)
	frappe.db.commit()
