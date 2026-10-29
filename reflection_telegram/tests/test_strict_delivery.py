"""Strict delivery (Report Distribution Center) and the public API's permission checks.

Telegram is never called: telegram_client.send / send_many are replaced. The code
under test commits as it goes, so commits are turned off and every test rolls back.
"""

import hashlib
import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from reflection_telegram import api, outbox, strict, telegram_client

BOT = "_RT Strict Bot"


def _pdf(marker: str) -> bytes:
	"""A real one-page PDF (Frappe parses uploaded PDFs), unique per marker."""
	import io

	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=200, height=200)
	writer.add_metadata({"/Title": marker})
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()


PDF = _pdf("strict delivery test")


class StrictTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.enterContext(patch.object(frappe.db, "commit", lambda *a, **k: None))
		self.sends = []
		self.answer = None  # None = success; an exception instance = raise it
		self.enterContext(patch.object(telegram_client, "send", side_effect=self._send))
		self.enterContext(patch.object(strict.time, "sleep", lambda *_: None))
		if not frappe.db.exists("Telegram Settings", BOT):
			frappe.get_doc(
				{"doctype": "Telegram Settings", "settings_name": BOT, "telegram_token": "123456:TESTTOKEN", "bot_name": "rt_test_bot", "messages_per_minute": 20}
			).insert(ignore_permissions=True)
		self.customer = self.make_customer("_RT Strict Customer")
		self.recipient = self.link(self.customer, chat_id="555001")
		self.note = frappe.get_doc({"doctype": "Note", "title": f"_RT {frappe.generate_hash(length=6)}", "public": 0}).insert(ignore_permissions=True)
		self.file = self.attach(self.note, PDF)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _send(self, token, chat_id, text, document=None, filename=None, parse_mode=None):
		self.sends.append({"chat_id": chat_id, "text": text, "document": document, "filename": filename})
		if self.answer:
			raise self.answer
		return [9000 + len(self.sends)]

	def make_customer(self, name):
		if not frappe.db.exists("Customer", name):
			frappe.get_doc({"doctype": "Customer", "customer_name": name, "customer_group": "All Customer Groups", "territory": "All Territories"}).insert(ignore_permissions=True)
		return name

	def link(self, customer, chat_id, is_group_chat=0):
		name = frappe.db.get_value("Telegram User Settings", {"party": "Customer", "telegram_user": customer, "telegram_settings": BOT})
		if not name:
			name = frappe.get_doc(
				{"doctype": "Telegram User Settings", "party": "Customer", "telegram_user": customer, "telegram_settings": BOT, "is_group_chat": is_group_chat}
			).insert(ignore_permissions=True).name
		frappe.db.set_value("Telegram User Settings", name, {"telegram_chat_id": chat_id, "chat_status": "Active", "is_group_chat": is_group_chat})
		return name

	def attach(self, doc, content, private=1):
		return frappe.get_doc(
			{"doctype": "File", "file_name": f"rt-{frappe.generate_hash(length=8)}.pdf", "is_private": private, "content": content,
			 "attached_to_doctype": doc.doctype, "attached_to_name": doc.name}
		).insert(ignore_permissions=True).name  # fmt: skip

	def queue(self, key=None, **kw):
		args = dict(
			idempotency_key=key or f"rdc:test:{frappe.generate_hash(length=8)}", telegram_user=self.recipient, file=self.file,
			file_sha256=hashlib.sha256(PDF).hexdigest(), reference_doctype="Note", reference_name=self.note.name,
			external_reference="RDC-TEST-1", caption="كشف حساب",
		)  # fmt: skip
		args.update(kw)
		return strict.enqueue(**args)

	def row(self, name):
		return frappe.db.get_value("Telegram Outbox", name, ["*"], as_dict=True)

	def run_worker(self):
		return strict.process_settings(BOT, budget=20, pause=0)


class TestStrictQueue(StrictTestCase):
	def test_a_valid_message_is_sent_once_with_its_message_id(self):
		result = self.queue()
		self.assertTrue(result["created"])
		self.assertEqual(self.run_worker(), 1)
		row = self.row(result["outbox"])
		self.assertEqual(row.status, "Sent")
		self.assertEqual(json.loads(row.message_ids), [9001])
		self.assertEqual(len(self.sends), 1)
		self.assertEqual(self.sends[0]["document"], PDF)
		self.assertEqual(self.sends[0]["chat_id"], "555001")
		log = frappe.db.get_value("Telegram Message Log", {"outbox": result["outbox"]}, ["status", "message_id"], as_dict=True)
		self.assertEqual((log.status, log.message_id), ("Sent", 9001))
		self.assertEqual(self.run_worker(), 0, "a sent message is never picked again")

	def test_the_same_key_is_idempotent_and_a_different_message_conflicts(self):
		first = self.queue("rdc:test:same")
		again = self.queue("rdc:test:same")
		self.assertEqual((again["outbox"], again["created"]), (first["outbox"], False))
		other = self.attach(self.note, _pdf("other"))
		with self.assertRaises(strict.StrictDeliveryError) as e:
			self.queue("rdc:test:same", file=other, file_sha256=hashlib.sha256(_pdf("other")).hexdigest())
		self.assertEqual(e.exception.code, "IDEMPOTENCY_CONFLICT")
		self.assertEqual(frappe.db.count("Telegram Outbox", {"idempotency_key": "rdc:test:same"}), 1)

	def test_groups_unlinked_and_blocked_chats_are_refused(self):
		group = self.link(self.make_customer("_RT Strict Group"), chat_id="-100123", is_group_chat=1)
		with self.assertRaises(strict.StrictDeliveryError) as e:
			self.queue(telegram_user=group)
		self.assertEqual(e.exception.code, "NOT_PRIVATE_CHAT")
		negative = self.link(self.make_customer("_RT Strict Negative"), chat_id="-42")
		with self.assertRaises(strict.StrictDeliveryError) as e:
			self.queue(telegram_user=negative)
		self.assertEqual(e.exception.code, "NOT_PRIVATE_CHAT")
		frappe.db.set_value("Telegram User Settings", self.recipient, "chat_status", "Blocked")
		with self.assertRaises(strict.StrictDeliveryError) as e:
			self.queue()
		self.assertEqual(e.exception.code, "BLOCKED")

	def test_a_file_of_another_document_a_public_file_or_changed_bytes_is_never_sent(self):
		other_note = frappe.get_doc({"doctype": "Note", "title": "_RT other", "public": 0}).insert(ignore_permissions=True)
		foreign = self.attach(other_note, _pdf("foreign"))
		with self.assertRaises(strict.StrictDeliveryError) as e:
			self.queue(file=foreign, file_sha256=hashlib.sha256(_pdf("foreign")).hexdigest())
		self.assertEqual(e.exception.code, "FILE_NOT_OWNED")
		public = self.attach(self.note, _pdf("public"), private=0)
		with self.assertRaises(strict.StrictDeliveryError) as e:
			self.queue(file=public, file_sha256=hashlib.sha256(_pdf("public")).hexdigest())
		self.assertEqual(e.exception.code, "FILE_NOT_PRIVATE")
		with self.assertRaises(strict.StrictDeliveryError) as e:
			self.queue(file_sha256="0" * 64)
		self.assertEqual(e.exception.code, "CHECKSUM_MISMATCH")
		# Queued correctly, then the file changes before the send: refused, nothing sent.
		queued = self.queue()
		frappe.db.set_value("Telegram Outbox", queued["outbox"], "file_sha256", "f" * 64)
		self.run_worker()
		row = self.row(queued["outbox"])
		self.assertEqual((row.status, row.error_code), ("Failed", "CHECKSUM_MISMATCH"))
		self.assertEqual(self.sends, [])

	def test_an_unknown_outcome_goes_to_review_and_is_never_resent_automatically(self):
		queued = self.queue()
		self.answer = telegram_client.TransientError("Timed out")  # kind unknown
		self.run_worker()
		row = self.row(queued["outbox"])
		self.assertEqual((row.status, row.error_code), ("Needs Review", "OUTCOME_UNKNOWN"))
		self.answer = None
		self.run_worker()
		outbox.reclaim_stuck()
		strict.reclaim_stuck()
		self.assertEqual(self.row(queued["outbox"]).status, "Needs Review")
		self.assertEqual(len(self.sends), 1, "one real attempt only")

	def test_a_real_timeout_from_the_library_is_uncertain_and_a_bad_request_is_final(self):
		import telegram

		self.assertEqual(telegram_client.classify(telegram.error.TimedOut()).kind, "unknown")
		self.assertEqual(telegram_client.classify(telegram.error.BadRequest("chat not found")).kind, "rejected")
		self.assertIsInstance(telegram_client.classify(telegram.error.BadRequest("x")), telegram_client.PermanentError)
		self.assertEqual(telegram_client.classify(telegram.error.RetryAfter(30)).kind, "rate_limited")
		queued = self.queue()
		self.answer = telegram_client.classify(telegram.error.BadRequest("chat not found"))
		self.run_worker()
		self.assertEqual(self.row(queued["outbox"]).status, "Failed")

	def test_rate_limit_waits_and_does_not_spend_the_attempt(self):
		queued = self.queue()
		self.answer = telegram_client.TransientError("Flood", retry_after=30, kind="rate_limited")
		self.run_worker()
		row = self.row(queued["outbox"])
		self.assertEqual((row.status, row.error_code, row.attempts), ("Queued", "RATE_LIMITED", 0))
		self.assertGreater(row.scheduled_at, now_datetime())
		self.answer = None
		self.assertEqual(self.run_worker(), 0, "not before retry_after")
		frappe.db.set_value("Telegram Outbox", queued["outbox"], "scheduled_at", now_datetime())
		self.run_worker()
		self.assertEqual(self.row(queued["outbox"]).status, "Sent")

	def test_review_actions_are_audited_and_resend_needs_the_duplicate_risk(self):
		queued = self.queue("rdc:test:review")
		self.answer = telegram_client.TransientError("connection reset")
		self.run_worker()
		self.answer = None
		with self.assertRaises(strict.StrictDeliveryError) as e:
			strict.resolve_review("rdc:test:review", "resend", "customer did not get it")
		self.assertEqual(e.exception.code, "CONFIRMATION_REQUIRED")
		with self.assertRaises(strict.StrictDeliveryError):
			strict.resolve_review("rdc:test:review", "confirm_sent", "short")
		strict.resolve_review("rdc:test:review", "resend", "customer did not get it", confirm_duplicate_risk=1)
		row = self.row(queued["outbox"])
		self.assertEqual((row.status, row.review_resolution, row.reviewed_by), ("Queued", "Resend Requested", "Administrator"))
		self.run_worker()
		self.assertEqual(self.row(queued["outbox"]).status, "Sent")
		self.assertTrue(frappe.db.exists("Comment", {"reference_doctype": "Telegram Outbox", "reference_name": queued["outbox"]}))
		with self.assertRaises(strict.StrictDeliveryError) as e:
			strict.resolve_review("rdc:test:review", "confirm_unsent", "already sent, no review open")
		self.assertEqual(e.exception.code, "REVIEW_REFUSED")

	def test_cancel_stops_only_unsent_rows(self):
		waiting = self.queue("rdc:test:cancel-a")
		sent = self.queue("rdc:test:cancel-b")
		frappe.db.set_value("Telegram Outbox", waiting["outbox"], "scheduled_at", add_to_date(now_datetime(), hours=1))
		self.run_worker()
		result = strict.cancel(["rdc:test:cancel-a", "rdc:test:cancel-b"], reason="operator cancelled")
		self.assertEqual(result, {"rdc:test:cancel-a": "Cancelled", "rdc:test:cancel-b": "Sent"})
		self.assertEqual(self.row(sent["outbox"]).status, "Sent")

	def test_a_claim_is_won_by_one_worker_only(self):
		queued = self.queue()
		first = strict._claim(queued["outbox"])
		second = strict._claim(queued["outbox"])
		self.assertTrue(first)
		self.assertIsNone(second)

	def test_a_crash_before_the_attempt_requeues_after_it_needs_review(self):
		before = self.queue("rdc:test:crash-a")
		after = self.queue("rdc:test:crash-b")
		strict._claim(before["outbox"])
		token = strict._claim(after["outbox"])
		frappe.db.sql(
			"UPDATE `tabTelegram Outbox` SET attempt_started_at = %s WHERE name = %s AND claim_token = %s",
			(now_datetime(), after["outbox"], token),
		)
		frappe.db.sql(
			"UPDATE `tabTelegram Outbox` SET sending_since = %s WHERE name IN %s",
			(add_to_date(now_datetime(), minutes=-30), (before["outbox"], after["outbox"])),
		)
		self.assertEqual(strict.reclaim_stuck(), {"requeued": 1, "needs_review": 1})
		self.assertEqual(self.row(before["outbox"]).status, "Queued")
		self.assertEqual(self.row(after["outbox"]).status, "Needs Review")
		outbox.reclaim_stuck()  # the legacy reclaim never touches strict rows
		self.assertEqual(self.row(after["outbox"]).status, "Needs Review")
		self.assertEqual(self.sends, [])

	def test_listeners_hear_every_outcome(self):
		heard = []
		get_hooks, get_attr = frappe.get_hooks, frappe.get_attr
		with patch.object(
			frappe, "get_hooks", side_effect=lambda hook=None, *a, **k: ["rt.listener"] if hook == strict.LISTENER_HOOK else get_hooks(hook, *a, **k)
		), patch.object(
			frappe, "get_attr", side_effect=lambda path: (lambda **kw: heard.append(kw)) if path == "rt.listener" else get_attr(path)
		):
			queued = self.queue("rdc:test:listen")
			self.run_worker()
		self.assertEqual(heard[-1], {"outbox": queued["outbox"], "idempotency_key": "rdc:test:listen", "external_reference": "RDC-TEST-1", "status": "Sent"})


class TestLegacyQueueStillWorks(StrictTestCase):
	def test_legacy_rows_are_paced_and_strict_rows_ignored_by_the_legacy_worker(self):
		strict_row = self.queue()
		legacy = outbox.enqueue([{"telegram_user": self.recipient, "message": "hello"}], telegram_settings=BOT)
		sent = []
		with patch.object(telegram_client, "send_many", side_effect=lambda token, items, on_result=None, pause=0: [
			(sent.append(i["row"].name), on_result(i, None, [1])) for i in items
		]):  # fmt: skip
			self.assertEqual(outbox.process_settings(BOT), 1)
		self.assertEqual(sent, legacy)
		self.assertEqual(self.row(strict_row["outbox"]).status, "Queued")
		self.assertEqual(frappe.db.get_value("Telegram Outbox", legacy[0], "delivery_policy"), "Legacy")

	def test_manual_retry_refuses_strict_rows(self):
		queued = self.queue()
		frappe.db.set_value("Telegram Outbox", queued["outbox"], "status", "Failed")
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc("Telegram Outbox", queued["outbox"]).retry()


class TestPublicApiPermissions(StrictTestCase):
	def setUp(self):
		super().setUp()
		self.user = "_rt_plain_user@example.com"
		if not frappe.db.exists("User", self.user):
			frappe.get_doc({"doctype": "User", "email": self.user, "first_name": "RT", "send_welcome_email": 0, "roles": [{"role": "Blogger"}]}).insert(ignore_permissions=True)

	def test_a_user_cannot_send_a_private_file_or_print_a_document_they_cannot_read(self):
		frappe.set_user(self.user)
		file_url = frappe.db.get_value("File", self.file, "file_url")
		with self.assertRaises(frappe.PermissionError):
			api.send_message(telegram_user=self.recipient, message="x", file_url=file_url)
		with self.assertRaises(frappe.PermissionError):
			api.send_message(telegram_user=self.recipient, message="x", reference_doctype="Note", reference_name=self.note.name, attach_print=1)
		with self.assertRaises(frappe.PermissionError):
			api.send_message(telegram_user=self.recipient, message="plain text to someone else's chat")
		with self.assertRaises(frappe.PermissionError):
			api.send_bulk(messages=[{"telegram_user": self.recipient, "message": "x"}])
		with self.assertRaises(frappe.PermissionError):
			api.resolve_party("Customer", self.customer)
		self.assertEqual(self.sends, [])

	def test_an_unknown_file_url_is_refused_even_for_an_administrator(self):
		with self.assertRaises(frappe.DoesNotExistError):
			api.send_message(telegram_user=self.recipient, message="x", file_url="/private/files/not-a-real-file.pdf")
		with self.assertRaises(frappe.ValidationError):
			telegram_client.resolve_file("/private/files/not-a-real-file.pdf")

	def test_link_fields_cannot_be_redirected_by_a_record_owner(self):
		customer = self.make_customer("_RT Owner Customer")
		frappe.set_user(self.user)
		own = frappe.get_doc(
			{"doctype": "Telegram User Settings", "party": "Customer", "telegram_user": customer, "telegram_settings": BOT}
		)
		own.flags.ignore_permissions = True
		own.insert()
		own.telegram_chat_id = "999999"
		with self.assertRaises(frappe.PermissionError):
			own.save(ignore_permissions=True)

	def test_an_administrator_still_sends_as_before(self):
		result = api.send_message(telegram_user=self.recipient, message="hello")
		self.assertEqual(result["status"], "Sent")
		self.assertEqual(len(self.sends), 1)
