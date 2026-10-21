-- Preserve command evidence without scheduling a second execution path.
ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;

CREATE FUNCTION knowledge_source_learnable(payload JSONB) RETURNS BOOLEAN
LANGUAGE SQL IMMUTABLE AS $$
    SELECT COALESCE(payload->>'learning_owner','') = ''
        AND COALESCE(payload->>'origin','') <> 'confirmed_contact_command'
$$;

CREATE OR REPLACE FUNCTION knowledge_capture_chat() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE owner TEXT; src BIGINT; command_owner TEXT;
BEGIN
    SELECT user_email INTO owner FROM conversation_threads WHERE id=NEW.thread_id;
    IF NEW.role='user' AND owner IS NOT NULL THEN
        command_owner := NEW.metadata->>'learning_owner';
        IF COALESCE(command_owner,'')='' AND NEW.content ~* '^/(contact|event)(\s|$)' THEN
            command_owner := lower(substring(NEW.content from '^/([[:alpha:]]+)'));
        END IF;
        PERFORM intelligence_store_source('chat',NEW.message_id::text,owner,
            jsonb_build_object('text',NEW.content,'thread_id',NEW.thread_id,
                'sent_at',COALESCE(NEW.metadata->'request_time'->>'sent_at',NEW.created_at::text),
                'timezone',NEW.metadata->'request_time'->>'timezone',
                'learning_owner',command_owner,'outgoing',TRUE));
        SELECT source_id INTO src FROM intelligence_sources
            WHERE kind='chat' AND entity_id=NEW.message_id::text ORDER BY source_id DESC LIMIT 1;
        IF COALESCE(command_owner,'')='' THEN
            INSERT INTO knowledge_learning(user_email,source_id,historical)
                VALUES(owner,src,FALSE) ON CONFLICT DO NOTHING;
            UPDATE knowledge_learning j SET status='pending',attempts=0,next_attempt_at=NOW()
                FROM intelligence_sources s WHERE j.source_id=s.source_id AND j.user_email=owner
                AND j.status='deferred' AND s.kind='chat' AND s.payload->>'thread_id'=NEW.thread_id
                AND knowledge_source_learnable(s.payload);
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- Recover ownership for persisted previews and their clarification exchanges.
WITH exchanges AS (
    SELECT u.message_id, CASE
        WHEN u.content ~* '^/(contact|event)(\s|$)' THEN lower(substring(u.content from '^/([[:alpha:]]+)'))
        WHEN a.metadata->'command_result'->>'type'='contact_confirmation' THEN 'contact'
        WHEN a.metadata->'command_result'->>'type'='event_confirmation' THEN 'event'
        WHEN a.metadata->'command_result'->>'source'='contact_command' THEN 'contact'
        WHEN a.metadata->'command_result'->>'source'='event_command' THEN 'event'
        ELSE a.metadata->'command_result'->'command_state'->>'command_name'
    END AS owner
    FROM conversation_messages u
    LEFT JOIN LATERAL (
        SELECT metadata FROM conversation_messages a WHERE a.thread_id=u.thread_id AND a.role='assistant'
            AND (a.created_at,a.message_id)>(u.created_at,u.message_id)
        ORDER BY a.created_at,a.message_id LIMIT 1
    ) a ON TRUE WHERE u.role='user'
)
UPDATE conversation_messages m SET metadata=COALESCE(m.metadata,'{}'::jsonb) || jsonb_build_object('learning_owner',e.owner)
FROM exchanges e WHERE m.message_id=e.message_id AND e.owner IN ('contact','event');

UPDATE intelligence_sources s SET payload=s.payload || jsonb_build_object('learning_owner',m.metadata->>'learning_owner')
FROM conversation_messages m WHERE s.kind='chat' AND s.entity_id=m.message_id::text
AND m.metadata->>'learning_owner' IN ('contact','event');

-- Revoke in-flight work as well as retries; do not undo previously applied data.
UPDATE knowledge_learning j SET status='done',lease_token=NULL,lease_until=NULL,error_code=NULL
FROM intelligence_sources s WHERE j.source_id=s.source_id AND NOT knowledge_source_learnable(s.payload);
