# External Automations

This document tracks the automations being brought back into the Digital Brain
repository after the n8n configuration was lost. Existing endpoints and agents
are evidence of supported capabilities; they do not prove that a particular
historical n8n workflow used them.

## Scope and Status

| Automation | Intended behavior | Repository status |
| --- | --- | --- |
| Google Calendar sync | Sync selected Google Calendar occurrences, retain cancellations, and reconcile regularly. | Implemented as the local `google_calendar_sync` worker. It polls every five minutes for the recent two weeks and upcoming two months, and reconciles the recent and upcoming two weeks each Sunday at 03:00 Europe/Lisbon. Connect and select calendars in the web frontend at System → Calendar Sync. |
| Microsoft 365 Calendar sync | Sync selected Microsoft calendar occurrences, retain cancellations, and reconcile regularly. | Implemented as the local `microsoft_calendar_sync` worker with the same polling windows and weekly reconciliation schedule as Google Calendar. Connect and select calendars in the web frontend at System → Calendar Sync. |
| Email document ingestion | Monitor email and ingest a document when it is addressed to the designated destination email. | Not implemented. The destination address, mail provider, attachment selection rules, and handling of forwarded/replied messages need to be recovered before implementation. |
| Emergency stock | Check stock and expiry each day at 05:00, update the Google Sheet and Bring list, and notify about actions. | Implemented as the local `emergency_stock_daily` worker, scheduled for 05:00 Europe/Lisbon. Jobs are deduplicated by local date and timezone in `async_jobs`, and failures retry after five minutes. `GET /agents/emergency-stock/run` remains available for manual or external triggering. |
| Encrypted database dump | Produce a database dump every day at midnight, encrypt it, and upload it to S3. | Implemented as the local `database_backup_daily` orchestrator worker, scheduled for 00:00 Europe/Lisbon. It uses the existing `backend/.env`, the runtime AWS role, and the bucket lifecycle rule for retention. |

## Emergency Stock Worker

The orchestrator starts `emergency_stock_jobs.py` with its other background
workers. It checks once per minute whether 05:00 Europe/Lisbon has passed,
enqueues one job for that local calendar date, then claims due work from the
shared `async_jobs` queue. The unique `(job_type, user_email, dedupe_key)` key
prevents a second successful run for that date; pending and failed jobs remain
eligible for the existing retry flow. The status is exposed by the authenticated
`GET /system/jobs` endpoint.

## Google Calendar Sync

The web frontend provides a separate Google OAuth connection using the same
Google account as Digital Brain sign-in and read-only Calendar access. It checks
that the authorized Google email matches the signed-in account, then sends the
refresh token directly from the frontend server to the authenticated backend.
The backend encrypts the token with Fernet before storing it in
`google_calendar_connections`; access and refresh tokens are never returned to
the browser. Selected calendar IDs are stored with the connection.

Configure `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET`, and
`GOOGLE_CALENDAR_TOKEN_ENCRYPTION_KEY` in the existing `backend/.env`. The OAuth
client values must match the frontend's existing Google OAuth client. Add
`https://YOUR_FRONTEND_HOST/api/google-calendar/callback` as an authorized
redirect URI for that client, and enable the Google Calendar API in its Cloud
project. The frontend uses `NEXTAUTH_URL` as the public callback origin and
requires a public HTTPS domain in production; do not use a bind address such as
`0.0.0.0`. For local development, use `http://localhost:3000`. The connection
requests only the calendar-list-readonly and events-readonly scopes. Generate a
Fernet key once with:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Keep the key stable and backed up; changing or losing it prevents the backend
from decrypting the stored Calendar refresh token. The frontend server also
needs its existing `BACKEND_API_BASE` configured to reach the orchestrator.

The worker expands recurring events into occurrences and polls every five
minutes over the prior 14 days through the next 60 days. Google event IDs are
stored with their calendar ID so multiple selected calendars remain distinct.
Calendar title, times, attendees, and location metadata follow Google. A
changed Calendar description is appended to the existing Digital Brain summary
as a replaceable “Google Calendar description” section, preserving transcript
summaries and notes. Canceled or missing occurrences are retained with
`is_cancelled = true`; future meeting searches and daily briefings omit them,
while event history and details retain the records, transcripts, and linked
todos. The weekly reconciliation probes locally stored events missing from the
Google list to distinguish a moved event from a deleted one.

Connection status, worker health, latest sync and weekly reconciliation times,
and the most recent safe error are available in the frontend and `GET
/system/jobs`.

Google API and token-refresh failures surface only the HTTP status and a bounded
provider error code (for example, `accessNotConfigured` or `invalid_grant`) in
the frontend and orchestrator logs; provider response bodies and credentials
are never exposed.

## Microsoft 365 Calendar Sync

The frontend uses a separate Microsoft Entra web app registration and delegated
OAuth authorization. The connection belongs to the signed-in Digital Brain
user, while the connected Microsoft mailbox address may differ. Refresh tokens
are sent server-to-server and encrypted by the orchestrator in
`microsoft_calendar_connections`; they are never returned to the browser.
Calendar IDs are selected in the web frontend. The worker polls every five
minutes for the prior 14 days through the next 60 days and reconciles the prior
and next 14 days each Sunday at 03:00 Europe/Lisbon. Canceled meetings remain
in history with related records, are hidden from upcoming views and briefings,
and the Microsoft Calendar description is appended as a replaceable section in
the existing Digital Brain summary.

Configure `MICROSOFT_CALENDAR_CLIENT_ID`, `MICROSOFT_CALENDAR_CLIENT_SECRET`,
and `MICROSOFT_CALENDAR_TOKEN_ENCRYPTION_KEY` in the existing `backend/.env`.
The OAuth flow uses the `organizations` authority, so the Entra app registration
must support accounts in any organizational directory. The account picker lets
the user choose the connected work or school account. Configure the matching
Application (client) ID and client secret Value in `frontend/web/.env`; the ID
is a GUID and is distinct from the secret Value. Add
`https://YOUR_FRONTEND_HOST/api/microsoft-calendar/callback` as a Web redirect
URI in the Entra app registration. Grant delegated `User.Read` and
`Calendars.Read`; the tenant may require administrator consent. Generate a
Fernet key once and keep it stable and backed up. The frontend server must also
have `BACKEND_API_BASE` configured to reach the orchestrator.

The worker calls the existing emergency-stock executor. It uses
`EMERGENCY_STOCK_SHEET_ID` and the Google service-account configuration to read
and update stock data, and uses the existing Bring integration and notification
configuration for resulting actions. The existing service-key route runs the
same executor directly when an external/manual trigger is needed.

## Encrypted Database Backup

The orchestrator starts `database_backup_jobs.py` with its other background
workers. It checks once per minute whether midnight Europe/Lisbon has passed,
enqueues one job for that local calendar date, then claims due work from the
shared `async_jobs` queue. Jobs are deduplicated by local date and timezone;
failures retry after five minutes. It waits to enqueue backups until the
passphrase, bucket, and database password are configured. The worker and its
configuration readiness are listed by the authenticated `GET /system/jobs`
endpoint.

Configure `DB_BACKUP_PASSPHRASE`, `DB_BACKUP_S3_BUCKET`, and
`DB_BACKUP_S3_PREFIX`, and `AWS_DEFAULT_REGION` in the existing `backend/.env`.
`AWS_REGION` is also honored when present. PostgreSQL connection settings come
from the existing `POSTGRES_HOST`, `POSTGRES_PORT`,
`POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`; optional
`DB_BACKUP_POSTGRES_USER` and `DB_BACKUP_POSTGRES_DB` override the database user
and name. `docker-compose.yml` already provides `backend/.env` to the
orchestrator. The orchestrator image includes the PostgreSQL 16 client to match
the Compose database server.

The worker creates a custom-format dump, compresses it, and encrypts it using
AES-256-CBC with PBKDF2 and salt. Only the encrypted artifact is written to a
temporary file; it is removed after upload or failure. The fixed S3 object key is
`backup.sql.gz.enc` when the prefix is empty, or `<prefix>/backup.sql.gz.enc`
when configured; every run replaces that object. Despite the requested `.sql`
filename, the contents remain a PostgreSQL custom-format archive matching the
current manual command, so restore it with `pg_restore`. Boto3 uses the AWS SDK
credential chain; give the orchestrator's instance or task role permission to
upload to the bucket. No access-key variables are required. Configure retention
with the bucket's existing S3 lifecycle policy.

To restore, download the `.dump.gz.enc` object, decrypt and decompress it, then
restore the custom-format dump into a prepared database:

```bash
aws s3 cp s3://BUCKET/PREFIX/backup.sql.gz.enc - \
  | openssl enc -d -aes-256-cbc -pbkdf2 -pass env:DB_BACKUP_PASSPHRASE \
  | gunzip \
  | pg_restore --clean --if-exists -U DB_USER -d DB_NAME
```

Set `DB_BACKUP_PASSPHRASE` in the restore shell before running the pipeline.
Keep the passphrase in a secure secret store independently of the bucket; losing
it makes client-side encrypted dumps unrecoverable.

## Service Authentication

External service calls use `x-service-api-key`, validated against
`ORCHESTRATOR_API_KEY`. Current orchestrator service-key endpoints and the
separate Robot Gateway routes are implemented in their route modules; this
document records the desired automation scope, not a credential inventory.
