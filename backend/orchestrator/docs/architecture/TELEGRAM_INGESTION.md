# Telegram personal archive

Status: implemented locally on 2026-10-17; no live account linked or production
rebuild performed. This is the personal-account messaging connector, independent
of the retired Telegram bot/photo-to-Immich webhook. The retired hook remains removed.

## Access and dependencies

Telegram provides an official user-account API (MTProto), including QR login and
history retrieval. The Bot API does not grant access to a user's existing inbox.
The connector uses pinned `Telethon==1.45.0`, a third-party Python client for that
official API. Telethon is not an official Telegram SDK. The official TDLib is an
alternative, but requires a native library; Telethon fits the existing Python
container without another service or native build dependency.

Primary references:

- [Telegram APIs](https://core.telegram.org/)
- [Create an application](https://core.telegram.org/api/obtaining_api_id)
- [QR login protocol](https://core.telegram.org/api/qr-login)
- [Telethon authentication](https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.auth.AuthMethods.qr_login)
- [Telethon session storage](https://docs.telethon.dev/en/stable/concepts/sessions.html)
- [Telegram API terms](https://core.telegram.org/api/terms)
- [Content licensing and AI terms](https://telegram.org/tos/content-licensing)

The current terms broadly restrict AI-related use and describe a context-specific
consent exception. Technical API availability does not establish permission for
all downstream uses. The user explicitly requested their personal, noncommercial
memory integration after discussing this distinction. Telegram is therefore
included in the same owner-scoped AI search/inspection path as WhatsApp. No
consent certification, commercial authorization, or platform approval is claimed.

## Configure and connect

1. Register your own application through **API development tools** at
   `https://my.telegram.org`. Keep the resulting API hash private.
2. Add `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` to `backend/.env`, following
   `backend/env.template`. Do not paste credentials into chat or commit them.
3. Rebuild the existing orchestrator to install Telethon; rebuild the frontend
   for the new management page. Startup applies `0074_telegram_archive.sql`
   after the shared archive migration, `0073_messaging_archive.sql`.
4. Open web **System → Messengers → Telegram**, choose **Connect Telegram**, and scan the QR
   using Telegram **Settings → Devices → Link Desktop Device** on your phone.
5. If your account has two-step verification, enter that password in the web
   form. It is submitted over the normal authenticated frontend proxy, held
   transiently in memory, sent to the child over a private pipe, and never written
   to the archive, configuration, logs, or analytics. Login challenges expire
   after five minutes and are account/generation scoped. QR codes expire and rotate.

Management uses the existing `/messaging/*` API with `?provider=telegram`.
WhatsApp remains the default for management requests without a provider. All
routes require the current user's bearer identity via the frontend proxy. The
password route is `POST /messaging/telegram/password`; it checks the current
account, generation and challenge. Status and pairing responses use `no-store`.

**People** supports manual linking/correction/unlinking, contact creation, and
navigation from web Contacts. Telegram numeric user IDs are distinct from phone
numbers and usernames: do not infer a phone or merge names. A contact may link
both its WhatsApp and Telegram identities. **Archive** supports keyword search,
conversation classification, older-message paging, and bounded message context.

## Storage and runtime

`whatsapp_runtime.py` now supervises both providers, retaining its filename for
existing lifecycle references. A PostgreSQL advisory lease and per-session file
lock prevent simultaneous ownership. Enabled accounts must belong to
`auth.ALLOWED_USERS`. Telegram runs as a managed Python child inside the existing
orchestrator container, with parent-death cleanup, process reaping, safe static
failure states, and no native/client output in general logs.

Private session files live at `storage/telegram/<account hash>/session.db.session`
(Telethon adds its own suffix). Compose mounts this directory persistently. It is
excluded from Git and Docker build contexts, created with private permissions,
and must be treated as a full-account credential. Session/entity state is local
SQLite; it is not message-body or attachment storage. No session string or API
credential is exposed through the API. Disconnect stops capture but retains the
session and existing archive, as WhatsApp does; Telegram Devices can revoke the
linked session. The initial runtime assumes one orchestrator worker, because QR
and login challenges are intentionally local, transient state.

`messaging_accounts` is unique per owner/provider. `self_jid` is a legacy internal
column name holding the pinned self identity for either provider. Telegram uses
`<id>@telegram.user`, `<id>@telegram.group`, and `<id>@telegram.channel`; the last
namespace includes megagroups. The provider is validated at the staging boundary.
Relinking a different Telegram account cannot silently mix its archive with the
previous account. Contact links, corrections, classifications, retries, message
identities, and cursors are scoped to the account/provider.

## Capture and history

- Direct cloud chats, basic groups, megagroups, and joined broadcast channels
  are supported. Secret chats are not available through this client. Service-only, unsupported, and disappearing
  content are skipped.
- Incoming messages, edits, and observed deletion events enter a bounded
  100-event lane. Only sanitized text, sender/chat attribution, timestamps, reply
  IDs, and allow-listed attachment filename/type/size enter PostgreSQL staging.
  Files, thumbnails, media references/access hashes and downloads are excluded.
- History loads the newest 100 messages per dialog, then progressively fetches
  older 100-message pages within a rolling six-calendar-month window (UTC,
  inclusive cutoff). Older messages are skipped before sender lookup/projection;
  reaching the boundary marks backfill complete and stops older-page requests.
  Separate oldest/newest cursors persist in
  `messaging_sync_cursors`. Cursors advance only after staging commits, so a crash
  may replay a page without skipping uncommitted data. One older page per dialog
  per pass prevents history from owning the live lane.
- Subsequent passes catch up new messages from the committed newest cursor in
  ascending pages and reconcile the latest 100 messages for missed edits. Passes
  wait 60 seconds after completing the dialog list, with a one-second pause
  between dialogs; this is not a per-dialog sixty-second SLA. Flood waits are
  respected. Very old missed edits, inaccessible/deleted history, unsupported
  content and queue overflow do not have a completeness guarantee.
- Deletion events resolve original sender/chat from staged or archived evidence.
  Chatless deletions apply only to non-channel IDs; a megagroup's matching numeric
  ID is not affected. Telegram does not report every deletion. Source deletion
  markers retain previously archived evidence, following the personal-archive
  model; they cannot recover content never observed.
- `search_memories` searches both providers and `get_message` returns the matched
  exchange under the same owner boundary. The attachment remains in Telegram;
  no attachment interpretation, automatic contact facts, projects, or daily
  intelligence jobs were introduced by this connector milestone.

The shared `messaging_history.py` policy also filters historical records at
staging and when draining an already queued inbox. Out-of-window queued history
is acknowledged and cleared without creating archive messages. Existing archive
rows and live messages are retained; this is not a six-month deletion policy.

## Verification

Focused tests use actual Telethon message types and a disposable PostgreSQL
schema. They cover metadata projection, disappearing-content exclusions and channel/group classification,
account/provider isolation, contact corrections, shared AI retrieval, edit/replay
semantics, deletion namespaces, durable cursors, QR rotation, two-step verification
retry/session reuse, authenticated routes, and history staging-failure recovery.
Run with the pinned dependencies installed:

```sh
MESSAGING_TEST_DSN='host=127.0.0.1 port=55479 user=postgres dbname=postgres' \
  .venv/bin/python -m pytest tests/test_messaging_archive.py -q
```

Live QR approval, real account history/catch-up, reconnect, device rendering,
production migration and deployment remain unverified. The offline tests never
connect to Telegram or send a message.

Validation recorded: 209 targeted backend checks, web TypeScript checking and
focused ESLint pass. These are local/offline checks, not live account proof.

The six-month cutoff change passes 41 messaging tests, including calendar/month-end
boundaries, both-provider staging, old edits, queued history, and Telegram stopping
on the initial or a resumed page.
