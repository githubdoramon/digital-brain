CREATE TABLE IF NOT EXISTS news_story_clusters (
    cluster_id TEXT PRIMARY KEY,
    story_fingerprint TEXT NOT NULL UNIQUE,
    canonical_title TEXT NOT NULL,
    canonical_url TEXT,
    source_domain TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mention_count BIGINT NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS idx_news_story_clusters_last_seen
    ON news_story_clusters (last_seen_at DESC);

CREATE TABLE IF NOT EXISTS news_story_mentions (
    mention_id TEXT PRIMARY KEY,
    cluster_id TEXT NOT NULL REFERENCES news_story_clusters(cluster_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    source TEXT NOT NULL,
    source_domain TEXT,
    article_url TEXT,
    canonical_url TEXT,
    title TEXT NOT NULL,
    summary TEXT,
    topic_matches TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    published_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS idx_news_story_mentions_cluster
    ON news_story_mentions (cluster_id, ingested_at DESC);

CREATE INDEX IF NOT EXISTS idx_news_story_mentions_domain
    ON news_story_mentions (source_domain, ingested_at DESC);
