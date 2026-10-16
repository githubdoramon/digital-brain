ALTER TABLE conversation_messages
    ADD COLUMN IF NOT EXISTS content_tsv TSVECTOR;

ALTER TABLE conversation_threads
    ADD COLUMN IF NOT EXISTS title_tsv TSVECTOR;

CREATE OR REPLACE FUNCTION conversation_messages_tsv_update() RETURNS trigger AS $$
BEGIN
    NEW.content_tsv := to_tsvector(
        'simple',
        unaccent(COALESCE(NEW.content, ''))
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS conversation_messages_tsv_trg ON conversation_messages;
CREATE TRIGGER conversation_messages_tsv_trg
BEFORE INSERT OR UPDATE OF content ON conversation_messages
FOR EACH ROW EXECUTE FUNCTION conversation_messages_tsv_update();

CREATE OR REPLACE FUNCTION conversation_threads_title_tsv_update() RETURNS trigger AS $$
BEGIN
    NEW.title_tsv := to_tsvector(
        'simple',
        unaccent(COALESCE(NEW.title, ''))
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS conversation_threads_title_tsv_trg ON conversation_threads;
CREATE TRIGGER conversation_threads_title_tsv_trg
BEFORE INSERT OR UPDATE OF title ON conversation_threads
FOR EACH ROW EXECUTE FUNCTION conversation_threads_title_tsv_update();

UPDATE conversation_messages
SET content_tsv = to_tsvector('simple', unaccent(COALESCE(content, '')))
WHERE content_tsv IS NULL;

UPDATE conversation_threads
SET title_tsv = to_tsvector('simple', unaccent(COALESCE(title, '')))
WHERE title_tsv IS NULL;

ALTER TABLE conversation_messages
    ALTER COLUMN content_tsv SET NOT NULL;

ALTER TABLE conversation_threads
    ALTER COLUMN title_tsv SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_messages_content_tsv
    ON conversation_messages USING GIN (content_tsv);

CREATE INDEX IF NOT EXISTS idx_conversation_threads_title_tsv
    ON conversation_threads USING GIN (title_tsv);
