"""Shared helpers.

Chiefly: working out this site's public URL. `frappe.utils.get_url()` derives it
from the incoming HTTP request, which is fine in the desk and wrong everywhere
this app actually runs -- the scheduler, background jobs and webhook handlers
have no request, and get back `http://<site name>`. A link like that in a
Telegram message goes nowhere.
"""

from contextlib import contextmanager

import frappe
from frappe.utils import cstr


def site_base_url() -> str:
	"""The public https base URL for this site, request or no request."""
	host = frappe.conf.get("host_name")

	if not host:
		domains = frappe.conf.get("domains") or []
		host = domains[0] if domains else None

	if not host:
		host = frappe.utils.get_url()

	host = cstr(host).rstrip("/")
	if not host.startswith("http"):
		host = f"https://{host}"

	return host


def doc_url(doctype: str, name: str) -> str:
	"""A link to a document that still works from someone's phone."""
	return f"{site_base_url()}/app/{frappe.scrub(doctype).replace('_', '-')}/{frappe.utils.quoted(name)}"


@contextmanager
def pinned_host():
	"""Force `get_url()` onto the site's real host for the duration.

	The PDF renderer fetches stylesheets and images over HTTP using whatever
	`get_url()` returns. With no request in scope that is `http://<site name>`,
	which resolves nowhere, and wkhtmltopdf aborts with HostNotFoundError instead
	of producing an unstyled document. Pinning it here rather than setting
	`host_name` in site config keeps the change to this app's rendering.
	"""
	previous = frappe.local.conf.get("host_name")
	frappe.local.conf.host_name = site_base_url()
	try:
		yield
	finally:
		frappe.local.conf.host_name = previous


def render_pdf(doctype: str, name: str, print_format: str = None) -> tuple[str, bytes]:
	"""Render a document to PDF bytes, whatever Print Settings says.

	`frappe.attach_print` quietly returns HTML when "Send Print as PDF" is off in
	Print Settings, and an .html attachment is useless in a chat. Telegram gets a
	PDF or nothing.
	"""
	with pinned_host():
		content = frappe.get_print(doctype, name, print_format=print_format, as_pdf=True)

	return f"{frappe.scrub(name)}.pdf", content
