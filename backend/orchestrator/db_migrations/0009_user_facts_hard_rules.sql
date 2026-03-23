ALTER TABLE IF EXISTS user_facts
    ADD COLUMN IF NOT EXISTS fact_mode TEXT NOT NULL DEFAULT 'soft';

ALTER TABLE IF EXISTS user_facts
    ADD COLUMN IF NOT EXISTS rule_type TEXT;

ALTER TABLE IF EXISTS user_facts
    ADD COLUMN IF NOT EXISTS rule_scope TEXT[] NOT NULL DEFAULT '{}'::TEXT[];

ALTER TABLE IF EXISTS user_facts
    ADD COLUMN IF NOT EXISTS rule_payload JSONB NOT NULL DEFAULT '{}'::JSONB;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = current_schema()
          AND table_name = 'user_facts'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'user_facts_fact_mode_check'
        ) THEN
            ALTER TABLE user_facts
                ADD CONSTRAINT user_facts_fact_mode_check
                CHECK (fact_mode IN ('soft', 'hard_rule'));
        END IF;
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_user_facts_mode_scope
    ON user_facts (user_email, fact_mode, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_facts_rule_scope
    ON user_facts USING GIN (rule_scope)
    WHERE fact_mode = 'hard_rule';
