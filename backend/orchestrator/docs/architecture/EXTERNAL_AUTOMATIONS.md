# External Automations

This document tracks the automations being brought back into the Digital Brain
repository after the n8n configuration was lost. Existing endpoints and agents
are evidence of supported capabilities; they do not prove that a particular
historical n8n workflow used them.

## Scope and Status

| Automation | Intended behavior | Repository status |
| --- | --- | --- |
| Google Calendar sync | Sync calendar meetings into Digital Brain and apply later updates. | External meeting ingest supports Google IDs through `POST /ingest/event/external`. A calendar poller/change-feed sync is not currently present. |
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
`DB_BACKUP_S3_PREFIX` in the existing `backend/.env`. The optional
`DB_BACKUP_AWS_REGION` overrides the AWS SDK's default region. PostgreSQL
connection settings come from the existing `POSTGRES_HOST`, `POSTGRES_PORT`,
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
