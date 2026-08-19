# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from . import __version__ as app_version

app_name = "reflection_telegram"
app_title = "Reflection Telegram"
app_publisher = "Amr Basha"
app_description = "Telegram integration for Frappe/ERPNext: QR onboarding, rate-limited bulk sending, and a reusable API"
app_icon = "octicon octicon-comment-discussion"
app_color = "grey"
app_email = "amrbasha900@users.noreply.github.com"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/reflection_telegram/css/reflection_telegram.css"
# app_include_js = "/assets/reflection_telegram/js/reflection_telegram.js"
app_include_js = ["reflection_telegram.bundle.js"]


# include js, css files in header of web template
# web_include_css = "/assets/reflection_telegram/css/reflection_telegram.css"
# web_include_js = "/assets/reflection_telegram/js/reflection_telegram.js"

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
## doctype_js = {"Quote" : "public/js/send_to_telegram.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Website user home page (by function)
# get_website_user_home_page = "reflection_telegram.utils.get_home_page"

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------

# before_install = "reflection_telegram.install.before_install"
# after_install = "reflection_telegram.install.after_install"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "reflection_telegram.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }
doc_events = {
	"*": {
		"validate": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"onload": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"before_insert": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"after_insert": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"before_naming": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"before_change": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"before_update_after_submit": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"before_validate": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"before_save": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"autoname": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"on_update": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"on_cancel": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"on_trash": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"on_submit": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"on_update_after_submit": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
		"on_change": [
			"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.run_telegram_notifications",
			"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.run_sms_notifications",
		],
	},
}
# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		# The outbox paces itself internally; this just wakes it up. Overlapping
		# runs take a lock and skip, so a slow batch cannot pile up.
		"* * * * *": [
			"reflection_telegram.outbox.process",
		],
		# Fallback for bots with `enable_polling` set and no webhook registered.
		"*/5 * * * *": [
			"reflection_telegram.webhook.poll",
		],
	},
	"daily": [
		"reflection_telegram.message_log.purge",
		"reflection_telegram.reflection_telegram.doctype.telegram_notification.telegram_notification.trigger_daily_alerts",
		"reflection_telegram.reflection_telegram.doctype.sms_notification.sms_notification.trigger_daily_alerts",
		"reflection_telegram.extra_notifications.doctype.date_notification.date_notification.trigger_daily_alerts",
	],
}

# Testing
# -------

# before_tests = "reflection_telegram.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "reflection_telegram.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "reflection_telegram.task.get_dashboard_data"
# }
