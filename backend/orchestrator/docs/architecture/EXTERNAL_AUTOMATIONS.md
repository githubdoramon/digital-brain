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
| Encrypted database dump | Produce a database dump every day at midnight and encrypt it. | Not implemented. Backup destination, retention, encryption/key management, and restore procedure remain to be specified. |

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

## Service Authentication

External service calls use `x-service-api-key`, validated against
`ORCHESTRATOR_API_KEY`. Current orchestrator service-key endpoints and the
separate Robot Gateway routes are implemented in their route modules; this
document records the desired automation scope, not a credential inventory.
