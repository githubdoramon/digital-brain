-- Multi-source evidence failures formerly spent the retry budget of every part.
-- Retry only that recoverable failure class, with smaller initial groups; the
-- controller now durably splits further before spending single-part retries.
UPDATE knowledge_learning
SET status='pending', attempts=0, batch_limit=LEAST(batch_limit,12),
    error_code=NULL, next_attempt_at=NOW(), lease_token=NULL, lease_until=NULL
WHERE status='failed' AND attempts>=3 AND error_code='invalid_evidence'
    AND batch_limit>1 AND (lease_until IS NULL OR lease_until<=NOW());
