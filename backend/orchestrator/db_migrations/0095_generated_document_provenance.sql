-- Generated documents remain in the archive, but are not independent evidence.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS raw_metadata JSONB DEFAULT '{}';

CREATE OR REPLACE FUNCTION intelligence_document_payload(d documents)
RETURNS JSONB LANGUAGE SQL IMMUTABLE AS $$
    SELECT jsonb_build_object('text', COALESCE(d.title, '') || E'\n' ||
        COALESCE(d.description, '') || E'\n' || COALESCE(d.content, ''),
        'sent_at', COALESCE(d.document_date, d.created_at), 'deleted', FALSE, 'title', d.title)
        || CASE WHEN d.raw_metadata->>'evidence_origin' = 'app_generated'
            THEN '{"evidence_origin":"app_generated"}'::jsonb ELSE '{}'::jsonb END;
$$;

DROP TRIGGER intelligence_document_change ON documents;
CREATE TRIGGER intelligence_document_change AFTER INSERT OR UPDATE OF title, description,
    content, document_date, raw_metadata ON documents
    FOR EACH ROW EXECUTE FUNCTION intelligence_capture_shared();

-- Only explicit legacy app signatures; a preparation title alone proves nothing.
UPDATE documents SET raw_metadata=COALESCE(raw_metadata,'{}') ||
    '{"evidence_origin":"app_generated"}'::jsonb
WHERE raw_metadata->>'evidence_origin' IS DISTINCT FROM 'app_generated'
    AND (right(COALESCE(content,''),1500) ~
        E'(?m)^\\*?Prepared automatically by your personal memory assistant\\.'
        OR description LIKE 'Generated PDF: %');

-- Let previously exhausted generated sources finish through the no-model skip
-- path. Never reset completed work or an active lease.
UPDATE knowledge_learning j SET status='pending',attempts=0,next_attempt_at=NOW(),
    lease_token=NULL,lease_until=NULL
FROM intelligence_sources s JOIN documents d ON d.document_id=s.entity_id
WHERE j.source_id=s.source_id AND s.kind='document' AND j.status='failed'
    AND (j.lease_until IS NULL OR j.lease_until<=NOW())
    AND d.raw_metadata->>'evidence_origin'='app_generated';
