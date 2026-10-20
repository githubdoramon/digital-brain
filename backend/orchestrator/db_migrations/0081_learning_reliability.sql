-- Long reasoning calls need durable summary ownership across worker processes.
ALTER TABLE knowledge_summaries ADD COLUMN lease_token TEXT;
ALTER TABLE knowledge_summaries ADD COLUMN lease_until TIMESTAMPTZ;
ALTER TABLE knowledge_summaries ADD COLUMN error_code TEXT;

-- One deployment-scoped replay of previous failures. Preserve completed work,
-- ambiguous identities, manual corrections, deletion boundaries and evidence.
UPDATE knowledge_learning SET status='pending', attempts=0, error_code=NULL,
    lease_token=NULL, lease_until=NULL, next_attempt_at=NOW()
WHERE status='failed';
UPDATE intelligence_failures SET attempts=0, next_attempt_at=NOW();
UPDATE intelligence_runs r SET status='pending', next_attempt_at=NOW(), reviewed=FALSE
WHERE r.status IN ('failed','succeeded') AND EXISTS (
    SELECT 1 FROM intelligence_failures f JOIN intelligence_sources s USING(source_id)
    WHERE f.user_email=r.user_email AND f.knowledge_only=r.knowledge_only AND s.observed_at<=r.cutoff
);
UPDATE knowledge_summaries SET next_attempt_at=NOW() WHERE version<>rendered_version;
