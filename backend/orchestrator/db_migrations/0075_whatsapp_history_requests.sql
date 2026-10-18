CREATE TABLE whatsapp_history_cursors (
    account_id TEXT NOT NULL REFERENCES messaging_accounts ON DELETE CASCADE,
    external_chat_id TEXT NOT NULL,
    oldest_message_id TEXT NOT NULL,
    oldest_from_me BOOLEAN NOT NULL,
    oldest_timestamp_ms BIGINT NOT NULL CHECK (oldest_timestamp_ms > 0),
    seen_anchor_ids TEXT[] NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'ready' CHECK (state IN ('ready', 'waiting', 'complete', 'stalled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    request_token TEXT,
    request_id TEXT,
    reason TEXT,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (account_id, external_chat_id)
);
CREATE INDEX whatsapp_history_due ON whatsapp_history_cursors(account_id, next_attempt_at)
    WHERE state IN ('ready', 'waiting');
