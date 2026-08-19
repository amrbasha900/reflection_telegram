# -*- coding: utf-8 -*-
# Copyright (c) 2019, Youssef Restom and contributors
# Copyright (c) 2026, Amr Basha and contributors
# For license information, please see license.txt

import frappe
from bs4 import BeautifulSoup
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr

from reflection_telegram import api
from reflection_telegram.utils import doc_url


class TelegramSettings(Document):
	def validate(self):
		self.telegram_token = cstr(self.telegram_token).strip()
		self.bot_name = cstr(self.bot_name).strip().lstrip("@")

		# The single most common setup mistake is pasting only the half of the
		# token after the colon, which Telegram answers with a bare 404.
		if self.telegram_token and ":" not in self.telegram_token:
			frappe.throw(
				_(
					"That looks like only part of the bot token. BotFather gives you "
					"<bot id>:<secret>, for example 1234567890:AAG... -- paste the whole thing."
				)
			)


@frappe.whitelist()
def send_to_telegram(
	telegram_user, message, reference_doctype=None, reference_name=None, attachment=None
):
	"""Send from the "Send To Telegram" menu that this app adds to every form.

	Kept as the entry point the desk bundle and Telegram Notification already
	call, but it now goes through `api.send_message` rather than talking to
	Telegram itself -- which is what puts these messages in Telegram Message Log,
	applies the blocked-chat check, and splits anything over 4096 characters
	instead of letting Telegram reject it.
	"""
	message = _plain_text(message)

	if reference_doctype and reference_name:
		link = _("See the document at {0}").format(doc_url(reference_doctype, reference_name))
		message = f"{message}\n\n{link}" if message else link

	return api.send_message(
		telegram_user=telegram_user,
		message=message,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		# The original sent a signed download link as text. Sending the PDF itself
		# means the recipient does not need the site to be reachable from wherever
		# they are, and there is no public URL left lying around in a chat.
		attach_print=cint(attachment) if reference_doctype and reference_name else 0,
	)


def _plain_text(message) -> str:
	"""Notification templates render HTML; Telegram wants text."""
	message = cstr(message)
	if not message:
		return ""

	return BeautifulSoup(message, "html.parser").get_text("\n").strip()
