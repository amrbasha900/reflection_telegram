"""Server side of the Telegram QR Codes page.

The page lets staff pick suppliers or customers, create the linking records that
do not exist yet, and print the QR codes -- one per person or a sheet of them.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, cstr, escape_html

from reflection_telegram import api

SUPPORTED_PARTY_TYPES = ("Supplier", "Customer", "Employee", "Contact", "User")


@frappe.whitelist()
def get_party_types() -> list[str]:
	return [dt for dt in SUPPORTED_PARTY_TYPES if frappe.db.exists("DocType", dt)]


@frappe.whitelist()
def get_parties(
	party_type: str,
	telegram_settings: str,
	search: str = None,
	only_unlinked: int = 0,
	limit: int = 200,
	start: int = 0,
) -> list[dict]:
	"""List parties alongside their Telegram linking state.

	Parties without a linking record are included on purpose -- the point of the
	page is to create the missing ones.
	"""
	if party_type not in SUPPORTED_PARTY_TYPES:
		frappe.throw(_("Unsupported party type {0}").format(party_type))

	frappe.has_permission(party_type, throw=True)

	meta = frappe.get_meta(party_type)
	title_field = meta.get_title_field()
	fields = ["name"] + ([title_field] if title_field and title_field != "name" else [])

	filters = {}
	or_filters = {}
	if search:
		or_filters = {"name": ["like", f"%{search}%"]}
		if title_field and title_field != "name":
			or_filters[title_field] = ["like", f"%{search}%"]

	if meta.has_field("disabled"):
		filters["disabled"] = 0

	parties = frappe.get_list(
		party_type,
		filters=filters,
		or_filters=or_filters,
		fields=fields,
		limit_page_length=cint(limit),
		limit_start=cint(start),
		order_by="modified desc",
	)
	if not parties:
		return []

	links = {
		row.telegram_user: row
		for row in frappe.get_all(
			"Telegram User Settings",
			filters={
				"party": party_type,
				"telegram_settings": telegram_settings,
				"telegram_user": ["in", [p.name for p in parties]],
			},
			fields=["name", "telegram_user", "telegram_chat_id", "qr_code", "deep_link", "linked_on"],
		)
	}

	rows = []
	for party in parties:
		link = links.get(party.name)
		linked = bool(link and link.telegram_chat_id)

		if cint(only_unlinked) and linked:
			continue

		rows.append(
			{
				"party": party.name,
				"party_name": party.get(title_field) if title_field else party.name,
				"telegram_user": link.name if link else None,
				"linked": linked,
				"qr_code": link.qr_code if link else None,
				"deep_link": link.deep_link if link else None,
				"linked_on": link.linked_on if link else None,
			}
		)

	return rows


@frappe.whitelist()
def generate(party_type: str, parties, telegram_settings: str, is_group_chat: int = 0) -> dict:
	"""Create the missing linking records and their QR codes.

	Already-linked parties are returned untouched: `api.ensure_link` never
	rotates a payload that is working.
	"""
	parties = _as_list(parties)
	if not parties:
		frappe.throw(_("Select at least one {0}").format(party_type))

	frappe.has_permission("Telegram User Settings", "create", throw=True)

	results, errors = [], []
	for party in parties:
		try:
			results.append(api.ensure_link(party_type, party, telegram_settings, is_group_chat))
		except Exception as exc:
			errors.append({"party": party, "error": cstr(exc)})
			frappe.log_error(title=f"Telegram QR generation failed for {party}", message=frappe.get_traceback())

	frappe.db.commit()
	return {"generated": results, "errors": errors}


@frappe.whitelist()
def print_html(telegram_users, columns: int = 2) -> str:
	"""Build a print sheet of QR cards.

	Returned as HTML rather than a Print Format so the page can lay out one card
	or fifty at the same size, which is what makes handing them out practical.
	"""
	telegram_users = _as_list(telegram_users)
	if not telegram_users:
		frappe.throw(_("Nothing selected to print"))

	rows = frappe.get_all(
		"Telegram User Settings",
		filters={"name": ["in", telegram_users]},
		fields=["name", "party", "telegram_user", "telegram_user_name", "qr_code", "deep_link", "telegram_chat_id"],
		order_by="telegram_user asc",
	)

	cards = []
	for row in rows:
		if not row.qr_code:
			continue

		title = escape_html(row.telegram_user_name or row.telegram_user)
		cards.append(
			f"""
			<div class="qr-card">
				<div class="qr-party">{title}</div>
				<div class="qr-code-id">{escape_html(row.telegram_user)}</div>
				<img src="{escape_html(row.qr_code)}" alt="QR">
				<div class="qr-hint">{escape_html(_("Scan, then press START"))}</div>
				<div class="qr-link">{escape_html(row.deep_link or "")}</div>
			</div>"""
		)

	if not cards:
		frappe.throw(_("None of the selected records have a QR code yet. Generate them first."))

	return _wrap(cards, cint(columns) or 2)


def _wrap(cards: list[str], columns: int) -> str:
	return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{_("Telegram QR Codes")}</title>
<style>
	body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 12mm; color: #111; }}
	.qr-sheet {{ display: grid; grid-template-columns: repeat({columns}, 1fr); gap: 8mm; }}
	.qr-card {{
		border: 1px solid #999; border-radius: 6px; padding: 6mm 4mm;
		text-align: center; break-inside: avoid; page-break-inside: avoid;
	}}
	.qr-card img {{ width: 100%; max-width: 55mm; image-rendering: pixelated; }}
	.qr-party {{ font-size: 13pt; font-weight: 600; margin-bottom: 1mm; }}
	.qr-code-id {{ font-size: 9pt; color: #666; margin-bottom: 3mm; }}
	.qr-hint {{ font-size: 10pt; margin-top: 3mm; }}
	.qr-link {{ font-size: 6.5pt; color: #888; word-break: break-all; margin-top: 2mm; }}
	@media print {{ body {{ padding: 8mm; }} }}
</style>
</head>
<body>
	<div class="qr-sheet">{"".join(cards)}</div>
	<script>window.onload = function () {{ window.print(); }};</script>
</body>
</html>"""


def _as_list(value) -> list[str]:
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			value = [value]
	return list(value or [])
