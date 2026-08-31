CREATE TABLE moments (
    id UUID PRIMARY KEY,
    user_email TEXT NOT NULL,
    source_type TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    observed_timezone TEXT NOT NULL,
    observed_utc_offset_minutes SMALLINT NOT NULL,
    schema_version TEXT NOT NULL,
    observation JSONB NOT NULL,
    location JSONB NOT NULL DEFAULT '{}'::JSONB,
    place_id TEXT REFERENCES places(place_id) ON DELETE SET NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (length(trim(source_type)) > 0),
    CHECK (schema_version = 'moment_observation.v1')
);

CREATE INDEX idx_moments_user_observed_at
    ON moments (user_email, observed_at DESC, id DESC);

CREATE INDEX idx_moments_user_source_observed_at
    ON moments (user_email, source_type, observed_at DESC, id DESC);
