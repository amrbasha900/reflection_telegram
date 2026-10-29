# -*- coding: utf-8 -*-
# Copyright (c) 2019, Youssef Restom and contributors
# Copyright (c) 2026, Amr Basha and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from reflection_telegram import onboarding, telegram_client, webhook


#: Set only by the linking flow (webhook, check_link) and the Telegram status
#: updates, never typed: a changed chat id silently redirects a party's messages.
LINK_FIELDS = ("telegram_chat_id", "linked_on", "linked_chat_title", "chat_status", "status_changed_on", "telegram_token")


class TelegramUserSettings(Document):
	def validate(self):
		# flags.linking: set by this app's own linking code (api.ensure_link), never by a request.
		if self.is_new() or self.flags.linking or "System Manager" in frappe.get_roles():
			return
		changed = [f for f in LINK_FIELDS if self.has_value_changed(f)]
		if changed:
			frappe.throw(
				_("Only the Telegram linking flow can change {0}").format(", ".join(changed)),
				frappe.PermissionError,
			)

	def before_insert(self):
		# The QR image is attached in after_insert -- it needs self.name to exist.
		onboarding.assign_link(self)

	def after_insert(self):
		onboarding.attach_qr(self)
		self.db_set("qr_code", self.qr_code, update_modified=False)

	def on_update(self):
		"""`is_group_chat` decides between ?start= and ?startgroup=, so a change
		there invalidates the stored link and the QR printed from it."""
		if not self.has_value_changed("is_group_chat"):
			return

		onboarding.assign_link(self)
		onboarding.attach_qr(self)
		self.db_set(
			{"deep_link": self.deep_link, "qr_code": self.qr_code}, update_modified=False
		)

	@frappe.whitelist()
	def regenerate_qr(self):
		"""Issue a fresh payload and QR, dropping any existing chat link.

		Deliberately destructive: this is the "someone else has my QR code" button,
		so the old payload must stop working.
		"""
		self.check_permission("write")
		onboarding.refresh(self, force=True)
		self.db_set(
			{
				"telegram_token": self.telegram_token,
				"deep_link": self.deep_link,
				"qr_code": self.qr_code,
				"telegram_chat_id": None,
				"linked_on": None,
				"linked_chat_title": None,
			},
			update_modified=False,
		)
		return {"deep_link": self.deep_link, "qr_code": self.qr_code}

	@frappe.whitelist()
	def check_link(self):
		"""Ask Telegram directly whether this QR has been scanned yet.

		The webhook normally records the link on its own; this is the manual pull
		for sites polling instead, or for checking without waiting for the tick.
		"""
		self.check_permission("write")
		if self.telegram_chat_id:
			return {"linked": True, "chat_id": self.telegram_chat_id}

		token = telegram_client.get_token(self.telegram_settings)
		info = telegram_client.call_api(token, "get_webhook_info")

		if info.url:
			frappe.msgprint(
				_(
					"A webhook is registered for this bot, so linking happens automatically "
					"the moment the QR is scanned. Nothing to pull."
				)
			)
			return {"linked": False, "chat_id": None}

		for update in telegram_client.call_api(token, "get_updates", limit=100, timeout=0):
			if webhook.handle_update(update.to_dict(), self.telegram_settings):
				self.reload()
				if self.telegram_chat_id:
					return {"linked": True, "chat_id": self.telegram_chat_id}

		frappe.msgprint(
			_(
				"No scan found yet. Ask the recipient to scan the QR code and press START, "
				"then try again. Telegram discards unread updates after 24 hours."
			)
		)
		return {"linked": False, "chat_id": None}


@frappe.whitelist()
def generate_telegram_token(is_group_chat=None):
	"""Kept for callers of the old API. Payloads no longer carry a "/" prefix:
	a deep link arrives as `/start <payload>`, which is already a command."""
	return onboarding.build_payload()


@frappe.whitelist()
def get_chat_id_button(telegram_token, telegram_settings):
	"""Backwards-compatible entry point for the old "Get Chat ID" button."""
	doc = onboarding.find_by_payload(telegram_token, telegram_settings)
	if not doc:
		frappe.msgprint(_("No Telegram User Settings record matches that token."))
		return None

	result = doc.check_link()
	return result.get("chat_id")
