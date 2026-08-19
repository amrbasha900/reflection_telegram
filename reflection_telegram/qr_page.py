"""Server side of the Telegram QR Codes page.

Sites here carry thousands of suppliers and customers, which shapes everything
below: listing is paginated in SQL rather than filtered in Python, generating
for a whole party type runs as a background job with progress, and printing lays
cards out in fixed-size A4 sheets so a run of 300 pages comes out identical.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, cstr, escape_html

from reflection_telegram import api

SUPPORTED_PARTY_TYPES = ("Supplier", "Customer", "Employee", "Contact", "User")

# Above this, generating happens in the background instead of blocking the page.
INLINE_GENERATE_LIMIT = 25

# Card counts that divide an A4 sheet evenly, as (per page, columns, rows).
SHEET_LAYOUTS = {2: (1, 2), 4: (2, 2), 6: (2, 3), 8: (2, 4), 9: (3, 3), 12: (3, 4)}

# Printing more than this at once is usually a mistake, not a plan.
PRINT_LIMIT = 600


@frappe.whitelist()
def get_party_types() -> list[dict]:
	"""Party types worth showing, with how many records each holds."""
	types = []
	for doctype in SUPPORTED_PARTY_TYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		if not frappe.has_permission(doctype):
			continue
		types.append({"party_type": doctype, "count": frappe.db.count(doctype)})

	return types


def _validate(party_type: str):
	if party_type not in SUPPORTED_PARTY_TYPES:
		frappe.throw(_("Unsupported party type {0}").format(party_type))
	frappe.has_permission(party_type, throw=True)


def _title_field(party_type: str) -> str | None:
	title_field = frappe.get_meta(party_type).get_title_field()
	return title_field if title_field and title_field != "name" else None


def _query(party_type: str, telegram_settings: str, search: str, link_status: str):
	"""Build the shared FROM/WHERE for listing and counting.

	A LEFT JOIN rather than two queries: the page needs parties that have no
	linking record yet, which is exactly what it exists to create, and the
	linked/unlinked filter has to apply before pagination or the page counts lie.
	"""
	title_field = _title_field(party_type)
	name_expr = f"p.`{title_field}`" if title_field else "p.`name`"

	conditions = ["1 = 1"]
	values = {"party_type": party_type, "settings": telegram_settings}

	if frappe.get_meta(party_type).has_field("disabled"):
		conditions.append("IFNULL(p.`disabled`, 0) = 0")

	if search:
		conditions.append(f"(p.`name` LIKE %(search)s OR {name_expr} LIKE %(search)s)")
		values["search"] = f"%{search}%"

	if link_status == "linked":
		conditions.append("t.`telegram_chat_id` IS NOT NULL AND t.`telegram_chat_id` != ''")
	elif link_status == "unlinked":
		conditions.append("(t.`telegram_chat_id` IS NULL OR t.`telegram_chat_id` = '')")
	elif link_status == "no_qr":
		conditions.append("(t.`qr_code` IS NULL OR t.`qr_code` = '')")

	from_clause = f"""
		FROM `tab{party_type}` p
		LEFT JOIN `tabTelegram User Settings` t
			ON t.`telegram_user` = p.`name`
			AND t.`party` = %(party_type)s
			AND t.`telegram_settings` = %(settings)s
		WHERE {" AND ".join(conditions)}
	"""

	return from_clause, values, name_expr


@frappe.whitelist()
def get_parties(
	party_type: str,
	telegram_settings: str,
	search: str = None,
	link_status: str = "",
	start: int = 0,
	page_length: int = 24,
) -> dict:
	"""One page of parties with their Telegram linking state, plus the total."""
	_validate(party_type)

	from_clause, values, name_expr = _query(party_type, telegram_settings, search, link_status)

	total = frappe.db.sql(f"SELECT COUNT(*) {from_clause}", values)[0][0]  # nosemgrep

	values["start"] = cint(start)
	values["page_length"] = max(1, cint(page_length))

	rows = frappe.db.sql(
		f"""
		SELECT
			p.`name` AS party,
			{name_expr} AS party_name,
			t.`name` AS telegram_user,
			t.`telegram_chat_id` AS chat_id,
			t.`qr_code` AS qr_code,
			t.`deep_link` AS deep_link,
			t.`linked_on` AS linked_on
		{from_clause}
		ORDER BY p.`name` ASC
		LIMIT %(page_length)s OFFSET %(start)s
		""",  # nosemgrep
		values,
		as_dict=True,
	)

	for row in rows:
		row["linked"] = bool(row.get("chat_id"))

	return {
		"rows": rows,
		"total": total,
		"start": cint(start),
		"page_length": cint(page_length),
	}


def _matching_parties(party_type: str, telegram_settings: str, search: str, link_status: str) -> list[str]:
	"""Every party the current filter selects, ignoring pagination."""
	from_clause, values, _ = _query(party_type, telegram_settings, search, link_status)
	return [
		row[0]
		for row in frappe.db.sql(f"SELECT p.`name` {from_clause} ORDER BY p.`name` ASC", values)  # nosemgrep
	]


@frappe.whitelist()
def count_matching(party_type: str, telegram_settings: str, search: str = None, link_status: str = "") -> int:
	_validate(party_type)
	from_clause, values, _ = _query(party_type, telegram_settings, search, link_status)
	return frappe.db.sql(f"SELECT COUNT(*) {from_clause}", values)[0][0]  # nosemgrep


@frappe.whitelist()
def generate(
	party_type: str,
	telegram_settings: str,
	parties=None,
	select_all: int = 0,
	search: str = None,
	link_status: str = "",
	is_group_chat: int = 0,
) -> dict:
	"""Create the missing linking records and their QR codes.

	Pass an explicit `parties` list, or `select_all=1` to take everything the
	current filter matches. Small runs happen inline so the page can refresh
	immediately; anything larger is queued, because generating a few thousand QR
	images would outlast the request.
	"""
	_validate(party_type)
	frappe.has_permission("Telegram User Settings", "create", throw=True)

	if cint(select_all):
		parties = _matching_parties(party_type, telegram_settings, search, link_status)
	else:
		parties = _as_list(parties)

	if not parties:
		frappe.throw(_("Select at least one {0}").format(_(party_type)))

	if len(parties) <= INLINE_GENERATE_LIMIT:
		result = _generate_now(party_type, parties, telegram_settings, is_group_chat)
		result["queued"] = False
		return result

	frappe.enqueue(
		"reflection_telegram.qr_page.generate_in_background",
		queue="long",
		timeout=7200,
		party_type=party_type,
		parties=parties,
		telegram_settings=telegram_settings,
		is_group_chat=cint(is_group_chat),
		user=frappe.session.user,
	)

	return {"queued": True, "total": len(parties), "generated": [], "errors": []}


def _generate_now(party_type: str, parties: list[str], telegram_settings: str, is_group_chat: int) -> dict:
	generated, errors = [], []

	for party in parties:
		try:
			generated.append(api.ensure_link(party_type, party, telegram_settings, is_group_chat))
		except Exception as exc:
			errors.append({"party": party, "error": cstr(exc)})
			frappe.log_error(
				title=f"Telegram QR generation failed for {party}", message=frappe.get_traceback()
			)

	frappe.db.commit()
	return {"generated": generated, "errors": errors, "total": len(parties)}


def generate_in_background(party_type, parties, telegram_settings, is_group_chat, user):
	"""Chip away at a large run, publishing progress as it goes.

	Committing per record rather than at the end means a failure late in a run of
	thousands does not throw away the QR codes already built.
	"""
	total = len(parties)
	done = failed = 0

	for index, party in enumerate(parties, start=1):
		try:
			api.ensure_link(party_type, party, telegram_settings, cint(is_group_chat))
			frappe.db.commit()
			done += 1
		except Exception:
			frappe.db.rollback()
			failed += 1
			frappe.log_error(
				title=f"Telegram QR generation failed for {party}", message=frappe.get_traceback()
			)

		if index % 10 == 0 or index == total:
			frappe.publish_realtime(
				"telegram_qr_progress",
				{"done": done, "failed": failed, "total": total, "finished": index == total},
				user=user,
			)


@frappe.whitelist()
def print_html(
	telegram_users=None,
	parties=None,
	party_type: str = None,
	telegram_settings: str = None,
	select_all: int = 0,
	search: str = None,
	link_status: str = "",
	per_page: int = 9,
) -> str:
	"""Build a print sheet, `per_page` cards to each A4 page.

	Accepts party names as well as linking-record names so a selection spanning
	several pages of the list can be printed -- the browser only holds the rows it
	has rendered, so resolving party names has to happen here.

	Returned as HTML rather than a Print Format because the layout has to change
	with the card count, and because a 300 page run needs every sheet identical.
	"""
	if cint(select_all):
		_validate(party_type)
		names = _matching_parties(party_type, telegram_settings, search, link_status)
		telegram_users = _resolve_users(party_type, telegram_settings, names)
	elif parties:
		_validate(party_type)
		telegram_users = _resolve_users(party_type, telegram_settings, _as_list(parties))
	else:
		telegram_users = _as_list(telegram_users)

	if not telegram_users:
		frappe.throw(_("Nothing to print. Generate the QR codes first."))

	if len(telegram_users) > PRINT_LIMIT:
		frappe.throw(
			_("That is {0} QR codes in one go. Narrow the selection to {1} or fewer, or print a page at a time.").format(
				len(telegram_users), PRINT_LIMIT
			)
		)

	rows = frappe.get_all(
		"Telegram User Settings",
		filters={"name": ["in", telegram_users]},
		fields=["name", "party", "telegram_user", "telegram_user_name", "qr_code", "deep_link"],
		order_by="telegram_user asc",
	)

	cards = [_card(row) for row in rows if row.qr_code]
	if not cards:
		frappe.throw(_("None of the selected records have a QR code yet. Generate them first."))

	per_page = cint(per_page)
	if per_page not in SHEET_LAYOUTS:
		per_page = 9

	return _sheets(cards, per_page)


def _resolve_users(party_type: str, telegram_settings: str, parties: list[str]) -> list[str]:
	"""Party names to linking-record names, skipping any without a QR."""
	if not parties:
		return []

	return frappe.get_all(
		"Telegram User Settings",
		filters={
			"party": party_type,
			"telegram_settings": telegram_settings,
			"telegram_user": ["in", parties],
			"qr_code": ["is", "set"],
		},
		pluck="name",
	)


def _card(row) -> str:
	title = escape_html(row.telegram_user_name or row.telegram_user)
	return f"""
		<div class="card">
			<div class="name">{title}</div>
			<div class="code">{escape_html(row.telegram_user)}</div>
			<img src="{escape_html(row.qr_code)}" alt="QR">
			<div class="hint">{escape_html(_("Scan, then press START"))}</div>
		</div>"""


def _sheets(cards: list[str], per_page: int) -> str:
	"""Group cards into fixed-size pages.

	Each sheet is its own grid sized to the printable area, so the last page of a
	run looks like the first instead of the cards stretching to fill it.
	"""
	columns, rows = SHEET_LAYOUTS[per_page]

	pages = []
	for start in range(0, len(cards), per_page):
		chunk = cards[start : start + per_page]
		pages.append(f'<div class="sheet">{"".join(chunk)}</div>')

	return _document("".join(pages), columns, rows)


def _document(body: str, columns: int, rows: int) -> str:
	return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{_("Telegram QR Codes")}</title>
<style>
	@page {{ size: A4; margin: 8mm; }}
	* {{ box-sizing: border-box; }}
	body {{
		margin: 0; background: #f4f4f5;
		font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif; color: #18181b;
	}}
	.sheet {{
		width: 194mm; height: 281mm; margin: 0 auto 6mm; padding: 0; background: #fff;
		display: grid;
		grid-template-columns: repeat({columns}, 1fr);
		grid-template-rows: repeat({rows}, 1fr);
		gap: 4mm;
		page-break-after: always; break-after: page;
	}}
	.sheet:last-child {{ page-break-after: auto; break-after: auto; }}
	.card {{
		border: 1px dashed #a1a1aa; border-radius: 3mm;
		padding: 4mm 3mm; overflow: hidden;
		display: flex; flex-direction: column; align-items: center; justify-content: center;
		text-align: center; break-inside: avoid; page-break-inside: avoid;
	}}
	.card img {{ flex: 1 1 auto; min-height: 0; max-width: 100%; object-fit: contain; image-rendering: pixelated; }}
	.name {{ font-size: 11pt; font-weight: 600; line-height: 1.2; margin-bottom: 0.5mm;
	         overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
	.code {{ font-size: 7.5pt; color: #71717a; margin-bottom: 2mm; }}
	.hint {{ font-size: 8.5pt; color: #3f3f46; margin-top: 2mm; }}
	@media screen {{ body {{ padding: 6mm 0; }} .sheet {{ box-shadow: 0 1px 6px rgba(0,0,0,.15); }} }}
	@media print {{ body {{ background: #fff; padding: 0; }} .sheet {{ margin: 0; box-shadow: none; }} }}
</style>
</head>
<body>{body}
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
