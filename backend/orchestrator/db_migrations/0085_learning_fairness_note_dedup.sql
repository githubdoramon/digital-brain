-- Scheduling state survives process restarts and is updated under owner row locks.
ALTER TABLE knowledge_backfills ADD COLUMN current_streak SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE knowledge_backfills ADD COLUMN last_claimed_at TIMESTAMPTZ;
ALTER TABLE knowledge_backfills ADD COLUMN last_history_claimed_at TIMESTAMPTZ;
ALTER TABLE intelligence_state ADD COLUMN current_streak SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE intelligence_state ADD COLUMN last_claimed_at TIMESTAMPTZ;
ALTER TABLE intelligence_state ADD COLUMN last_history_claimed_at TIMESTAMPTZ;

-- Keep every original snapshot, evidence link, receipt and lease. Only redundant
-- learning work is excluded. An A -> B -> A edit is three distinct episodes.
ALTER TABLE intelligence_sources ADD COLUMN duplicate_of_source_id BIGINT
    REFERENCES intelligence_sources(source_id) ON DELETE SET NULL
    CHECK(duplicate_of_source_id < source_id);
WITH previous AS (
    SELECT source_id,entity_id,owner_email,payload-'sent_at' AS semantic,
        lag(payload-'sent_at') OVER(PARTITION BY owner_email,entity_id ORDER BY source_id) AS prior
    FROM intelligence_sources WHERE kind='contact_note'
), episodes AS (
    SELECT *,sum((semantic IS DISTINCT FROM prior)::int)
        OVER(PARTITION BY owner_email,entity_id ORDER BY source_id) AS episode
    FROM previous
), canonical AS (
    SELECT source_id,min(source_id) OVER(PARTITION BY owner_email,entity_id,episode) AS original
    FROM episodes
)
UPDATE intelligence_sources s SET duplicate_of_source_id=c.original
FROM canonical c WHERE c.source_id=s.source_id AND c.source_id<>c.original;
CREATE INDEX knowledge_note_duplicates ON intelligence_sources(duplicate_of_source_id)
    WHERE duplicate_of_source_id IS NOT NULL;

CREATE OR REPLACE FUNCTION knowledge_capture_note() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE data JSONB; previous JSONB;
BEGIN
    data := jsonb_build_object('text',COALESCE(NEW.comments,''),'contact_id',NEW.contact_id,
        'title',NEW.display_name,'sent_at',NOW());
    SELECT payload INTO previous FROM intelligence_sources
        WHERE kind='contact_note' AND entity_id=NEW.contact_id AND owner_email IS NULL
        ORDER BY source_id DESC LIMIT 1;
    -- Capture time is provenance, not a content change. This also compares seeded
    -- notes, whose original applicability timestamp is deliberately unknown.
    IF previous-'sent_at' IS DISTINCT FROM data-'sent_at' THEN
        PERFORM intelligence_store_source('contact_note',NEW.contact_id,NULL,data);
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER knowledge_note_change ON contacts;
CREATE TRIGGER knowledge_note_change AFTER INSERT OR UPDATE OF comments,display_name ON contacts
    FOR EACH ROW EXECUTE FUNCTION knowledge_capture_note();

-- Reconsider retained reference-format failures, preserving successful work,
-- active leases, genuine ambiguity deferrals and manual corrections.
UPDATE knowledge_learning j SET status='pending',attempts=0,error_code=NULL,
    lease_token=NULL,lease_until=NULL,next_attempt_at=NOW()
FROM intelligence_sources s WHERE j.source_id=s.source_id AND s.duplicate_of_source_id IS NULL
    AND j.status='failed' AND j.error_code='invalid_subject';
UPDATE intelligence_failures SET attempts=0,next_attempt_at=NOW() WHERE error_code='invalid_subject';
UPDATE intelligence_runs r SET status='pending',next_attempt_at=NOW(),reviewed=FALSE
WHERE r.status IN ('failed','succeeded') AND EXISTS (
    SELECT 1 FROM intelligence_failures f JOIN intelligence_sources s USING(source_id)
    WHERE f.user_email=r.user_email AND f.knowledge_only=r.knowledge_only
        AND f.error_code='invalid_subject' AND s.observed_at<=r.cutoff
);
