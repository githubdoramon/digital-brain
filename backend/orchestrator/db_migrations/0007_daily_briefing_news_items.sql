CREATE TABLE IF NOT EXISTS daily_briefing_news_items (
    briefing_item_id TEXT PRIMARY KEY,
    briefing_id TEXT NOT NULL,
    user_email TEXT NOT NULL,
    briefing_date DATE NOT NULL,
    timezone TEXT NOT NULL,
    cluster_id TEXT,
    title TEXT NOT NULL,
    url TEXT,
    source TEXT NOT NULL,
    source_domain TEXT,
    section TEXT NOT NULL,
    topic_label TEXT,
    rank INTEGER NOT NULL,
    score DOUBLE PRECISION,
    brief_summary TEXT,
    topic_matches TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT daily_briefing_news_items_section_check
        CHECK (section IN ('topic', 'general'))
);

CREATE INDEX IF NOT EXISTS idx_daily_briefing_news_items_briefing
    ON daily_briefing_news_items (briefing_id, rank);

CREATE INDEX IF NOT EXISTS idx_daily_briefing_news_items_user_date
    ON daily_briefing_news_items (user_email, briefing_date DESC, timezone);

CREATE INDEX IF NOT EXISTS idx_daily_briefing_news_items_cluster
    ON daily_briefing_news_items (cluster_id, created_at DESC);
