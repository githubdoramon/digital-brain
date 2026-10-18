-- Provider scope is part of every account, identity, and archive key.
ALTER TABLE messaging_accounts DROP CONSTRAINT messaging_accounts_provider_check;
ALTER TABLE messaging_accounts ADD CONSTRAINT messaging_accounts_provider_check
    CHECK (provider IN ('whatsapp', 'telegram'));
ALTER TABLE messaging_conversations DROP CONSTRAINT messaging_conversations_kind_check;
ALTER TABLE messaging_conversations ADD CONSTRAINT messaging_conversations_kind_check
    CHECK (kind IN ('direct', 'group', 'channel'));
-- Telegram history is paged independently from live delivery; advance only after staging.
CREATE TABLE messaging_sync_cursors (
    account_id TEXT NOT NULL REFERENCES messaging_accounts ON DELETE CASCADE,
    external_chat_id TEXT NOT NULL,
    oldest_id BIGINT NOT NULL DEFAULT 0,
    newest_id BIGINT NOT NULL DEFAULT 0,
    backfill_done BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (account_id, external_chat_id)
);
