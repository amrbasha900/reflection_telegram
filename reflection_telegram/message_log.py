"""The record of what this bot sent and received.

Telegram gives bots **no delivery or read receipt**. `sendMessage` returns a
message id meaning Telegram accepted the message, and that is the end of the
signal -- there is no update type reporting delivery, and nothing arrives later
to say a message was read. What the log can honestly record is therefore:

* outgoing: accepted (with the message id) or failed (with the reason),
* incoming: whatever the person actually sent back,
* `my_chat_member`: the one real status signal, fired the moment someone blocks
  or unblocks the bot.

Anything claiming more than that would be invented.
"""

import json

import frappe
from frappe.utils import cint, cstr, now_datetime

TRUNCATE_MESSAGE_AT = 5000


def _setting(telegram_settings: str, field: str, default=0):
	value = frappe.db.get_value("Telegram Settings", telegram_settings, field)
	return cint(default if value is None else value)


def record_outgoing(
	telegram_settings: str,
	telegram_user: str = None,
	chat_id: str = None,
	message: str = None,
	message_ids: list = None,
	error=None,
	attachment: str = None,
	reference_doctype: str = None,
	reference_name: str = None,
	outbox: str = None,
	broadcast: str = None,
):
	"""Log one outgoing message. Never raises -- logging must not break sending."""
	if not _setting(telegram_settings, "log_outgoing", default=1):
		return None

	try:
		party = party_name = None
		if telegram_user:
			party, party_name = frappe.db.get_value(
				"Telegram User Settings", telegram_user, ["party", "telegram_user"]
			) or (None, None)

		doc = frappe.get_doc(
			{
				"doctype": "Telegram Message Log",
				"direction": "Outgoing",
				"status": "Failed" if error else "Sent",
				"telegram_settings": telegram_settings,
				"telegram_user": telegram_user,
				"party": party,
				"party_name": party_name,
				"chat_id": cstr(chat_id),
				"message_id": (message_ids or [None])[0],
				"timestamp": now_datetime(),
				"message": cstr(message)[:TRUNCATE_MESSAGE_AT],
				"attachment": attachment,
				"error": cstr(error)[:500] if error else None,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"outbox": outbox,
				"broadcast": broadcast,
			}
		).insert(ignore_permissions=True)

		return doc.name
	except Exception:
		frappe.log_error(title="Could not write Telegram message log", message=frappe.get_traceback())
		return None


def record_incoming(telegram_settings: str, update: dict, telegram_user: str = None):
	"""Log an inbound message, if this bot is configured to keep them.

	Stores the full Telegram payload alongside the extracted fields -- an inbound
	message can carry photos, documents, locations and contacts, and guessing in
	advance which of those matter would lose the rest.
	"""
	if not _setting(telegram_settings, "save_incoming_messages", default=0):
		return None

	message = (update or {}).get("message") or {}
	chat = message.get("chat") or {}
	sender = message.get("from") or {}

	try:
		party = party_name = None
		if telegram_user:
			party, party_name = frappe.db.get_value(
				"Telegram User Settings", telegram_user, ["party", "telegram_user"]
			) or (None, None)

		doc = frappe.get_doc(
			{
				"doctype": "Telegram Message Log",
				"direction": "Incoming",
				"status": "Received",
				"telegram_settings": telegram_settings,
				"telegram_user": telegram_user,
				"party": party,
				"party_name": party_name,
				"chat_id": cstr(chat.get("id")),
				"message_id": message.get("message_id"),
				"timestamp": now_datetime(),
				"message": cstr(message.get("text") or message.get("caption") or "")[:TRUNCATE_MESSAGE_AT],
				"attachment": describe_attachment(message),
				"from_first_name": " ".join(
					filter(None, [sender.get("first_name"), sender.get("last_name")])
				)
				or None,
				"from_username": sender.get("username"),
				"from_user_id": cstr(sender.get("id")) if sender.get("id") else None,
				"chat_type": chat.get("type"),
				"raw_payload": json.dumps(update, ensure_ascii=False, indent=1),
			}
		).insert(ignore_permissions=True)

		return doc.name
	except Exception:
		frappe.log_error(title="Could not write Telegram message log", message=frappe.get_traceback())
		return None


def describe_attachment(message: dict) -> str | None:
	"""A short human label for whatever media rode along with the text."""
	if message.get("document"):
		return message["document"].get("file_name") or "document"

	for kind in ("photo", "video", "audio", "voice", "sticker", "animation", "video_note"):
		if message.get(kind):
			return kind

	for kind in ("location", "contact", "poll", "venue", "dice"):
		if message.get(kind):
			return kind

	return None


def find_user_by_chat(telegram_settings: str, chat_id) -> str | None:
	return frappe.db.get_value(
		"Telegram User Settings",
		{"telegram_settings": telegram_settings, "telegram_chat_id": cstr(chat_id)},
		"name",
	)


def purge():
	"""Daily cleanup. Without it the log is a table that only ever grows."""
	for settings in frappe.get_all(
		"Telegram Settings", fields=["name", "log_retention_days"]
	):
		days = cint(settings.log_retention_days)
		if days <= 0:
			continue

		frappe.db.delete(
			"Telegram Message Log",
			{
				"telegram_settings": settings.name,
				"timestamp": ["<", frappe.utils.add_days(now_datetime(), -days)],
			},
		)

	frappe.db.commit()
