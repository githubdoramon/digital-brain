CREATE TABLE IF NOT EXISTS contact_enrichment_proposals (
    proposal_id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    contact_id TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
    related_contact_id TEXT REFERENCES contacts(contact_id) ON DELETE CASCADE,
    suggested_value TEXT NOT NULL,
    reciprocal_value TEXT,
    source_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
    confidence TEXT NOT NULL DEFAULT 'medium',
    evidence TEXT,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '90 days'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (kind IN ('profession', 'relationship')),
    CHECK (status IN ('pending', 'accepted', 'dismissed', 'expired')),
    CHECK (confidence IN ('low', 'medium', 'high')),
    CHECK (
        (kind = 'profession' AND related_contact_id IS NULL AND reciprocal_value IS NULL)
        OR (kind = 'relationship' AND related_contact_id IS NOT NULL)
    ),
    UNIQUE (user_email, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_contact_enrichment_proposals_user_status
    ON contact_enrichment_proposals (user_email, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contact_enrichment_proposals_expiry
    ON contact_enrichment_proposals (expires_at)
    WHERE status = 'pending';
