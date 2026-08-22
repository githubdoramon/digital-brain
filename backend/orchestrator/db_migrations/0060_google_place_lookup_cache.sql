CREATE TABLE IF NOT EXISTS google_place_lookup_cache (
    provider_place_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    primary_type TEXT,
    types TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    formatted_address TEXT,
    city TEXT,
    country TEXT,
    business_status TEXT,
    fetched_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_google_place_lookup_cache_location
    ON google_place_lookup_cache (lat, lon);

CREATE INDEX IF NOT EXISTS idx_google_place_lookup_cache_fetched
    ON google_place_lookup_cache (fetched_at);

CREATE TABLE IF NOT EXISTS google_place_search_cache (
    search_id BIGSERIAL PRIMARY KEY,
    center_lat DOUBLE PRECISION NOT NULL,
    center_lon DOUBLE PRECISION NOT NULL,
    radius_m DOUBLE PRECISION NOT NULL,
    provider_place_ids TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    fetched_at TIMESTAMPTZ NOT NULL,
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_google_place_search_cache_fetched
    ON google_place_search_cache (fetched_at DESC);

CREATE TABLE IF NOT EXISTS google_place_canonical_links (
    provider TEXT NOT NULL,
    provider_place_id TEXT NOT NULL,
    place_id TEXT NOT NULL REFERENCES places(place_id) ON DELETE CASCADE,
    PRIMARY KEY (provider, provider_place_id),
    UNIQUE (provider, place_id)
);
