# Reflection Telegram

Telegram integration for Frappe / ERPNext.

Forked from [yrestom/erpnext_telegram](https://github.com/yrestom/erpnext_telegram) and
extended with QR onboarding, a rate-limited sending queue, and an API other apps can call.

## What it adds over the original

| | Original | This app |
|---|---|---|
| Linking a recipient | Copy a token, paste it into the bot chat by hand, click **Get Chat ID** within 24 h | Scan a QR, press **START** — done |
| Capturing the chat id | Manual button, `getUpdates` polling | Webhook (instant), with optional 5-minute polling as a fallback |
| Sending in bulk | One at a time, inline | Queued and paced, so Telegram does not restrict the bot |
| Calling from another app | Import a doctype module | `reflection_telegram.api` |

## Setup

1. **Create the bot.** Talk to [@BotFather](https://t.me/BotFather), `/newbot`, and copy the
   token. It looks like `1234567890:AAG...` — the digits before the colon are part of it.
2. **Telegram Settings.** Create a record with the full token and the bot's username
   (without the `@`).
3. **Register the webhook.** Open the record → **Webhook** → **Register Webhook**. Use
   **Check Bot** to confirm Telegram accepted it.

   The site must be reachable over HTTPS from the internet. If it is not, leave the webhook
   off and tick **Poll Telegram Every 5 Minutes** instead.
4. **Set the sending rate** if 20 messages/minute is not right for you.

## Linking recipients

Open **Telegram QR Codes** (or the workspace shortcut):

1. Pick the Telegram Settings and the party type (Supplier, Customer, Employee, Contact, User).
2. Select the records you want and press **Generate QR**.
3. **Print Selected** produces a sheet of QR cards to hand out. **Print One Per Page** gives
   one card per sheet.
4. The recipient scans the card, presses **START**, and the chat id is stored automatically.
   The card turns green on the page.

Tick **Group Chat** before generating to produce links that add the bot to a group instead of
opening a private chat.

### How the QR works

The QR encodes a Telegram [deep link](https://core.telegram.org/bots/features#deep-linking):

```
https://t.me/<bot>?start=<payload>            private chat
https://t.me/<bot>?startgroup=<payload>       group chat
```

Opening it shows a START button. Pressing it makes Telegram send `/start <payload>` to the
bot, the webhook matches the payload back to the Telegram User Settings record, and stores
`telegram_chat_id`. Nothing has to be typed or copied.

The payload is 32 hex characters, well inside Telegram's 64-character limit. Records created
before this app existed still link: the matcher also accepts a bare paste and the old
`/<token>` group form.

## Sending

See [docs/API.md](docs/API.md) for the full reference. The short version:

```python
from reflection_telegram import api

# one message, right now
api.send_message(telegram_user="2012381-Send Agri Statement", message="Your statement is ready")

# by business record instead of linking record
api.send_to_party(party_type="Supplier", party="2012381", message="Your statement is ready")

# with a PDF of a document
api.send_message(
    telegram_user="2012381-Send Agri Statement",
    message="Statement attached",
    reference_doctype="Sales Invoice",
    reference_name="ACC-SINV-2026-00001",
    attach_print=1,
)

# 250 messages, each to its own recipient, paced automatically
api.send_bulk(messages=[
    {"telegram_user": "2012381-Send Agri Statement", "message": "..."},
    {"telegram_user": "2012382-Send Agri Statement", "message": "..."},
    # ...
], title="August statements")
```

### Why bulk sends are queued

Telegram rate-limits, and eventually restricts, a bot that fires hundreds of messages at
once. `send_bulk` never sends inline. It writes **Telegram Outbox** rows stamped with a
`scheduled_at` spread across time, and a once-a-minute scheduler job drains whatever has come
due, pausing between each send.

At the default 20 messages/minute, 250 messages go out over about 12 minutes. Track progress
on the **Telegram Broadcast** record — it counts sent and failed, and **Cancel Pending** stops
anything that has not left yet.

Messages that fail for a transient reason go back in the queue. When Telegram answers `429` it
also says how long to wait, and the queue honours that exactly, which is what keeps a rate
limit from escalating into a restriction. Permanent failures — the bot was blocked, the chat
is gone, the token is wrong — are marked Failed immediately rather than retried.

## Doctypes

| DocType | Purpose |
|---|---|
| Telegram Settings | One bot: token, webhook, polling toggle, sending rate |
| Telegram User Settings | One recipient: party, payload, deep link, QR, chat id |
| Telegram Outbox | The sending queue, one row per message |
| Telegram Broadcast | Groups a bulk send and tracks its progress |
| Telegram Notification | Event-driven alerts (from the original app) |
| SMS Notification, Date Notification | Unrelated extras carried over from the original app |

## Scheduler

| Schedule | Job | What it does |
|---|---|---|
| every minute | `reflection_telegram.outbox.process` | Drains due Outbox rows. Overlapping runs take a lock and skip. |
| every 5 minutes | `reflection_telegram.webhook.poll` | Only for bots with polling on and no webhook registered. |

## Notes

- **Webhook and polling are mutually exclusive at Telegram's end.** While a webhook is
  registered, `getUpdates` returns nothing, so the polling job skips those bots.
- **Telegram discards undelivered updates after 24 hours.** This only matters when polling;
  with a webhook, updates arrive immediately.
- **Group privacy mode.** In a group the bot only sees messages that start with `/`. Deep
  links arrive as `/start <payload>`, so they work without turning privacy mode off.
- The webhook endpoint is public by necessity. It is protected by a per-bot secret that
  Telegram sends in the `X-Telegram-Bot-Api-Secret-Token` header; anything else gets a 403.

## Licence

MIT
