ALTER TABLE intelligence_failures
    ADD COLUMN batch_limit INTEGER NOT NULL DEFAULT 12 CHECK (batch_limit BETWEEN 1 AND 12),
    ADD COLUMN error_details JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN failed_at TIMESTAMPTZ;
ALTER TABLE knowledge_learning
    ADD COLUMN error_details JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN failed_at TIMESTAMPTZ;

-- One-time recovery of validation/output failures retained under the old strategy.
-- Preserve the old code; original diagnostics cannot be reconstructed retroactively.
UPDATE intelligence_failures SET attempts=0,next_attempt_at=NOW(),batch_limit=1
WHERE NOT knowledge_only AND next_attempt_at IS NULL AND attempts>=3
    AND error_code IN ('invalid_schema','invalid_evidence','invalid_contact_change','output_exhausted');
UPDATE knowledge_learning SET status='pending',attempts=0,batch_limit=1,
    next_attempt_at=NOW(),lease_token=NULL,lease_until=NULL
WHERE status='failed' AND attempts>=3 AND (lease_until IS NULL OR lease_until<=NOW())
    AND error_code IN ('invalid_schema','invalid_evidence','invalid_contact_change','output_exhausted');
