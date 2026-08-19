# Copyright (c) 2026, Amr Basha and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class TelegramOutbox(Document):
	def before_insert(self):
		if not self.telegram_settings:
			self.telegram_settings = frappe.db.get_value(
				"Telegram User Settings", self.telegram_user, "telegram_settings"
			)

		# Snapshot the chat id for traceability; delivery re-reads it so a
		# re-linked user still gets a queued message.
		if not self.chat_id:
			self.chat_id = frappe.db.get_value(
				"Telegram User Settings", self.telegram_user, "telegram_chat_id"
			)

		if not self.scheduled_at:
			self.scheduled_at = now_datetime()
