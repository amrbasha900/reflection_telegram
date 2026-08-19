# Copyright (c) 2026, Amr Basha and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

RETRYABLE_STATUSES = ("Failed", "Cancelled")


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

	@frappe.whitelist()
	def retry(self):
		"""Send this again.

		Attempts reset to zero: automatic retries gave up for a reason, and a
		person asking again is a new decision, not a continuation of that one.
		"""
		if self.status not in RETRYABLE_STATUSES:
			frappe.throw(_("Only failed or cancelled messages can be retried."))

		self.db_set(
			{
				"status": "Queued",
				"attempts": 0,
				"error": None,
				"sending_since": None,
				"scheduled_at": now_datetime(),
			},
			update_modified=False,
		)

		if self.broadcast:
			frappe.get_doc("Telegram Broadcast", self.broadcast).refresh_counts()

		frappe.msgprint(_("Queued. It goes out on the next tick."))


@frappe.whitelist()
def retry_messages(names):
	"""Bulk retry from the list view."""
	import json

	if isinstance(names, str):
		names = json.loads(names)

	retried = 0
	for name in names or []:
		doc = frappe.get_doc("Telegram Outbox", name)
		doc.check_permission("write")
		if doc.status in RETRYABLE_STATUSES:
			doc.retry()
			retried += 1

	return retried
