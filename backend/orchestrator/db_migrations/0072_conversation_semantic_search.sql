-- Durable per-message indexing work. Revision checks prevent stale workers from
-- publishing after edits; both work and chunks disappear with their message.
CREATE TABLE IF NOT EXISTS conversation_message_index (
    message_id BIGINT PRIMARY KEY REFERENCES conversation_messages(message_id) ON DELETE CASCADE,
    revision BIGINT NOT NULL DEFAULT 1,
    indexed_revision BIGINT NOT NULL DEFAULT 0,
    index_version TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lease_until TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversation_chunks (
    message_id BIGINT NOT NULL REFERENCES conversation_messages(message_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    chunk_embed VECTOR(768) NOT NULL,
    PRIMARY KEY (message_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_conversation_index_due
    ON conversation_message_index (next_run_at)
    WHERE indexed_revision <> revision;

CREATE OR REPLACE FUNCTION queue_conversation_message_index() RETURNS trigger AS $$
BEGIN
    INSERT INTO conversation_message_index (message_id)
    VALUES (NEW.message_id)
    ON CONFLICT (message_id) DO UPDATE SET
        revision = conversation_message_index.revision + 1,
        attempts = 0,
        next_run_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS conversation_message_index_trg ON conversation_messages;
CREATE TRIGGER conversation_message_index_trg
AFTER INSERT OR UPDATE OF content, role, metadata ON conversation_messages
FOR EACH ROW EXECUTE FUNCTION queue_conversation_message_index();

CREATE OR REPLACE FUNCTION queue_conversation_title_index() RETURNS trigger AS $$
BEGIN
    INSERT INTO conversation_message_index (message_id)
    SELECT message_id FROM conversation_messages WHERE thread_id = NEW.id
    ON CONFLICT (message_id) DO UPDATE SET
        revision = conversation_message_index.revision + 1,
        attempts = 0,
        next_run_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS conversation_title_index_trg ON conversation_threads;
CREATE TRIGGER conversation_title_index_trg
AFTER UPDATE OF title ON conversation_threads
FOR EACH ROW WHEN (OLD.title IS DISTINCT FROM NEW.title)
EXECUTE FUNCTION queue_conversation_title_index();

INSERT INTO conversation_message_index (message_id)
SELECT message_id FROM conversation_messages
ON CONFLICT (message_id) DO NOTHING;
