"""Rename the app from `erpnext_telegram_integration` to `reflection_telegram`.

Only the app package and its main module are renamed. DocType names are left
untouched so existing Telegram Settings / Telegram User Settings data survives.

Everything here goes through raw SQL on purpose. The Document API loads
`hooks.py` for every installed app and resolves controllers through the module
map, both of which still point at the old package name while this patch runs --
touching them before the rename lands raises ModuleNotFoundError.
"""

import json

import frappe

OLD_APP = "erpnext_telegram_integration"
NEW_APP = "reflection_telegram"
OLD_MODULE = "Erpnext Telegram Integration"
NEW_MODULE = "Reflection Telegram"
OLD_WORKSPACE = "ERPNext Telegram Integration"

WORKSPACE_CHILD_TABLES = (
	"Workspace Link",
	"Workspace Shortcut",
	"Workspace Chart",
	"Workspace Number Card",
	"Workspace Quick List",
	"Workspace Custom Block",
)


def execute():
	if not needs_rename():
		return

	rename_installed_app()
	rename_module()
	drop_stale_workspace()

	frappe.db.commit()
	forget_old_app_name()


def needs_rename():
	installed = frappe.db.sql(
		"select defvalue from `tabDefaultValue` where defkey = 'installed_apps' and parent = '__global'"
	)
	has_old_app = bool(installed) and OLD_APP in json.loads(installed[0][0] or "[]")
	has_old_module = bool(
		frappe.db.sql("select name from `tabModule Def` where name = %s", (OLD_MODULE,))
	)
	return has_old_app or has_old_module


def rename_installed_app():
	"""The app name lives in two places and both have to move.

	`frappe.get_installed_apps()` reads the `installed_apps` global; the
	`Installed Application` child table is only the human-readable mirror of it.
	"""
	frappe.db.sql(
		"update `tabInstalled Application` set app_name = %s where app_name = %s",
		(NEW_APP, OLD_APP),
	)

	row = frappe.db.sql(
		"select defvalue from `tabDefaultValue` where defkey = 'installed_apps' and parent = '__global'"
	)
	if not row:
		return

	installed = json.loads(row[0][0] or "[]")
	if OLD_APP not in installed:
		return

	installed = [NEW_APP if app == OLD_APP else app for app in installed]
	frappe.db.sql(
		"update `tabDefaultValue` set defvalue = %s where defkey = 'installed_apps' and parent = '__global'",
		(json.dumps(installed),),
	)


def rename_module():
	"""Rename the Module Def and repoint everything that links to it.

	Both modules shipped by this app ("Reflection Telegram" and
	"Extra Notifications") need their `app_name` moved; only the first is renamed.
	"""
	frappe.db.sql(
		"update `tabModule Def` set app_name = %s where app_name = %s",
		(NEW_APP, OLD_APP),
	)
	frappe.db.sql(
		"update `tabModule Def` set name = %s, module_name = %s where name = %s",
		(NEW_MODULE, NEW_MODULE, OLD_MODULE),
	)

	for table in tables_with_module_column():
		frappe.db.sql(
			f"update `{table}` set module = %s where module = %s",  # nosemgrep
			(NEW_MODULE, OLD_MODULE),
		)


def tables_with_module_column():
	"""Every doctype that stores a Module Def link, discovered rather than listed."""
	return [
		row[0]
		for row in frappe.db.sql(
			"""
			select TABLE_NAME from INFORMATION_SCHEMA.COLUMNS
			where TABLE_SCHEMA = DATABASE() and COLUMN_NAME = 'module'
			"""
		)
	]


def drop_stale_workspace():
	"""The workspace is recreated from the app's JSON on the next migrate."""
	for table in WORKSPACE_CHILD_TABLES:
		if frappe.db.table_exists(table):
			frappe.db.sql(f"delete from `tab{table}` where parent = %s", (OLD_WORKSPACE,))  # nosemgrep

	frappe.db.sql("delete from `tabWorkspace` where name = %s", (OLD_WORKSPACE,))


def forget_old_app_name():
	"""Drop every cache that still answers with the old app name."""
	for key in ("defaults", "app_hooks", "installed_apps", "app_modules", "installed_app_modules"):
		frappe.cache.delete_value(key)

	if hasattr(frappe.local, "request_cache"):
		frappe.local.request_cache.clear()

	frappe.setup_module_map()
