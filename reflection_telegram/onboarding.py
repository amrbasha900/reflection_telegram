"""QR onboarding: turn a Telegram User Settings record into a scannable link.

The flow this replaces asked the user to copy a token, paste it into the bot
chat by hand, and then have someone press "Get Chat ID" within 24 hours. A
Telegram deep link does all of that in one scan: opening
`https://t.me/<bot>?start=<payload>` shows a START button, and pressing it makes
Telegram send `/start <payload>` to the bot on the user's behalf. The webhook (or
the polling fallback) then matches the payload back to this record and stores the
chat id.

https://core.telegram.org/bots/features#deep-linking
"""

import binascii
import io
import os
import re

import frappe
from frappe import _
from frappe.utils import cstr, now_datetime
from frappe.utils.file_manager import save_file

# Telegram allows at most 64 base64url characters in a deep link payload.
PAYLOAD_LENGTH = 32
PAYLOAD_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def build_payload() -> str:
	"""A deep-link-safe token.

	Hex only, and deliberately no leading slash: the old generator prefixed group
	tokens with "/" so that Telegram's group privacy mode would treat the pasted
	text as a command, but a deep link arrives as `/start <payload>` and is a
	command already.
	"""
	return binascii.hexlify(os.urandom(PAYLOAD_LENGTH // 2)).decode()


def normalise_payload(text: str) -> str:
	"""Reduce whatever the user actually sent to the bot back to a bare payload.

	Accepts the deep link form (`/start abc`), the legacy group form (`/abc`) and
	a plain paste (`abc`), so records created before deep links still link up.
	"""
	text = cstr(text).strip()
	if not text:
		return ""

	if text.lower().startswith("/start"):
		text = text[len("/start") :].strip()

	return text.lstrip("/").strip()


def deep_link(bot_name: str, payload: str, is_group_chat: bool = False) -> str:
	"""`startgroup` makes Telegram offer a group picker and add the bot to it."""
	if not bot_name or not payload:
		return ""

	action = "startgroup" if is_group_chat else "start"
	return f"https://t.me/{cstr(bot_name).lstrip('@')}?{action}={payload}"


def qr_png(data: str) -> bytes:
	import qrcode

	qr = qrcode.QRCode(
		version=None,
		error_correction=qrcode.constants.ERROR_CORRECT_M,
		box_size=10,
		border=2,
	)
	qr.add_data(data)
	qr.make(fit=True)

	buffer = io.BytesIO()
	qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
	return buffer.getvalue()


def assign_link(doc, force: bool = False):
	"""Give `doc` a payload and a deep link.

	Split from the QR image because the image is stored as a File attached to
	`doc.name`, which does not exist yet during `before_insert`.

	Existing links are left alone unless `force` is set -- rotating the payload of
	an already linked user would orphan a working link for no reason.
	"""
	settings = doc.telegram_settings
	if not settings:
		return

	bot_name = frappe.db.get_value("Telegram Settings", settings, "bot_name")
	if not bot_name:
		frappe.throw(
			_("Telegram Settings {0} has no bot name, so no QR link can be built").format(settings)
		)

	if force or not doc.telegram_token or not PAYLOAD_PATTERN.match(cstr(doc.telegram_token)):
		doc.telegram_token = build_payload()

	doc.deep_link = deep_link(bot_name, doc.telegram_token, doc.is_group_chat)


def refresh(doc, force: bool = False):
	"""Rebuild the link and its QR image for an already-saved document."""
	assign_link(doc, force=force)
	attach_qr(doc)


def attach_qr(doc):
	"""Store the QR as a real File so it prints and downloads like any attachment."""
	if not doc.deep_link:
		return

	remove_existing_qr(doc)

	file_doc = save_file(
		f"telegram-qr-{frappe.scrub(doc.name)}.png",
		qr_png(doc.deep_link),
		doc.doctype,
		doc.name,
		df="qr_code",
		is_private=0,
	)
	doc.qr_code = file_doc.file_url


def remove_existing_qr(doc):
	for name in frappe.get_all(
		"File",
		filters={"attached_to_doctype": doc.doctype, "attached_to_name": doc.name, "attached_to_field": "qr_code"},
		pluck="name",
	):
		frappe.delete_doc("File", name, force=True, ignore_permissions=True, delete_permanently=True)


def mark_linked(doc, chat_id, chat_title=None):
	"""Record a successful link. Returns True when something actually changed."""
	chat_id = cstr(chat_id)
	if cstr(doc.telegram_chat_id) == chat_id:
		return False

	doc.db_set(
		{
			"telegram_chat_id": chat_id,
			"linked_on": now_datetime(),
			"linked_chat_title": cstr(chat_title)[:140] if chat_title else None,
		},
		update_modified=False,
	)
	return True


def find_by_payload(payload: str, telegram_settings: str = None):
	"""Locate the Telegram User Settings record a payload belongs to."""
	payload = normalise_payload(payload)
	if not payload:
		return None

	filters = {"telegram_token": payload}
	if telegram_settings:
		filters["telegram_settings"] = telegram_settings

	name = frappe.db.get_value("Telegram User Settings", filters, "name")
	return frappe.get_doc("Telegram User Settings", name) if name else None
