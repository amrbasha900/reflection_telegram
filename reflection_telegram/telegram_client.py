"""Low level Telegram transport.

Everything that talks to the Telegram Bot API goes through here, so retry
classification and the async/sync bridge live in exactly one place.

python-telegram-bot is fully async while Frappe is not, so each entry point
opens one event loop and, where several messages are involved, reuses a single
bot session across all of them instead of paying the setup cost per message.
"""

import asyncio
import os

import frappe
import telegram
from frappe import _
from frappe.utils import cstr, get_files_path

# https://core.telegram.org/bots/api#sendmessage
MAX_MESSAGE_LENGTH = 4096
MAX_CAPTION_LENGTH = 1024


class TelegramError(frappe.ValidationError):
	pass


class PermanentError(TelegramError):
	"""Retrying will not help: bad token, blocked bot, unknown chat."""


class TransientError(TelegramError):
	"""Worth another attempt: network blips and rate limits."""

	def __init__(self, message, retry_after=0):
		super().__init__(message)
		self.retry_after = retry_after


def get_token(telegram_settings: str) -> str:
	token = frappe.db.get_value("Telegram Settings", telegram_settings, "telegram_token")
	if not token:
		frappe.throw(
			_("Telegram Settings {0} has no bot token").format(telegram_settings), PermanentError
		)

	token = cstr(token).strip()
	if ":" not in token:
		frappe.throw(
			_(
				"The bot token in Telegram Settings {0} is incomplete. A token from BotFather "
				"looks like <bot id>:<secret>, for example 1234567890:AAG..."
			).format(telegram_settings),
			PermanentError,
		)

	return token


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
	"""Break an over-long message on line boundaries where possible.

	Telegram rejects anything past the limit outright, so a long statement has to
	arrive as several messages rather than be silently truncated.
	"""
	text = cstr(text)
	if len(text) <= limit:
		return [text]

	chunks = []
	remaining = text
	while len(remaining) > limit:
		window = remaining[:limit]
		split_at = window.rfind("\n")
		if split_at < limit // 2:
			split_at = window.rfind(" ")
		if split_at < limit // 2:
			split_at = limit

		chunks.append(remaining[:split_at].rstrip())
		remaining = remaining[split_at:].lstrip()

	if remaining:
		chunks.append(remaining)

	return chunks


def resolve_file(file_url: str) -> tuple[str, bytes]:
	"""Read a Frappe file URL off disk and return (filename, contents)."""
	file_url = cstr(file_url)
	if not file_url:
		return None, None

	file_name = frappe.db.get_value("File", {"file_url": file_url}, "file_name")
	is_private = file_url.startswith("/private/")
	path = os.path.join(
		get_files_path(is_private=1 if is_private else 0),
		os.path.basename(file_url.split("?")[0]),
	)

	if not os.path.exists(path):
		frappe.throw(_("Attachment {0} was not found on disk").format(file_url), PermanentError)

	with open(path, "rb") as f:
		return file_name or os.path.basename(path), f.read()


def classify(exc: Exception) -> TelegramError:
	"""Map a python-telegram-bot exception onto retry semantics."""
	if isinstance(exc, telegram.error.RetryAfter):
		return TransientError(str(exc), retry_after=int(getattr(exc, "retry_after", 0) or 0))

	if isinstance(exc, telegram.error.TimedOut | telegram.error.NetworkError):
		return TransientError(str(exc))

	if isinstance(exc, telegram.error.InvalidToken):
		return PermanentError(
			_("Telegram rejected the bot token: {0}. Check the token in Telegram Settings.").format(exc)
		)

	if isinstance(exc, telegram.error.Forbidden):
		return PermanentError(
			_("The bot cannot message this chat: {0}. The user may have blocked it or removed it from the group.").format(exc)
		)

	if isinstance(exc, telegram.error.BadRequest):
		return PermanentError(str(exc))

	return TransientError(str(exc))


async def _deliver(bot, chat_id, text, document=None, filename=None, parse_mode=None):
	"""Send one logical message, splitting it if Telegram would reject the length."""
	if document:
		caption = text if text and len(text) <= MAX_CAPTION_LENGTH else None
		await bot.send_document(
			chat_id=chat_id,
			document=document,
			filename=filename,
			caption=caption,
			parse_mode=parse_mode,
		)
		if text and not caption:
			for chunk in split_message(text):
				await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode)
		return

	for chunk in split_message(text):
		await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode)


def send(token: str, chat_id: str, text: str, document=None, filename=None, parse_mode=None):
	"""Send a single message. Raises PermanentError or TransientError on failure."""

	async def _run():
		bot = telegram.Bot(token=token)
		async with bot:
			await _deliver(bot, chat_id, text, document, filename, parse_mode)

	try:
		asyncio.run(_run())
	except Exception as exc:
		raise classify(exc) from exc


def send_many(token: str, items: list[dict], on_result=None, pause: float = 0.0):
	"""Send several messages over one bot session, pausing between each.

	`items` are dicts of chat_id / text / document / filename / parse_mode plus
	whatever the caller needs to correlate results. `on_result` is called with
	(item, error) after each attempt -- error is None on success -- so the caller
	can record progress as it happens rather than only at the end.
	"""

	async def _run():
		bot = telegram.Bot(token=token)
		async with bot:
			for index, item in enumerate(items):
				error = None
				try:
					await _deliver(
						bot,
						item["chat_id"],
						item.get("text"),
						item.get("document"),
						item.get("filename"),
						item.get("parse_mode"),
					)
				except Exception as exc:
					error = classify(exc)

				if on_result:
					on_result(item, error)

				if pause and index < len(items) - 1:
					await asyncio.sleep(pause)

	asyncio.run(_run())


def call_api(token: str, method: str, **kwargs):
	"""Run one arbitrary Bot API method, e.g. get_me / set_webhook / get_updates."""

	async def _run():
		bot = telegram.Bot(token=token)
		async with bot:
			return await getattr(bot, method)(**kwargs)

	try:
		return asyncio.run(_run())
	except Exception as exc:
		raise classify(exc) from exc
