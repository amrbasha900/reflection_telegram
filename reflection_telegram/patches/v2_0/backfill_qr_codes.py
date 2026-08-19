"""Give every existing Telegram User Settings record a deep link and a QR code.

Records created before QR onboarding carry a hand-typed token -- including the
old `/`-prefixed group form, which is not a valid deep link payload. Those are
reissued. Chat ids are never touched, so recipients who are already linked stay
linked and keep receiving messages.
"""

import frappe

from reflection_telegram import onboarding


def execute():
	names = frappe.get_all(
		"Telegram User Settings",
		or_filters={"qr_code": ["is", "not set"], "deep_link": ["is", "not set"]},
		pluck="name",
	)

	for name in names:
		try:
			doc = frappe.get_doc("Telegram User Settings", name)
			onboarding.refresh(doc)
			doc.db_set(
				{
					"telegram_token": doc.telegram_token,
					"deep_link": doc.deep_link,
					"qr_code": doc.qr_code,
				},
				update_modified=False,
			)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title=f"Could not build a QR code for {name}",
				message=frappe.get_traceback(),
			)
