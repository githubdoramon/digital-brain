CREATE TABLE IF NOT EXISTS user_last_known_locations (
    user_email TEXT PRIMARY KEY,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    accuracy_m DOUBLE PRECISION,
    captured_at TIMESTAMPTZ NOT NULL,
    source TEXT,
    timezone TEXT,
    place_name TEXT,
    city TEXT,
    country TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_last_known_locations_updated
    ON user_last_known_locations (updated_at DESC);
