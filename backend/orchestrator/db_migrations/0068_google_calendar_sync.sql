ALTER TABLE events
  ADD COLUMN IF NOT EXISTS is_cancelled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS google_calendar_connections (
  user_email TEXT PRIMARY KEY,
  refresh_token_encrypted TEXT NOT NULL,
  selected_calendar_ids TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
  connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_sync_at TIMESTAMPTZ,
  last_reconciliation_at TIMESTAMPTZ,
  last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_google_calendar_connections_sync
  ON google_calendar_connections (last_sync_at);
