# coding=utf-8

from __future__ import unicode_literals
from frappe import _

def get_data():
	return [
		# Modules
		{
			"module_name": "Reflection Telegram",
			"category": "Administration",
			"label": _("Reflection Telegram"),
			"color": "#3498db",
			"icon": "octicon octicon-repo",
			"type": "module",
			"description": "Telegram integration, QR onboarding and bulk sending."
		},
		{
			"module_name": "Extra Notifications",
			"category": "Administration",
			"label": _("Extra Notifications"),
			"color": "#3498db",
			"icon": "octicon octicon-repo",
			"type": "module",
			"description": "Telegram & SMS Notifications For Erpnext."
		}
	]
