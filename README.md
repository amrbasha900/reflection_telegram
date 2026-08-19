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

Open **Telegram QR Codes** from the workspace.

**1. Pick who you are printing for.** Supplier, Customer, Employee, Contact or User, each
shown with how many records it holds.

**2. Find them.** The list is paginated with page numbers — sites here carry thousands of
suppliers and customers, so nothing is truncated and nothing loads all at once. Search by
code or name, and filter by **Not linked**, **Linked** or **No QR yet**. Page size is
12, 24, 48 or 96.

**3. Select.** Click cards to select them, **Select Page** for everything on screen, or
**Select all N matching** to take the whole filtered set across every page.

**4. Generate.** Up to 25 records are done immediately. Anything larger runs in the
background with a progress bar, committing as it goes — a failure two thousand records in
does not throw away the QR codes already built. Records that are already linked are left
alone: an existing payload that works is never rotated.

**5. Print.** Choose how many cards fit on an A4 page — 2, 4, 6, 8, 9 (default) or 12. Each
sheet is a fixed-size grid, so the last page looks like the first instead of the cards
stretching to fill it. Up to 600 cards in one run; beyond that, filter or print page by page.

The recipient scans a card, presses **START**, and the chat id is stored automatically. The
card turns green in the list.

Tick **Group chat** before generating to produce links that add the bot to a group instead of
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

### What happens when a message fails

Only **queued** messages are retried. An inline `api.send_message` raises to the caller
instead — the caller is in a better position to decide than this app is.

The queue splits failures by whether another attempt could plausibly help:

| Failure | Retried? | Why |
|---|---|---|
| `429` rate limit | Yes, after exactly the `retry_after` Telegram asked for | Honouring it is what keeps a rate limit from becoming a restriction |
| Network error, timeout | Yes, backing off 1 minute, then 5 | The network is the likeliest thing to have changed |
| `403` bot blocked | **No** | Nothing will change until the user unblocks |
| `400` chat not found | **No** | The chat is gone |
| Bad or incomplete token | **No** | Every attempt fails identically |
| Recipient never scanned their QR | **No** | There is no chat to send to |

**Max Attempts Per Message** on Telegram Settings caps the retryable kinds (3 by default:
the original send, then two retries). After that the row is Failed and stays put.

If a worker dies mid-batch — a restart, a deploy, an out-of-memory kill — the rows it was
holding are put back in the queue after 15 minutes rather than being stranded in Sending
forever. At most one message per crash could go out twice, because results are committed
one at a time; bounded duplication beats a statement that silently never arrives.

**Retrying by hand.** A Failed row has a **Retry** button, the Outbox list view has a bulk
**Retry** action, and a Broadcast has **Retry Failed** which requeues its failures re-paced
at the normal rate — dumping 60 recovered messages in at once is how a recovery becomes the
next rate limit. Manual retries reset the attempt count: the automatic retries gave up for a
reason, and a person asking again is a new decision.

## The log, and what "status" can mean

**Telegram gives bots no delivery or read receipt.** There is no update type that
reports a sent message was delivered, and nothing arrives later to say it was read. Any
integration claiming otherwise is inventing it. What you actually get is:

| Signal | Where it comes from | What it means |
|---|---|---|
| `message_id` | the reply to `sendMessage` | Telegram **accepted** the message |
| `403 Forbidden` | the send attempt | the user blocked the bot |
| `400 chat not found` | the send attempt | the chat is gone |
| `429` + `retry_after` | the send attempt | you are going too fast |
| **`my_chat_member`** | webhook | someone blocked or unblocked the bot, **as it happens** |
| `message` | webhook | someone replied |

**Telegram Message Log** records both directions in one place:

- **Outgoing** — every send, whether it went through the queue or was sent inline by
  another app calling `api.send_message`. Stores the message id on success and the reason
  on failure, linked back to the Outbox row, the Broadcast, and the source document.
- **Incoming** — what people send back. Off by default; turn on **Save Incoming Messages**
  on the Telegram Settings record. When on, each message is stored with its text, a label
  for any attachment, the sender's name and username, and the complete raw Telegram
  payload — an inbound message can carry photos, documents, locations or contacts, and
  keeping the payload means none of that is lost to a guess made in advance.

Nothing is notified about incoming messages; it is a record, not an inbox.

**Telegram Outbox** stays what it was: the queue. A row is Queued, Sending, Sent, Failed or
Cancelled with its attempt count. The log is the history; the outbox is the work list.

Set **Delete Logs After (Days)** on Telegram Settings (90 by default, 0 keeps forever) —
a daily job prunes anything older, because a log table otherwise only grows.

### Blocking

With **Stop Sending to Blocked Chats** on (the default), a `my_chat_member` update marks
the recipient's **Chat Status** as Blocked the moment it happens. After that:

- `api.send_message` refuses rather than attempting the send,
- `send_bulk` drops those recipients and reports how many under `skipped` — one person
  blocking should not stop a run of 250,
- the queue skips their rows instead of spending attempts to be told 403.

If they unblock the bot, the same update sets Chat Status back to Active on its own.

## The "Send To Telegram" menu

This app adds a **Send To Telegram** item to every form's menu. It goes through the same
path as everything else — `send_to_telegram` delegates to `api.send_message` — so those
sends land in Telegram Message Log, respect a blocked recipient, and get split if they run
past 4096 characters. Telegram Notification uses the same entry point, so event-driven
alerts behave identically.

Attaching the document sends a **real PDF**. Two things get in the way of that on a normal
bench, and both are handled here:

- `frappe.attach_print` silently returns HTML when **Send Print as PDF** is off in Print
  Settings, and an `.html` attachment is useless in a chat. This app renders the PDF
  directly instead of asking.
- The PDF renderer fetches stylesheets over HTTP using `frappe.utils.get_url()`, which
  outside an HTTP request resolves to `http://<site name>` and fails with
  `HostNotFoundError`. Rendering pins the host to the site's real domain for the duration.

The same reasoning applies to the "See the document at ..." link: it is built from
`host_name`, or the first entry in `domains` in site config, so a notification sent by the
scheduler links somewhere the recipient can actually open.

A notification that fails is logged and skipped, never raised. These run from `doc_events`
on every doctype, so one unreachable recipient must not be able to stop a business document
from being saved.

## Doctypes

| DocType | Purpose |
|---|---|
| Telegram Settings | One bot: token, webhook, polling toggle, sending rate |
| Telegram User Settings | One recipient: party, payload, deep link, QR, chat id |
| Telegram Outbox | The sending queue, one row per message |
| Telegram Broadcast | Groups a bulk send and tracks its progress |
| Telegram Message Log | History of everything sent and received |
| Telegram Notification | Event-driven alerts (from the original app) |
| SMS Notification, Date Notification | Unrelated extras carried over from the original app |

## Scheduler

| Schedule | Job | What it does |
|---|---|---|
| every minute | `reflection_telegram.outbox.process` | Drains due Outbox rows. Overlapping runs take a lock and skip. |
| every 5 minutes | `reflection_telegram.webhook.poll` | Only for bots with polling on and no webhook registered. |
| daily | `reflection_telegram.message_log.purge` | Prunes log rows past the retention window. |

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
