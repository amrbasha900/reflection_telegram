# Reflection Telegram — API reference

Everything in `reflection_telegram.api` is whitelisted, so each function works three ways:

```python
# 1. direct import (same site, server side)
from reflection_telegram import api
api.send_message(telegram_user="...", message="...")
```

```python
# 2. frappe.call
frappe.call("reflection_telegram.api.send_message", telegram_user="...", message="...")
```

```js
// 3. over HTTP / from client scripts
frappe.call({
    method: "reflection_telegram.api.send_message",
    args: { telegram_user: "...", message: "..." },
});
```

A recipient is identified either by its **Telegram User Settings** name (`telegram_user`) or by
the business record it belongs to (`party_type` + `party`). Use whichever the calling code
already has.

**Permissions (v2.1.0).** Every whitelisted function checks the *calling* user:

| Call | Requires |
|---|---|
| a `reference_doctype` / `reference_name` | read on that document (and print when `attach_print`) |
| a `file_url` | the File record exists and the user can read it (private files: its owner or readers of the document it is attached to) |
| neither | read on the recipient's Telegram User Settings |
| `send_bulk` | create on Telegram Broadcast (or Telegram Outbox with `create_broadcast=0`), plus the checks above per message |
| `send_to_party`, `resolve_party`, `ensure_link` | read on the party record |
| `get_status` | read on the Telegram User Settings record |

Server code configured by an administrator (Telegram Notification) calls `api.deliver`, which skips
those checks and is not whitelisted. The chat-link fields of Telegram User Settings (chat id, link
status) are set only by the linking flow or a System Manager.

## Strict delivery (`reflection_telegram.strict`, server-side only)

For one private PDF per person where a duplicate matters (Report Distribution Center in Al-Dallala).
Not whitelisted; the calling app authorises its user first.

| Function | Purpose |
|---|---|
| `enqueue(idempotency_key, telegram_user, file, file_sha256, reference_doctype, reference_name, external_reference=None, caption=None, file_name=None)` | Queue; idempotent per key (`created: false` on replay, `IDEMPOTENCY_CONFLICT` for a different message) |
| `status(keys)` | Rows by key: status, error code, message ids, review fields |
| `cancel(keys, reason)` | Cancel rows still Queued |
| `resolve_review(key, action, reason, confirm_duplicate_risk=0, user=None)` | `confirm_sent`, `confirm_unsent`, `resend` (duplicate risk acknowledged); audited |

Rules: private chats only (not a group, positive chat id, not blocked); the File must be private,
attached to the reference document, and match its SHA-256 when queued and again when sent; one
guarded claim per row; the attempt marker is committed before Telegram is called. Telegram's answer
decides: message id → **Sent** (accepted — Telegram gives no delivery or read receipt); 429 → back in
the queue after `retry_after`, attempt not counted; a refusal (BadRequest, Forbidden, bad token) →
**Failed**; timeout, lost connection or anything unexpected → **Needs Review**, never resent
automatically. A worker that died before the marker leaves the row requeued, after it Needs Review.
Strict rows share the bot's per-minute rate with the legacy queue (legacy first).

Dispatch guard (v2.1.1), for apps that must authorise each send at the last moment:
`telegram_strict_dispatch_guards = ["app.module.fn"]`, called with `outbox`, `reference_doctype`, `reference_name`,
`external_reference` immediately before the attempt marker on every attempt (first send, review resend, retry). A guard
that raises fails the row unsent and final (`REVOKED_<code>`); it is never retried automatically.

Outcome hook, for apps that mirror the state: `telegram_strict_delivery_listeners = ["app.module.fn"]`
in the app's `hooks.py`; called after commit with `outbox`, `idempotency_key`, `external_reference`,
`status`.

`BadRequest` is now classified as a permanent failure for every caller (python-telegram-bot makes it
a subclass of `NetworkError`, so it used to be retried).

---

## send_message

Send one message to one linked recipient.

```python
api.send_message(
    telegram_user,            # str   name of a Telegram User Settings record
    message=None,             # str   the text
    file_url=None,            # str   Frappe file URL to send as a document
    parse_mode=None,          # str   "HTML" | "MarkdownV2" | None (plain)
    reference_doctype=None,   # str   recorded against the message
    reference_name=None,      # str
    attach_print=0,           # int   render the reference document as a PDF
    print_format=None,        # str   which print format, defaults to the doctype's
    queue=0,                  # int   queue instead of sending inline
)
```

Returns `{"status": "Sent", "message_ids": [...], "log": "<name>"}`, or
`{"status": "Queued", "outbox": "<name>"}` when `queue=1`.

`message_ids` are Telegram's own ids. They confirm Telegram **accepted** the message —
Telegram gives bots no delivery or read receipt beyond that.

Raises if the recipient has no chat id yet (the QR has not been scanned), or if they have
blocked the bot and **Stop Sending to Blocked Chats** is on.

Every send is written to **Telegram Message Log**, success or failure, including sends made
inline by other apps.

**Text longer than 4096 characters is split across several messages** rather than truncated,
because Telegram rejects anything over that limit outright.

Pass `queue=1` from inside a web request so the caller does not wait on Telegram's network
round trip.

### Examples

```python
# plain text
api.send_message(telegram_user="2012381-Send Agri Statement", message="Statement ready")

# an existing attachment
api.send_message(
    telegram_user="2012381-Send Agri Statement",
    message="Signed contract",
    file_url="/private/files/contract.pdf",
)

# a PDF rendered from a document
api.send_message(
    telegram_user="2012381-Send Agri Statement",
    message="Your invoice",
    reference_doctype="Sales Invoice",
    reference_name="ACC-SINV-2026-00001",
    attach_print=1,
    print_format="Agri Statement",
)

# HTML formatting
api.send_message(
    telegram_user="2012381-Send Agri Statement",
    message="<b>Balance:</b> 5,432 SAR",
    parse_mode="HTML",
)
```

---

## send_to_party

Same as `send_message`, but addressed by business record.

```python
api.send_to_party(
    party_type,               # "Supplier" | "Customer" | "Employee" | "Contact" | "User"
    party,                    # the record name, e.g. "2012381"
    message=None,
    telegram_settings=None,   # which bot, if the party is linked to more than one
    **kwargs,                 # anything send_message accepts
)
```

Raises `frappe.DoesNotExistError` when the party is not linked yet.

```python
api.send_to_party("Supplier", "2012381", message="Your statement is ready")
```

---

## send_bulk

Queue many messages, paced so Telegram does not restrict the bot. **Nothing is sent inline.**

```python
api.send_bulk(
    messages,                 # list[dict] — each needs telegram_user, plus any send_message arg
    telegram_settings=None,   # inferred from the first recipient when omitted
    title=None,               # label for the Telegram Broadcast record
    rate=0,                   # messages per minute for this run; 0 uses the settings default
    create_broadcast=1,       # set 0 to queue without a tracking record
)
```

Returns `{"broadcast": "<name>", "queued": <count>, "skipped": <count>}`.

Every recipient must already be linked. If any are not, the call raises before queueing
anything, naming how many and the first offender — a partial bulk send is worse than none.

Recipients who have since **blocked the bot** are a different case: they are dropped and
counted under `skipped` rather than raising, because one person blocking should not stop a
run of 250.

```python
recipients = frappe.get_all(
    "Telegram User Settings",
    filters={"party": "Supplier", "telegram_chat_id": ["is", "set"]},
    pluck="name",
)

api.send_bulk(
    messages=[
        {"telegram_user": name, "message": build_statement(name)}
        for name in recipients
    ],
    title="August statements",
    rate=20,
)
```

### Pacing

At `rate` messages/minute, row *n* is scheduled at `now + n × (60/rate)` seconds. The
scheduler job runs every minute, takes up to `rate` due rows, and pauses `60/rate` seconds
between each send — so the rate holds both across minutes and within one.

| rate | 250 messages take |
|---|---|
| 20/min (default) | ~12.5 minutes |
| 60/min | ~4 minutes |

Watch progress on the **Telegram Broadcast** record. **Cancel Pending** on it stops everything
that has not gone out; already-sent messages cannot be recalled.

---

## ensure_link

Get — or create — the linking record for a party, QR code included.

```python
api.ensure_link(
    party_type,
    party,
    telegram_settings,
    is_group_chat=0,
)
```

Returns the same shape as `get_status`. Idempotent: calling it for an already-linked party
returns the existing record untouched rather than rotating a payload that works.

This is what the **Telegram QR Codes** page calls when you press **Generate QR**.

---

## get_status

Whether a recipient can receive messages, and the QR link if not.

```python
api.get_status(telegram_user)
```

```json
{
  "telegram_user": "2012381-Send Agri Statement",
  "party_type": "Supplier",
  "party": "2012381",
  "linked": true,
  "chat_id": "936289007",
  "linked_on": "2026-08-19 17:20:14",
  "deep_link": "https://t.me/Amrbasha_bot?start=ccb26374...",
  "qr_code": "/files/telegram-qr-2012381.png"
}
```

---

## resolve_party

The linking record name for a business record, or `None`.

```python
api.resolve_party(party_type, party, telegram_settings=None)
```

Only returns records that are actually linked, so a non-`None` result is safe to send to.

---

# Other modules

These are internal, but useful to know about.

## reflection_telegram.webhook

| Function | Purpose |
|---|---|
| `register(telegram_settings)` | Point the bot at this site; stores the shared secret |
| `unregister(telegram_settings)` | Remove the webhook |
| `status(telegram_settings)` | What Telegram itself reports: bot username, webhook URL, pending updates, last error |
| `receive()` | The public callback. Guest endpoint, secret-header authenticated |
| `poll()` | Scheduler fallback for bots with polling on and no webhook |

## reflection_telegram.outbox

| Function | Purpose |
|---|---|
| `enqueue(messages, telegram_settings, broadcast, rate)` | Write queue rows with staggered `scheduled_at` |
| `process()` | Scheduler entry point, once a minute |
| `get_rate(telegram_settings, override)` | Effective messages/minute |

## reflection_telegram.onboarding

| Function | Purpose |
|---|---|
| `build_payload()` | A 32-character deep-link-safe token |
| `deep_link(bot_name, payload, is_group_chat)` | `?start=` or `?startgroup=` URL |
| `qr_png(data)` | QR image bytes |
| `normalise_payload(text)` | Reduce `/start abc`, `/abc` or `abc` to `abc` |
| `find_by_payload(payload, telegram_settings)` | The record a scan belongs to |

## reflection_telegram.telegram_client

The only module that talks to the Telegram API. Raises two exception types, and everything
upstream branches on which one it got:

- `PermanentError` — bad token, blocked bot, unknown chat. Retrying will not help.
- `TransientError` — network blips and rate limits. Carries `retry_after` when Telegram
  supplied one.

---

# Errors you may hit

| Message | Cause | Fix |
|---|---|---|
| `'Bot' object does not support the asynchronous context manager protocol` | python-telegram-bot older than v20 | `./env/bin/pip install --upgrade 'python-telegram-bot>=21'`, then restart |
| `module 'telegram.error' has no attribute 'Forbidden'` | Same cause, surfacing while handling another error | Same fix |
| `The bot token ... is incomplete` | Only the secret half of the token was pasted | Copy the whole `<id>:<secret>` from BotFather |
| `InvalidToken: Not Found` | Telegram rejected the token | Same as above, or the bot was deleted |
| `... has no chat id yet` | The QR has not been scanned | Send the recipient their QR card |
| `Wrong response from the webhook: 403` | Secret mismatch | Press **Register Webhook** again |
| Nothing links, no errors | A webhook is registered but the site is unreachable | **Check Bot** shows Telegram's last error |


---

# Message status, honestly

There is **no delivery or read receipt** in the Telegram Bot API. None of the 25 update
types a bot can subscribe to reports the state of a message you sent. Do not build
"delivered" or "seen" on top of this integration — the data does not exist.

What you can rely on:

| You know | How |
|---|---|
| Telegram accepted the message | `message_ids` in the `send_message` return, and the `message_id` column in Telegram Message Log |
| The send failed, and why | `status = Failed` plus `error` in the log |
| The user blocked the bot | `my_chat_member` webhook → **Chat Status** on Telegram User Settings, within seconds |
| The user replied | an Incoming row in the log, if **Save Incoming Messages** is on |

## reflection_telegram.message_log

| Function | Purpose |
|---|---|
| `record_outgoing(...)` | Log a send. Never raises — logging must not break sending |
| `record_incoming(telegram_settings, update, telegram_user)` | Log an inbound message, if the bot is configured to keep them |
| `find_user_by_chat(telegram_settings, chat_id)` | The linking record for a chat id |
| `purge()` | Daily retention cleanup |
