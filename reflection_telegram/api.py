"""Public API for Reflection Telegram.

This is the surface other apps should call. Everything here is whitelisted, so
the same functions work from Python (`frappe.call` / a direct import) and over
`/api/method/`.

	from reflection_telegram import api

	api.send_message(telegram_user="2012381-Send Agri Statement", message="Hello")
	api.send_to_party(party_type="Supplier", party="2012381", message="Hello")
	api.send_bulk(messages=[...])          # 250 messages, paced automatically

Single sends go out immediately by default; anything bulk is queued and paced by
`reflection_telegram.outbox`. Pass `queue=1` to a single send when the caller is
in a request and should not wait on the network.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, cstr

from reflection_telegram import message_log, onboarding, outbox, telegram_client
from reflection_telegram.utils import render_pdf


@frappe.whitelist()
def send_message(
	telegram_user: str,
	message: str = None,
	file_url: str = None,
	parse_mode: str = None,
	reference_doctype: str = None,
	reference_name: str = None,
	attach_print: int = 0,
	print_format: str = None,
	queue: int = 0,
) -> dict:
	"""Send one message to one linked Telegram user.

	Args:
		telegram_user: name of a Telegram User Settings record.
		message: the text. Long text is split across several messages rather than
			truncated, since Telegram rejects anything over 4096 characters.
		file_url: a Frappe file URL (public or private) to send as a document.
		parse_mode: "HTML" or "MarkdownV2". Plain text when omitted.
		reference_doctype, reference_name: recorded against the message, and used
			as the source document when `attach_print` is set.
		attach_print: render the reference document as a PDF and attach it.
		print_format: which print format to render. Defaults to the doctype's.
		queue: queue instead of sending inline, so the caller returns immediately.

	Returns:
		{"status": "Sent"} or {"status": "Queued", "outbox": "<name>"}.
	"""
	settings, chat_id = _resolve_target(telegram_user)

	document = filename = None
	if attach_print and reference_doctype and reference_name:
		filename, document = render_pdf(reference_doctype, reference_name, print_format)
	elif file_url:
		filename, document = telegram_client.resolve_file(file_url)

	if cint(queue):
		names = outbox.enqueue(
			[
				{
					"telegram_user": telegram_user,
					"message": message,
					"file_url": file_url,
					"parse_mode": parse_mode,
					"reference_doctype": reference_doctype,
					"reference_name": reference_name,
				}
			],
			telegram_settings=settings,
		)
		return {"status": "Queued", "outbox": names[0]}

	try:
		message_ids = telegram_client.send(
			telegram_client.get_token(settings),
			chat_id,
			message,
			document=document,
			filename=filename,
			parse_mode=parse_mode,
		)
	except Exception as exc:
		# Log the failure before re-raising, so a caller that swallows the
		# exception still leaves a trace of what was attempted.
		message_log.record_outgoing(
			settings,
			telegram_user=telegram_user,
			chat_id=chat_id,
			message=message,
			error=exc,
			attachment=filename,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
		raise

	log = message_log.record_outgoing(
		settings,
		telegram_user=telegram_user,
		chat_id=chat_id,
		message=message,
		message_ids=message_ids,
		attachment=filename,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)

	return {"status": "Sent", "message_ids": message_ids, "log": log}


@frappe.whitelist()
def send_to_party(
	party_type: str, party: str, message: str = None, telegram_settings: str = None, **kwargs
) -> dict:
	"""Send by business record instead of by Telegram User Settings name.

	Most callers know they want to message supplier `2012381`, not which linking
	record that maps to.
	"""
	telegram_user = resolve_party(party_type, party, telegram_settings)
	if not telegram_user:
		frappe.throw(
			_("{0} {1} is not linked to Telegram yet").format(party_type, party),
			frappe.DoesNotExistError,
		)

	return send_message(telegram_user=telegram_user, message=message, **kwargs)


@frappe.whitelist()
def send_bulk(
	messages, telegram_settings: str = None, title: str = None, rate: int = 0, create_broadcast: int = 1
) -> dict:
	"""Queue many messages, paced so Telegram does not restrict the bot.

	Args:
		messages: list of dicts, each with `telegram_user` plus any argument
			`send_message` accepts (`message`, `file_url`, `parse_mode`,
			`reference_doctype`, `reference_name`). Recipients are independent --
			each message goes to its own user.
		telegram_settings: which bot sends. Inferred from the first recipient when
			omitted.
		title: label for the Telegram Broadcast record.
		rate: messages per minute for this run only. Defaults to the rate on
			Telegram Settings (20).
		create_broadcast: set 0 to queue without a Broadcast record to track it.

	Returns:
		{"broadcast": "<name>", "queued": <count>, "skipped": <count>}. Nothing is
		sent inline -- the scheduler drains the queue a minute at a time.

	Recipients who never scanned their QR raise, because that is a mistake in the
	caller's list. Recipients who have since blocked the bot are dropped and
	counted instead -- one person blocking should not stop a run of 250.
	"""
	messages = _as_list(messages)
	if not messages:
		return {"broadcast": None, "queued": 0, "skipped": 0}

	unlinked = [m["telegram_user"] for m in messages if not _chat_id(m["telegram_user"])]
	if unlinked:
		frappe.throw(
			_("{0} recipients have not scanned their QR code yet, starting with {1}").format(
				len(unlinked), unlinked[0]
			)
		)

	total = len(messages)
	messages = [m for m in messages if _is_sendable(m["telegram_user"])]
	skipped = total - len(messages)

	if not messages:
		frappe.throw(_("Every recipient on that list has blocked the bot"))

	telegram_settings = telegram_settings or frappe.db.get_value(
		"Telegram User Settings", messages[0]["telegram_user"], "telegram_settings"
	)

	broadcast = None
	if cint(create_broadcast):
		broadcast = frappe.get_doc(
			{
				"doctype": "Telegram Broadcast",
				"title": title or _("Bulk send of {0} messages").format(len(messages)),
				"telegram_settings": telegram_settings,
				"messages_per_minute": cint(rate),
				"total": len(messages),
				"status": "Queued",
			}
		).insert(ignore_permissions=True)

	queued = outbox.enqueue(
		messages,
		telegram_settings=telegram_settings,
		broadcast=broadcast.name if broadcast else None,
		rate=rate,
	)

	return {
		"broadcast": broadcast.name if broadcast else None,
		"queued": len(queued),
		"skipped": skipped,
	}


@frappe.whitelist()
def resolve_party(party_type: str, party: str, telegram_settings: str = None) -> str | None:
	"""Find the Telegram User Settings record for a business record, if linked."""
	filters = {"party": party_type, "telegram_user": party, "telegram_chat_id": ["is", "set"]}
	if telegram_settings:
		filters["telegram_settings"] = telegram_settings

	return frappe.db.get_value("Telegram User Settings", filters, "name")


@frappe.whitelist()
def get_status(telegram_user: str) -> dict:
	"""Whether a recipient is ready to receive, and the QR link if not."""
	doc = frappe.get_doc("Telegram User Settings", telegram_user)
	return {
		"telegram_user": doc.name,
		"party_type": doc.party,
		"party": doc.telegram_user,
		"linked": bool(doc.telegram_chat_id),
		"chat_id": doc.telegram_chat_id,
		"linked_on": doc.linked_on,
		"deep_link": doc.deep_link,
		"qr_code": doc.qr_code,
	}


@frappe.whitelist()
def ensure_link(party_type: str, party: str, telegram_settings: str, is_group_chat: int = 0) -> dict:
	"""Get -- or create -- the linking record for a party, QR code included.

	Idempotent: calling it for an already-linked party returns the existing
	record untouched rather than rotating a working payload.
	"""
	name = frappe.db.get_value(
		"Telegram User Settings",
		{"party": party_type, "telegram_user": party, "telegram_settings": telegram_settings},
		"name",
	)

	if name:
		doc = frappe.get_doc("Telegram User Settings", name)
		if not doc.qr_code or not doc.deep_link:
			onboarding.refresh(doc)
			doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{
				"doctype": "Telegram User Settings",
				"party": party_type,
				"telegram_user": party,
				"telegram_settings": telegram_settings,
				"is_group_chat": cint(is_group_chat),
			}
		).insert(ignore_permissions=True)

	return get_status(doc.name)


def _resolve_target(telegram_user: str) -> tuple[str, str]:
	settings, chat_id, chat_status = frappe.db.get_value(
		"Telegram User Settings",
		telegram_user,
		["telegram_settings", "telegram_chat_id", "chat_status"],
	) or (None, None, None)

	if not settings:
		frappe.throw(
			_("Telegram User Settings {0} not found").format(telegram_user), frappe.DoesNotExistError
		)

	if not chat_id:
		frappe.throw(
			_("{0} has no chat id yet -- the QR code has not been scanned").format(telegram_user)
		)

	if chat_status in ("Blocked", "Left") and cint(
		frappe.db.get_value("Telegram Settings", settings, "auto_disable_blocked")
	):
		frappe.throw(
			_("{0} has blocked the bot, so Telegram will reject the message").format(telegram_user)
		)

	return settings, chat_id


def _is_sendable(telegram_user: str) -> bool:
	"""Linked, and not known to have blocked the bot."""
	row = frappe.db.get_value(
		"Telegram User Settings", telegram_user, ["telegram_chat_id", "chat_status"], as_dict=True
	)
	if not row or not row.telegram_chat_id:
		return False

	return row.chat_status not in ("Blocked", "Left")


def _chat_id(telegram_user: str) -> str | None:
	return frappe.db.get_value("Telegram User Settings", telegram_user, "telegram_chat_id")


def _as_list(value) -> list[dict]:
	"""Accept both a Python list and the JSON string an HTTP caller sends."""
	if isinstance(value, str):
		value = json.loads(value)
	return list(value or [])
