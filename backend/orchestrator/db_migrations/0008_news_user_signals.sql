CREATE TABLE IF NOT EXISTS news_user_interactions (
    interaction_id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    event_type TEXT NOT NULL,
    briefing_id TEXT,
    briefing_item_id TEXT,
    cluster_id TEXT,
    topic_label TEXT,
    source TEXT,
    source_domain TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT news_user_interactions_event_type_check
        CHECK (event_type IN ('article_opened', 'article_feedback_up', 'article_feedback_down'))
);

CREATE INDEX IF NOT EXISTS idx_news_user_interactions_user_time
    ON news_user_interactions (user_email, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_user_interactions_cluster
    ON news_user_interactions (cluster_id, created_at DESC);

CREATE TABLE IF NOT EXISTS news_user_profiles (
    user_email TEXT PRIMARY KEY,
    topic_weights JSONB NOT NULL DEFAULT '{}'::JSONB,
    source_weights JSONB NOT NULL DEFAULT '{}'::JSONB,
    last_interaction_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
