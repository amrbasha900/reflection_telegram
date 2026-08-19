# Copyright (c) 2026, Amr Basha and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, now_datetime


class TelegramBroadcast(Document):
	def refresh_counts(self):
		"""Recount from the Outbox rows rather than incrementing as we go.

		Counters that are only ever incremented drift whenever a send is retried
		or a row is cancelled by hand; recounting cannot drift.
		"""
		counts = {
			row.status: row.n
			for row in frappe.get_all(
				"Telegram Outbox",
				filters={"broadcast": self.name},
				fields=["status", "count(name) as n"],
				group_by="status",
			)
		}

		total = sum(counts.values())
		sent = counts.get("Sent", 0)
		failed = counts.get("Failed", 0)
		pending = counts.get("Queued", 0) + counts.get("Sending", 0)

		if pending:
			status = "In Progress"
		elif counts.get("Cancelled") and not sent:
			status = "Cancelled"
		elif failed:
			status = "Completed With Errors"
		else:
			status = "Completed"

		values = {"total": total, "sent": sent, "failed": failed, "status": status}

		if not self.started_at and (sent or failed):
			values["started_at"] = now_datetime()
		if not pending and not self.finished_at:
			values["finished_at"] = now_datetime()

		self.db_set(values, update_modified=False)

	@frappe.whitelist()
	def retry_failed(self):
		"""Requeue everything that failed, re-paced.

		Re-spacing matters: dumping 60 recovered messages in at once is how a
		recovery turns into the rate limit that caused the failures.
		"""
		from reflection_telegram.outbox import get_rate

		names = frappe.get_all(
			"Telegram Outbox",
			filters={"broadcast": self.name, "status": "Failed"},
			order_by="creation asc",
			pluck="name",
		)
		if not names:
			frappe.msgprint(_("Nothing failed in this broadcast."))
			return 0

		interval = 60.0 / get_rate(self.telegram_settings, self.messages_per_minute)
		start = now_datetime()

		for index, name in enumerate(names):
			frappe.db.set_value(
				"Telegram Outbox",
				name,
				{
					"status": "Queued",
					"attempts": 0,
					"error": None,
					"sending_since": None,
					"scheduled_at": add_to_date(start, seconds=index * interval),
				},
				update_modified=False,
			)

		self.db_set({"finished_at": None}, update_modified=False)
		self.refresh_counts()
		frappe.msgprint(_("Requeued {0} failed messages.").format(len(names)))
		return len(names)

	@frappe.whitelist()
	def cancel_pending(self):
		"""Stop anything that has not gone out yet. Sent messages are not recallable."""
		cancelled = frappe.db.set_value(
			"Telegram Outbox",
			{"broadcast": self.name, "status": "Queued"},
			"status",
			"Cancelled",
			update_modified=False,
		)
		self.refresh_counts()
		frappe.msgprint(_("Cancelled the messages that had not been sent yet."))
		return cancelled
