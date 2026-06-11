CREATE TABLE IF NOT EXISTS proposed_event_ignores (
    id BIGSERIAL PRIMARY KEY,
    user_email TEXT NOT NULL,
    ignore_type TEXT NOT NULL,
    value TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (ignore_type IN ('place_id', 'place_name', 'location_signature')),
    UNIQUE (user_email, ignore_type, value)
);

CREATE INDEX IF NOT EXISTS idx_proposed_event_ignores_user
    ON proposed_event_ignores (user_email, ignore_type);

CREATE TABLE IF NOT EXISTS proposed_events (
    proposal_id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'location_gap',
    local_date DATE NOT NULL,
    timezone TEXT,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    duration_minutes INTEGER NOT NULL,
    place_id TEXT REFERENCES places(place_id) ON DELETE SET NULL,
    place_name TEXT,
    city TEXT,
    country TEXT,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    confidence TEXT NOT NULL,
    reason TEXT,
    suggested_title TEXT,
    suggested_summary TEXT,
    suggested_contact_ids TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    ignored_signature TEXT,
    evidence JSONB NOT NULL DEFAULT '{}'::JSONB,
    accepted_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('pending', 'accepted', 'dismissed', 'ignored', 'expired')),
    CHECK (confidence IN ('medium', 'high'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_proposed_events_gap_unique
    ON proposed_events (user_email, local_date, start_at, end_at, COALESCE(place_id, ''), COALESCE(ignored_signature, ''))
    WHERE status <> 'expired';

CREATE INDEX IF NOT EXISTS idx_proposed_events_user_status
    ON proposed_events (user_email, status, start_at DESC);

CREATE INDEX IF NOT EXISTS idx_proposed_events_expires
    ON proposed_events (expires_at)
    WHERE status = 'pending';

INSERT INTO notification_subscriptions (
    user_email,
    notification_type,
    notification_channels
)
SELECT user_email,
       'proposed-events-ready',
       ARRAY['push']::TEXT[]
FROM (
    SELECT DISTINCT user_email
    FROM notification_subscriptions
    WHERE 'push' = ANY(notification_channels)
) push_users
ON CONFLICT (user_email, notification_type)
DO UPDATE SET
    notification_channels = (
        SELECT ARRAY(
            SELECT DISTINCT channel
            FROM unnest(notification_subscriptions.notification_channels || ARRAY['push']::TEXT[]) AS channel
            ORDER BY channel
        )
    ),
    updated_at = NOW()
WHERE NOT ('push' = ANY(notification_subscriptions.notification_channels));
