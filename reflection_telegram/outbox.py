"""Rate-limited delivery queue.

Telegram will rate-limit, and eventually restrict, a bot that fires a few
hundred messages at once. So nothing bulk is sent inline: callers enqueue
`Telegram Outbox` rows with a `scheduled_at` stamp spread over time, and a
once-a-minute job drains whatever has come due, pausing between sends.

Pacing happens twice on purpose. Spreading `scheduled_at` at enqueue time caps
the long-run rate no matter how many jobs run, and the in-batch pause keeps a
single minute's worth from leaving as one burst.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, cstr, now_datetime
from frappe.utils.file_lock import LockTimeoutError
from frappe.utils.synchronization import filelock

from reflection_telegram import telegram_client

DEFAULT_RATE = 20
BATCH_LOCK = "reflection_telegram_outbox"
# Backoff between attempts when Telegram did not say how long to wait.
RETRY_BACKOFF_SECONDS = (60, 300, 900)


def get_rate(telegram_settings: str, override: int = 0) -> int:
	rate = cint(override) or cint(
		frappe.db.get_value("Telegram Settings", telegram_settings, "messages_per_minute")
	)
	return max(1, rate or DEFAULT_RATE)


def get_max_attempts(telegram_settings: str) -> int:
	return max(1, cint(frappe.db.get_value("Telegram Settings", telegram_settings, "max_attempts")) or 3)


def enqueue(messages: list[dict], telegram_settings: str = None, broadcast: str = None, rate: int = 0):
	"""Queue messages, spaced so the whole run stays under `rate` per minute.

	Each entry needs `telegram_user` and `message`; `file_url`, `parse_mode`,
	`reference_doctype` and `reference_name` are optional. Returns the created
	Outbox row names.
	"""
	if not messages:
		return []

	telegram_settings = telegram_settings or _infer_settings(messages)
	rate = get_rate(telegram_settings, rate)
	interval = 60.0 / rate

	start = now_datetime()
	created = []

	for index, item in enumerate(messages):
		row = frappe.get_doc(
			{
				"doctype": "Telegram Outbox",
				"telegram_user": item["telegram_user"],
				"telegram_settings": telegram_settings,
				"message": item.get("message"),
				"file_url": item.get("file_url"),
				"parse_mode": item.get("parse_mode"),
				"reference_doctype": item.get("reference_doctype"),
				"reference_name": item.get("reference_name"),
				"broadcast": broadcast,
				"status": "Queued",
				"scheduled_at": add_to_date(start, seconds=index * interval),
			}
		).insert(ignore_permissions=True)
		created.append(row.name)

	return created


def _infer_settings(messages: list[dict]) -> str:
	user = messages[0].get("telegram_user")
	settings = frappe.db.get_value("Telegram User Settings", user, "telegram_settings")
	if not settings:
		frappe.throw(_("Cannot work out which Telegram Settings {0} belongs to").format(user))
	return settings


def process():
	"""Scheduler entry point. Runs every minute; overlapping runs skip quietly."""
	try:
		with filelock(BATCH_LOCK, timeout=0):
			for settings in _settings_with_due_messages():
				try:
					process_settings(settings)
				except Exception:
					frappe.log_error(
						title=f"Telegram outbox failed for {settings}",
						message=frappe.get_traceback(),
					)
	except LockTimeoutError:
		# The previous minute's batch is still sending. Nothing to do.
		return


def _settings_with_due_messages() -> list[str]:
	return frappe.get_all(
		"Telegram Outbox",
		filters={"status": "Queued", "scheduled_at": ["<=", now_datetime()]},
		distinct=True,
		pluck="telegram_settings",
	)


def process_settings(telegram_settings: str):
	rate = get_rate(telegram_settings)
	rows = frappe.get_all(
		"Telegram Outbox",
		filters={
			"status": "Queued",
			"telegram_settings": telegram_settings,
			"scheduled_at": ["<=", now_datetime()],
		},
		fields=["name", "telegram_user", "message", "file_url", "parse_mode", "broadcast", "attempts"],
		order_by="scheduled_at asc",
		limit=rate,
	)
	if not rows:
		return

	items = []
	for row in rows:
		chat_id = frappe.db.get_value("Telegram User Settings", row.telegram_user, "telegram_chat_id")
		if not chat_id:
			_fail(row, _("{0} has no chat id yet -- the QR code has not been scanned").format(row.telegram_user))
			continue

		document = filename = None
		if row.file_url:
			try:
				filename, document = telegram_client.resolve_file(row.file_url)
			except Exception as exc:
				_fail(row, cstr(exc))
				continue

		items.append(
			{
				"row": row,
				"chat_id": chat_id,
				"text": row.message,
				"document": document,
				"filename": filename,
				"parse_mode": row.parse_mode or None,
			}
		)

	if not items:
		frappe.db.commit()
		return

	frappe.db.set_value(
		"Telegram Outbox",
		{"name": ["in", [item["row"].name for item in items]]},
		"status",
		"Sending",
		update_modified=False,
	)
	frappe.db.commit()

	token = telegram_client.get_token(telegram_settings)
	max_attempts = get_max_attempts(telegram_settings)

	def record(item, error):
		if error is None:
			_succeed(item["row"])
		else:
			_handle_error(item["row"], error, max_attempts)
		frappe.db.commit()

	telegram_client.send_many(token, items, on_result=record, pause=60.0 / rate)

	_refresh_broadcasts({row.broadcast for row in rows if row.broadcast})


def _succeed(row):
	frappe.db.set_value(
		"Telegram Outbox",
		row.name,
		{"status": "Sent", "sent_at": now_datetime(), "attempts": cint(row.attempts) + 1, "error": None},
		update_modified=False,
	)


def _fail(row, message):
	frappe.db.set_value(
		"Telegram Outbox",
		row.name,
		{"status": "Failed", "error": cstr(message)[:500], "attempts": cint(row.attempts) + 1},
		update_modified=False,
	)


def _handle_error(row, error, max_attempts):
	"""Permanent failures stop here; transient ones go back in the queue.

	When Telegram answers 429 it also says how long to wait, and honouring that
	exactly is what keeps a rate limit from escalating into a restriction.
	"""
	attempts = cint(row.attempts) + 1

	if isinstance(error, telegram_client.PermanentError) or attempts >= max_attempts:
		_fail(row, error)
		return

	wait = getattr(error, "retry_after", 0) or RETRY_BACKOFF_SECONDS[
		min(attempts - 1, len(RETRY_BACKOFF_SECONDS) - 1)
	]

	frappe.db.set_value(
		"Telegram Outbox",
		row.name,
		{
			"status": "Queued",
			"attempts": attempts,
			"error": cstr(error)[:500],
			"scheduled_at": add_to_date(now_datetime(), seconds=wait),
		},
		update_modified=False,
	)


def _refresh_broadcasts(names):
	for name in names:
		if name:
			frappe.get_doc("Telegram Broadcast", name).refresh_counts()
