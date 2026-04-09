CREATE TABLE IF NOT EXISTS user_location_history (
    id BIGSERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    accuracy_m DOUBLE PRECISION,
    captured_at TIMESTAMPTZ NOT NULL,
    source TEXT,
    timezone TEXT,
    place_name TEXT,
    city TEXT,
    country TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_email, captured_at, lat, lon)
);

CREATE INDEX IF NOT EXISTS idx_user_location_history_user_captured
    ON user_location_history (user_email, captured_at DESC, id DESC);

INSERT INTO user_location_history (
    user_email,
    lat,
    lon,
    accuracy_m,
    captured_at,
    source,
    timezone,
    place_name,
    city,
    country,
    updated_at
)
SELECT
    user_email,
    lat,
    lon,
    accuracy_m,
    captured_at,
    source,
    timezone,
    place_name,
    city,
    country,
    updated_at
FROM user_last_known_locations
ON CONFLICT (user_email, captured_at, lat, lon) DO NOTHING;
