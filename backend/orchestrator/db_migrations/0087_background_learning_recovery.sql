-- Durable source batch boundaries survive provider deferrals and process restarts.
ALTER TABLE knowledge_learning ADD COLUMN IF NOT EXISTS batch_limit INTEGER NOT NULL DEFAULT 12
    CHECK (batch_limit BETWEEN 1 AND 12);
-- Only successful, validated tagging results are cached. No source text is retained.
CREATE TABLE IF NOT EXISTS tag_generation_cache (
    input_hash TEXT PRIMARY KEY,
    tags JSONB NOT NULL CHECK (jsonb_typeof(tags) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
