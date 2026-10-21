-- Historical extraction has one owner: knowledge_learning. Keep the old journal
-- for audit, and reuse successful work without erasing clarification waits.
INSERT INTO knowledge_learning(user_email,source_id,part,historical,status,last_success_at)
SELECT p.user_email,p.source_id,p.part,TRUE,'done',p.processed_at
FROM intelligence_processed p JOIN intelligence_sources s USING(source_id)
JOIN intelligence_state st ON st.user_email=p.user_email
WHERE s.kind IN ('message','event','document') AND (s.owner_email IS NULL OR s.owner_email=p.user_email)
AND (s.historical OR s.observed_at<st.started_at
    OR COALESCE((s.payload->>'sent_at')::timestamptz<st.started_at,FALSE))
ON CONFLICT(user_email,source_id,part) DO UPDATE SET status='done',error_code=NULL,
    lease_token=NULL,lease_until=NULL,last_success_at=EXCLUDED.last_success_at
WHERE knowledge_learning.status NOT IN ('done','deferred')
AND knowledge_learning.last_success_at IS NULL
AND knowledge_learning.next_attempt_at<EXCLUDED.last_success_at;

-- Preserve outstanding validation failures with their retry state in the owner queue.
INSERT INTO knowledge_learning(user_email,source_id,part,historical,status,attempts,error_code,next_attempt_at)
SELECT f.user_email,f.source_id,f.part,TRUE,'failed',f.attempts,f.error_code,
    COALESCE(f.next_attempt_at,NOW())
FROM intelligence_failures f JOIN intelligence_sources s USING(source_id)
WHERE f.knowledge_only AND NOT f.review AND (s.owner_email IS NULL OR s.owner_email=f.user_email)
AND NOT EXISTS(SELECT 1 FROM intelligence_processed p WHERE p.user_email=f.user_email
    AND p.source_id=f.source_id AND p.part=f.part)
ON CONFLICT(user_email,source_id,part) DO UPDATE SET status='failed',
    attempts=EXCLUDED.attempts,error_code=EXCLUDED.error_code,next_attempt_at=EXCLUDED.next_attempt_at
WHERE knowledge_learning.status='pending' AND knowledge_learning.attempts=0;

-- Revoke historical run tokens so a rolling deployment's old worker cannot commit.
UPDATE intelligence_runs SET status='succeeded',lease_token=NULL,lease_until=NULL,
    error_code=NULL WHERE knowledge_only;

-- Provider outages do not constitute bad evidence. Re-enable affected source parts.
UPDATE knowledge_learning SET status='pending',attempts=0,error_code=NULL,
    next_attempt_at=NOW(),lease_token=NULL,lease_until=NULL
WHERE status='failed' AND error_code IN ('model_unavailable','model_timeout','model_rate_limited');

-- Persist summary coalescing boundaries across worker restarts.
ALTER TABLE knowledge_summaries ADD COLUMN dirty_since TIMESTAMPTZ,
    ADD COLUMN history_dirty BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE knowledge_summaries SET dirty_since=NOW() WHERE version<>rendered_version;
CREATE FUNCTION knowledge_summary_dirty() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.version=NEW.rendered_version THEN
        NEW.dirty_since:=NULL;
        NEW.history_dirty:=FALSE;
    ELSIF TG_OP='INSERT' THEN
        NEW.dirty_since:=NOW();
        NEW.history_dirty:=COALESCE(current_setting('brain.learning_history',TRUE)='on',FALSE);
    ELSIF NEW.version<>OLD.version THEN
        NEW.dirty_since:=COALESCE(OLD.dirty_since,NOW());
        NEW.history_dirty:=COALESCE(current_setting('brain.learning_history',TRUE)='on',FALSE)
            AND (OLD.version=OLD.rendered_version OR OLD.history_dirty);
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER knowledge_summary_dirty_change BEFORE INSERT OR UPDATE ON knowledge_summaries
FOR EACH ROW EXECUTE FUNCTION knowledge_summary_dirty();
