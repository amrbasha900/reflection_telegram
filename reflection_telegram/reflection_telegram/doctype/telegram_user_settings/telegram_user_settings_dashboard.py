from frappe import _


def get_data():
	return {
		"fieldname": "telegram_user",
		"transactions": [
			{"label": _("Activity"), "items": ["Telegram Message Log", "Telegram Outbox"]},
		],
	}
