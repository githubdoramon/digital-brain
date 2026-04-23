CREATE TABLE IF NOT EXISTS mobile_location_request_dedupe (
    debug_request_id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    captured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mobile_location_request_dedupe_user_created
    ON mobile_location_request_dedupe (user_email, created_at DESC);
