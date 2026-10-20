-- Retry only retained context-limit failures with the bounded two-phase prompts.
UPDATE knowledge_learning SET status='pending',attempts=0,error_code=NULL,
    lease_token=NULL,lease_until=NULL,next_attempt_at=NOW()
WHERE status='failed' AND error_code='context_length_exceeded';
UPDATE intelligence_failures SET attempts=0,next_attempt_at=NOW()
WHERE error_code='context_length_exceeded';
UPDATE intelligence_runs r SET status='pending',next_attempt_at=NOW(),reviewed=FALSE
WHERE r.status IN ('failed','succeeded') AND EXISTS (
    SELECT 1 FROM intelligence_failures f JOIN intelligence_sources s USING(source_id)
    WHERE f.user_email=r.user_email AND f.knowledge_only=r.knowledge_only
        AND f.error_code='context_length_exceeded' AND s.observed_at<=r.cutoff
);
