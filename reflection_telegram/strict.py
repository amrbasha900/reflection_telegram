"""Strict delivery: one private PDF to one linked person, never sent twice by guesswork.

Used by other apps' server code (Report Distribution Center in Al-Dallala), in the
same process and as the user they have already authorised. Nothing here is
whitelisted: an HTTP caller cannot reach it.

A strict row lives in `Telegram Outbox` (delivery_policy = Strict) and differs from
the legacy queue in what happens when the outcome is not known:

* **Claimed one at a time.** A guarded ``UPDATE ... WHERE status = 'Queued'`` gives
  one worker the row (``claim_token``); its result is committed before the next.
* **Attempt marker.** ``attempt_started_at`` is committed *before* Telegram is
  called. A worker that dies before it leaves the row provably unsent (requeued);
  one that dies after it leaves the outcome unknown (Needs Review).
* **No automatic resend after an unknown outcome.** Telegram's answer decides:
  a message id = accepted (Sent); a 429 = refused, wait and queue again; any other
  answer that refuses the request = Failed. A timeout, a lost connection or any
  unexpected error = Needs Review, which only a person settles (`resolve_review`),
  with a reason, and a resend only with the duplicate risk acknowledged.
* **The file is proven at send time.** It must be a private File attached to the
  row's reference document, with the SHA-256 recorded when it was queued.
* **Private chats only.** The recipient's Telegram User Settings must be a linked
  one-to-one chat (not a group: ``is_group_chat`` off and a positive chat id) that
  has not blocked the bot.

Telegram's message id means Telegram accepted the message. The Bot API gives no
delivery or read receipt, so none is recorded.

Apps that queue strict messages may also authorise each send at the last moment::

    telegram_strict_dispatch_guards = ["my_app.module.authorize_telegram_send"]

called with ``outbox``, ``reference_doctype``, ``reference_name`` and ``external_reference`` immediately
before the attempt marker, on every attempt (first send, a resend after review, a retry). A guard that raises
stops the send: the row is Failed (``REVOKED_<code>``), provably unsent, never retried automatically.

Apps that want to hear about outcomes register a hook::

    telegram_strict_delivery_listeners = ["my_app.module.on_telegram_outcome"]

called after commit with ``outbox``, ``idempotency_key``, ``external_reference``
and ``status``. Only installed apps' hooks are called; nothing is imported from
request data.
"""

import hashlib
import json
import time
import uuid

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, cstr, now_datetime

from reflection_telegram import message_log, telegram_client

OUTBOX = "Telegram Outbox"
STRICT = "Strict"
#: The Bot API refuses documents over 50 MB.
MAX_DOCUMENT_BYTES = 50 * 1024**2
MAX_KEY_LENGTH = 140
LISTENER_HOOK = "telegram_strict_delivery_listeners"
GUARD_HOOK = "telegram_strict_dispatch_guards"
#: A strict row left "Sending" this long belongs to a worker that died.
STUCK_AFTER_MINUTES = 15
#: Wait after a 429 that did not say how long.
DEFAULT_RETRY_AFTER = 60
REVIEW_ACTIONS = {
	"confirm_sent": ("Sent", "Confirmed Sent"),
	"confirm_unsent": ("Failed", "Confirmed Unsent"),
	"resend": ("Queued", "Resend Requested"),
}
STATUS_FIELDS = [
	"name", "idempotency_key", "external_reference", "status", "error_code", "error", "attempts",
	"message_ids", "sent_at", "chat_id", "scheduled_at", "review_resolution", "reviewed_by",
	"reviewed_at", "review_reason",
]  # fmt: skip


class StrictDeliveryError(frappe.ValidationError):
	"""Refused before anything was sent. ``code`` is stable for callers."""

	def __init__(self, code: str, message: str):
		super().__init__(message)
		self.code = code


def _update(query: str, values) -> int:
	frappe.db.sql(query, values)
	return frappe.db.sql("SELECT ROW_COUNT()")[0][0]


def sha256(data: bytes) -> str:
	return hashlib.sha256(data).hexdigest()


# =====================================================================
# Checks
# =====================================================================


def check_recipient(telegram_user: str) -> tuple[str, str]:
	"""(telegram_settings, chat_id) of a linked private chat, or StrictDeliveryError."""
	row = frappe.db.get_value(
		"Telegram User Settings",
		telegram_user,
		["telegram_settings", "telegram_chat_id", "chat_status", "is_group_chat"],
		as_dict=True,
	)
	if not row:
		raise StrictDeliveryError("RECIPIENT_NOT_FOUND", _("Telegram User Settings {0} not found").format(telegram_user))
	if cint(row.is_group_chat):
		raise StrictDeliveryError("NOT_PRIVATE_CHAT", _("{0} is linked to a group, not a private chat").format(telegram_user))
	chat_id = cstr(row.telegram_chat_id).strip()
	if not chat_id:
		raise StrictDeliveryError("NOT_LINKED", _("{0} has not scanned the QR code yet").format(telegram_user))
	# Private chats have positive ids; groups, supergroups and channels negative ones.
	if not chat_id.isdigit() or int(chat_id) <= 0:
		raise StrictDeliveryError("NOT_PRIVATE_CHAT", _("{0} is not a private chat").format(telegram_user))
	if row.chat_status in ("Blocked", "Left"):
		raise StrictDeliveryError("BLOCKED", _("{0} has blocked the bot").format(telegram_user))
	if not row.telegram_settings:
		raise StrictDeliveryError("NO_BOT", _("{0} has no Telegram Settings").format(telegram_user))
	return row.telegram_settings, chat_id


def verify_file(file: str, expected_sha256: str, reference_doctype: str, reference_name: str) -> tuple[str, bytes]:
	"""(file name, content) of the private File attached to the reference document, proven by its SHA-256."""
	row = frappe.db.get_value(
		"File", file, ["name", "file_name", "file_url", "is_private", "attached_to_doctype", "attached_to_name"], as_dict=True
	)
	if not row:
		raise StrictDeliveryError("FILE_MISSING", _("File {0} does not exist").format(file))
	if not row.is_private or not cstr(row.file_url).startswith("/private/files/"):
		raise StrictDeliveryError("FILE_NOT_PRIVATE", _("File {0} is not private").format(file))
	if (row.attached_to_doctype, row.attached_to_name) != (reference_doctype, reference_name):
		raise StrictDeliveryError("FILE_NOT_OWNED", _("File {0} is not attached to {1} {2}").format(file, reference_doctype, reference_name))
	try:
		content = frappe.get_doc("File", row.name).get_content()
	except Exception:
		raise StrictDeliveryError("FILE_UNREADABLE", _("File {0} cannot be read").format(file)) from None
	if isinstance(content, str):
		content = content.encode()
	if not content or len(content) > MAX_DOCUMENT_BYTES:
		raise StrictDeliveryError("FILE_SIZE", _("File {0} is empty or larger than Telegram accepts").format(file))
	if not expected_sha256 or sha256(content) != expected_sha256:
		raise StrictDeliveryError("CHECKSUM_MISMATCH", _("File {0} does not match its recorded checksum").format(file))
	return row.file_name or f"{row.name}.pdf", content


# =====================================================================
# Queueing
# =====================================================================


def _fingerprint(row) -> tuple:
	return (
		cstr(row.get("telegram_user")), cstr(row.get("file")), cstr(row.get("file_sha256")),
		cstr(row.get("reference_doctype")), cstr(row.get("reference_name")), cstr(row.get("external_reference")),
	)  # fmt: skip


def enqueue(
	idempotency_key: str,
	telegram_user: str,
	file: str,
	file_sha256: str,
	reference_doctype: str,
	reference_name: str,
	external_reference: str | None = None,
	caption: str | None = None,
	file_name: str | None = None,
) -> dict:
	"""Queue one strict document message. Idempotent per ``idempotency_key``.

	The same key with the same message returns the existing row (``created`` false);
	with a different message it is refused (IDEMPOTENCY_CONFLICT). Recipient and file
	are checked now and again at send time.
	"""
	key = cstr(idempotency_key).strip()
	if not key or len(key) > MAX_KEY_LENGTH:
		raise StrictDeliveryError("INVALID_KEY", _("An idempotency key of at most {0} characters is required").format(MAX_KEY_LENGTH))
	caption = cstr(caption) or None
	if caption and len(caption) > telegram_client.MAX_CAPTION_LENGTH:
		# One API call per message: a longer text would be a second, separate send.
		raise StrictDeliveryError("CAPTION_TOO_LONG", _("The caption is longer than Telegram allows"))
	wanted = frappe._dict(
		telegram_user=telegram_user, file=file, file_sha256=file_sha256, reference_doctype=reference_doctype,
		reference_name=reference_name, external_reference=external_reference,
	)  # fmt: skip

	existing = _by_key(key)
	if existing:
		return _replay(existing, wanted)

	telegram_settings, chat_id = check_recipient(telegram_user)
	stored_name, _content = verify_file(file, file_sha256, reference_doctype, reference_name)
	try:
		frappe.db.savepoint("rt_strict_enqueue")
		doc = frappe.get_doc(
			{
				"doctype": OUTBOX,
				"delivery_policy": STRICT,
				"idempotency_key": key,
				"external_reference": external_reference,
				"telegram_user": telegram_user,
				"telegram_settings": telegram_settings,
				"chat_id": chat_id,
				"message": caption,
				"file": file,
				"file_sha256": file_sha256,
				"file_name": file_name or stored_name,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"status": "Queued",
				"scheduled_at": now_datetime(),
			}
		).insert(ignore_permissions=True)
	except frappe.UniqueValidationError:
		# Two callers raced on the same key: the database's unique index decided.
		frappe.db.rollback(save_point="rt_strict_enqueue")
		existing = _by_key(key)
		if not existing:
			raise
		return _replay(existing, wanted)
	return {"outbox": doc.name, "created": True, "status": doc.status}


def _by_key(key: str):
	return frappe.db.get_value(
		OUTBOX,
		{"idempotency_key": key},
		["name", "status", "delivery_policy", "telegram_user", "file", "file_sha256", "reference_doctype", "reference_name", "external_reference"],
		as_dict=True,
	)


def _replay(existing, wanted) -> dict:
	if existing.delivery_policy != STRICT or _fingerprint(existing) != _fingerprint(wanted):
		raise StrictDeliveryError("IDEMPOTENCY_CONFLICT", _("Idempotency key already used for a different message"))
	return {"outbox": existing.name, "created": False, "status": existing.status}


# =====================================================================
# Worker
# =====================================================================


def process_settings(telegram_settings: str, budget: int | None = None, pause: float | None = None) -> int:
	"""Send up to ``budget`` due strict rows of one bot, one guarded attempt each.

	Called by ``outbox.process`` under its lock, after the legacy rows, with what is
	left of the bot's per-minute rate. Returns the number of rows attempted.
	"""
	from reflection_telegram import outbox

	rate = outbox.get_rate(telegram_settings)
	budget = rate if budget is None else cint(budget)
	if budget <= 0:
		return 0
	pause = 60.0 / rate if pause is None else pause
	names = frappe.get_all(
		OUTBOX,
		filters={
			"status": "Queued",
			"delivery_policy": STRICT,
			"telegram_settings": telegram_settings,
			"scheduled_at": ["<=", now_datetime()],
		},
		pluck="name",
		order_by="scheduled_at asc, creation asc",
		limit=budget,
	)
	attempted = 0
	for name in names:
		token = _claim(name)
		if not token:
			continue  # cancelled or taken by another worker meanwhile
		if attempted and pause:
			time.sleep(pause)
		attempted += 1
		_attempt(name, token)
	return attempted


def _claim(name: str) -> str | None:
	token = uuid.uuid4().hex
	claimed = _update(
		"UPDATE `tabTelegram Outbox` SET status = 'Sending', claim_token = %s, sending_since = %s, "
		"attempt_started_at = NULL WHERE name = %s AND status = 'Queued' AND delivery_policy = 'Strict'",
		(token, now_datetime(), name),
	)
	frappe.db.commit()
	return token if claimed else None


def _finish(name: str, token: str, status: str, code: str | None, error: str | None, **fields) -> int:
	values = {
		"status": status,
		"error_code": code,
		"error": cstr(error)[:500] or None,
		"claim_token": None,
		"sending_since": None,
		**fields,
	}
	assignments = ", ".join(f"`{key}` = %({key})s" for key in values)
	return _update(
		f"UPDATE `tabTelegram Outbox` SET {assignments} WHERE name = %(name)s AND status = 'Sending' AND claim_token = %(token)s",
		{**values, "name": name, "token": token},
	)


def _attempt(name: str, token: str) -> str:
	row = frappe.db.get_value(
		OUTBOX,
		name,
		["name", "telegram_user", "telegram_settings", "message", "file", "file_sha256", "file_name",
		 "reference_doctype", "reference_name", "attempts", "external_reference", "idempotency_key"],
		as_dict=True,
	)  # fmt: skip
	try:
		settings, chat_id = check_recipient(row.telegram_user)
		if settings != row.telegram_settings:
			raise StrictDeliveryError("BOT_CHANGED", _("The recipient is now linked to another bot"))
		filename, content = verify_file(row.file, row.file_sha256, row.reference_doctype, row.reference_name)
		bot_token = telegram_client.get_token(settings)
		_run_guards(row)
	except (StrictDeliveryError, telegram_client.TelegramError) as e:
		# Nothing left the server: a final, recorded refusal.
		_finish(name, token, "Failed", getattr(e, "code", "NOT_SENDABLE"), str(e))
		frappe.db.commit()
		_notify(name)
		return "Failed"

	# The attempt marker, committed before Telegram is called: from here on only
	# Telegram's answer, or a person, decides the outcome.
	if not _update(
		"UPDATE `tabTelegram Outbox` SET attempt_started_at = %s, attempts = attempts + 1, chat_id = %s "
		"WHERE name = %s AND status = 'Sending' AND claim_token = %s",
		(now_datetime(), chat_id, name, token),
	):
		frappe.db.commit()
		return "Lost"
	frappe.db.commit()

	error = None
	message_ids = []
	try:
		message_ids = telegram_client.send(
			bot_token, chat_id, row.message, document=content, filename=row.file_name or filename
		)
	except telegram_client.TelegramError as e:
		error = e
	except Exception as e:
		frappe.log_error(title="Telegram strict send failed", message=frappe.get_traceback())
		error = telegram_client.TransientError(cstr(e))

	if error is None and message_ids:
		status = "Sent"
		_finish(
			name, token, "Sent", None, None,
			sent_at=now_datetime(), message_ids=json.dumps([cint(i) for i in message_ids]),
		)  # fmt: skip
	elif error is None:
		status = "Needs Review"  # an answer without a message id proves nothing
		_finish(name, token, status, "UNCONFIRMED", _("Telegram returned no message id"))
	elif error.kind == "rate_limited":
		status = "Queued"
		wait = cint(getattr(error, "retry_after", 0)) or DEFAULT_RETRY_AFTER
		_finish(
			name, token, "Queued", "RATE_LIMITED", str(error),
			scheduled_at=add_to_date(now_datetime(), seconds=wait), attempt_started_at=None,
			attempts=max(cint(row.attempts), 0),  # refused before sending: the attempt does not count
		)  # fmt: skip
	elif error.kind == "rejected":
		status = "Failed"
		_finish(name, token, status, "TELEGRAM_REJECTED", str(error))
	else:
		status = "Needs Review"
		_finish(name, token, status, "OUTCOME_UNKNOWN", str(error))

	if status != "Queued":
		message_log.record_outgoing(
			row.telegram_settings,
			telegram_user=row.telegram_user,
			chat_id=chat_id,
			message=row.message,
			message_ids=message_ids,
			error=error if status != "Sent" else None,
			attachment=row.file_name or filename,
			reference_doctype=row.reference_doctype,
			reference_name=row.reference_name,
			outbox=name,
		)
	frappe.db.commit()
	_notify(name)
	return status


def _run_guards(row) -> None:
	"""The queuing app's last word before the attempt marker; any refusal (or error deciding) stops the send."""
	import re

	for path in frappe.get_hooks(GUARD_HOOK) or []:
		try:
			frappe.get_attr(path)(
				outbox=row.name, reference_doctype=row.reference_doctype, reference_name=row.reference_name,
				external_reference=row.external_reference,
			)  # fmt: skip
		except Exception as e:
			code = re.sub(r"[^A-Z0-9_]", "", str(getattr(e, "code", "") or "NOT_AUTHORISED").upper())[:60] or "NOT_AUTHORISED"
			raise StrictDeliveryError(f"REVOKED_{code}", (str(e) or _("The send is no longer authorised"))[:450]) from None


def reclaim_stuck() -> dict:
	"""Rows whose worker died: requeued when provably unsent, otherwise Needs Review."""
	cutoff = add_to_date(now_datetime(), minutes=-STUCK_AFTER_MINUTES)
	report = {"requeued": 0, "needs_review": 0}
	for row in frappe.get_all(
		OUTBOX,
		filters={"status": "Sending", "delivery_policy": STRICT, "sending_since": ["<", cutoff]},
		fields=["name", "claim_token", "attempt_started_at"],
	):
		if not row.attempt_started_at:
			done = _update(
				"UPDATE `tabTelegram Outbox` SET status = 'Queued', claim_token = NULL, sending_since = NULL "
				"WHERE name = %s AND status = 'Sending' AND attempt_started_at IS NULL",
				row.name,
			)
			report["requeued"] += done
		else:
			done = _update(
				"UPDATE `tabTelegram Outbox` SET status = 'Needs Review', error_code = 'OUTCOME_UNKNOWN', "
				"error = %s, claim_token = NULL, sending_since = NULL WHERE name = %s AND status = 'Sending'",
				(_("The worker stopped after the message was handed to Telegram; the outcome is unknown."), row.name),
			)
			report["needs_review"] += done
		frappe.db.commit()
		if done:
			_notify(row.name)
	return report


# =====================================================================
# Caller operations
# =====================================================================


def status(keys: list[str]) -> list[dict]:
	"""The current state of strict rows, by idempotency key."""
	if not keys:
		return []
	rows = frappe.get_all(
		OUTBOX, filters={"idempotency_key": ["in", list(keys)], "delivery_policy": STRICT}, fields=STATUS_FIELDS
	)
	for row in rows:
		row["message_ids"] = json.loads(row.message_ids) if row.message_ids else []
	return rows


def cancel(keys: list[str], reason: str | None = None) -> dict:
	"""Cancel strict rows not handed to a worker yet. Returns {key: status after}."""
	out = {}
	for key in keys or []:
		_update(
			"UPDATE `tabTelegram Outbox` SET status = 'Cancelled', error_code = 'CANCELLED', error = %s "
			"WHERE idempotency_key = %s AND delivery_policy = 'Strict' AND status = 'Queued'",
			(cstr(reason)[:500] or _("Cancelled by the sender"), key),
		)
		out[key] = frappe.db.get_value(OUTBOX, {"idempotency_key": key}, "status")
	frappe.db.commit()
	return out


def resolve_review(
	idempotency_key: str, action: str, reason: str, confirm_duplicate_risk: bool = False, user: str | None = None
) -> dict:
	"""Settle a Needs Review row: confirm_sent, confirm_unsent, or resend (duplicate risk acknowledged)."""
	if action not in REVIEW_ACTIONS:
		raise StrictDeliveryError("INVALID_ACTION", _("Unknown review action {0}").format(action))
	reason = cstr(reason).strip()
	if len(reason) < 10:
		raise StrictDeliveryError("REASON_REQUIRED", _("A reason of at least 10 characters is required"))
	if action == "resend" and not cint(confirm_duplicate_risk):
		raise StrictDeliveryError(
			"CONFIRMATION_REQUIRED", _("A resend may deliver the message twice; confirm the duplicate risk")
		)
	name = frappe.db.get_value(OUTBOX, {"idempotency_key": idempotency_key, "delivery_policy": STRICT}, "name")
	if not name:
		raise StrictDeliveryError("NOT_FOUND", _("No strict message with that key"))
	target, resolution = REVIEW_ACTIONS[action]
	user = user or frappe.session.user
	extra = ", scheduled_at = %(now)s, attempt_started_at = NULL" if target == "Queued" else ""
	extra += ", sent_at = %(now)s" if target == "Sent" else ""
	done = _update(
		"UPDATE `tabTelegram Outbox` SET status = %(target)s, review_resolution = %(resolution)s, reviewed_by = %(user)s, "
		f"reviewed_at = %(now)s, review_reason = %(reason)s, error_code = %(code)s{extra} "
		"WHERE name = %(name)s AND status = 'Needs Review'",
		{
			"target": target, "resolution": resolution, "user": user, "now": now_datetime(), "reason": reason[:1000],
			"code": "CONFIRMED_UNSENT" if action == "confirm_unsent" else None, "name": name,
		},
	)  # fmt: skip
	if not done:
		current = frappe.db.get_value(OUTBOX, name, "status")
		raise StrictDeliveryError("REVIEW_REFUSED", _("Only messages in Needs Review can be resolved (this one is {0})").format(current))
	frappe.get_doc(OUTBOX, name).add_comment(
		"Comment", _("Review by {0}: {1} — {2}").format(user, resolution, reason[:500])
	)
	frappe.db.commit()
	_notify(name)
	return {"outbox": name, "status": target, "review_resolution": resolution}


def _notify(name: str) -> None:
	"""Tell registered apps about a strict row's new state (after commit; never raises)."""
	row = frappe.db.get_value(OUTBOX, name, ["name", "idempotency_key", "external_reference", "status"], as_dict=True)
	if not row:
		return
	for path in frappe.get_hooks(LISTENER_HOOK) or []:
		try:
			frappe.get_attr(path)(
				outbox=row.name, idempotency_key=row.idempotency_key, external_reference=row.external_reference, status=row.status
			)
		except Exception:
			frappe.log_error(title=f"Telegram strict listener {path} failed", message=frappe.get_traceback())
