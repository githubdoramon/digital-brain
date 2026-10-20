-- Knowledge remains private to its learner even when canonical contacts are shared.
ALTER TABLE intelligence_knowledge
    ADD COLUMN category TEXT NOT NULL DEFAULT 'general',
    ADD COLUMN inferred BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN inference_reason TEXT,
    ADD COLUMN importance SMALLINT NOT NULL DEFAULT 5 CHECK (importance BETWEEN 1 AND 10),
    ADD COLUMN deleted_at TIMESTAMPTZ,
    ADD COLUMN revision BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN legacy_fact_id TEXT,
    ADD COLUMN source_thread_id TEXT,
    ADD COLUMN embedding DOUBLE PRECISION[],
    ADD COLUMN search_text TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', statement)) STORED;
CREATE INDEX knowledge_search ON intelligence_knowledge USING GIN(search_text);
CREATE INDEX knowledge_active ON intelligence_knowledge(user_email, subject_ref) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX knowledge_legacy_fact ON intelligence_knowledge(user_email, legacy_fact_id) WHERE legacy_fact_id IS NOT NULL;

CREATE TABLE knowledge_entities (
    user_email TEXT NOT NULL,
    entity_ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    PRIMARY KEY(user_email, entity_ref)
);
CREATE TABLE knowledge_subjects (
    knowledge_id TEXT NOT NULL REFERENCES intelligence_knowledge ON DELETE CASCADE,
    subject_ref TEXT NOT NULL,
    PRIMARY KEY(knowledge_id, subject_ref)
);
INSERT INTO knowledge_subjects SELECT knowledge_id, subject_ref FROM intelligence_knowledge;
CREATE INDEX knowledge_subject_lookup ON knowledge_subjects(subject_ref, knowledge_id);
CREATE TABLE knowledge_summaries (
    user_email TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    rendered_version BIGINT NOT NULL DEFAULT 0,
    summary TEXT,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(user_email, subject_ref)
);
CREATE FUNCTION knowledge_invalidate() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE item intelligence_knowledge; ref TEXT;
BEGIN
    item := CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
    IF TG_OP <> 'DELETE' THEN
        INSERT INTO knowledge_subjects VALUES(item.knowledge_id,item.subject_ref) ON CONFLICT DO NOTHING;
    END IF;
    FOR ref IN SELECT subject_ref FROM knowledge_subjects WHERE knowledge_id=item.knowledge_id
        UNION SELECT 'contact:'||i.contact_id FROM knowledge_subjects s
            JOIN messaging_identities i ON s.subject_ref='identity:'||i.identity_id
            JOIN messaging_accounts a ON a.account_id=i.account_id
            WHERE s.knowledge_id=item.knowledge_id AND a.user_email=item.user_email AND i.contact_id IS NOT NULL
        UNION SELECT 'contact:'||c.contact_id FROM knowledge_subjects s,contacts c,unnest(c.emails) address
            WHERE s.knowledge_id=item.knowledge_id AND s.subject_ref='self' AND lower(address)=lower(item.user_email)
        LOOP
        INSERT INTO knowledge_summaries(user_email,subject_ref) VALUES(item.user_email,ref)
        ON CONFLICT(user_email,subject_ref) DO UPDATE SET version=knowledge_summaries.version+1,
            summary=NULL,next_attempt_at=NOW() + INTERVAL '2 seconds';
    END LOOP;
    RETURN item;
END;
$$;
CREATE TRIGGER knowledge_change AFTER INSERT OR UPDATE OF statement,status,applicable_at,valid_from,valid_until,evidence,manual,category,inferred,inference_reason,importance,deleted_at,subject_ref OR DELETE ON intelligence_knowledge
    FOR EACH ROW EXECUTE FUNCTION knowledge_invalidate();
CREATE FUNCTION knowledge_link_invalidate() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE owner TEXT; ref TEXT; ident TEXT; effective TEXT;
BEGIN
    ref := CASE WHEN TG_OP='DELETE' THEN OLD.subject_ref ELSE NEW.subject_ref END;
    ident := CASE WHEN TG_OP='DELETE' THEN OLD.knowledge_id ELSE NEW.knowledge_id END;
    SELECT user_email INTO owner FROM intelligence_knowledge WHERE knowledge_id=ident;
    IF owner IS NOT NULL THEN
        FOR effective IN SELECT ref
            UNION SELECT 'contact:'||i.contact_id FROM messaging_identities i JOIN messaging_accounts a USING(account_id)
                WHERE 'identity:'||i.identity_id=ref AND a.user_email=owner AND i.contact_id IS NOT NULL
            UNION SELECT 'contact:'||c.contact_id FROM contacts c,unnest(c.emails) address
                WHERE ref='self' AND lower(address)=lower(owner)
        LOOP
            INSERT INTO knowledge_summaries(user_email,subject_ref) VALUES(owner,effective)
            ON CONFLICT(user_email,subject_ref) DO UPDATE SET version=knowledge_summaries.version+1,
                summary=NULL,next_attempt_at=NOW();
        END LOOP;
    END IF;
    RETURN NULL;
END;
$$;
CREATE TRIGGER knowledge_link_change AFTER INSERT OR DELETE ON knowledge_subjects
    FOR EACH ROW EXECUTE FUNCTION knowledge_link_invalidate();

ALTER TABLE intelligence_sources DROP CONSTRAINT intelligence_sources_kind_check;
ALTER TABLE intelligence_sources ADD CHECK(kind IN ('message','event','document','chat','contact_note','legacy_fact','manual_fact'));
CREATE TABLE knowledge_learning (
    user_email TEXT NOT NULL,
    source_id BIGINT NOT NULL REFERENCES intelligence_sources ON DELETE CASCADE,
    part INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','done','deferred','failed')),
    historical BOOLEAN NOT NULL DEFAULT TRUE,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_token TEXT,
    lease_until TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    error_code TEXT,
    PRIMARY KEY(user_email,source_id,part)
);
CREATE INDEX knowledge_learning_due ON knowledge_learning(status,next_attempt_at);
CREATE TABLE knowledge_backfills (
    user_email TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 0,
    seeded_at TIMESTAMPTZ
);


-- Capture user-authored Brain messages in the same transaction as the exchange.
-- Assistant output is neither captured here nor eligible supporting evidence.
CREATE FUNCTION knowledge_capture_chat() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE owner TEXT; src BIGINT;
BEGIN
    SELECT user_email INTO owner FROM conversation_threads WHERE id=NEW.thread_id;
    IF NEW.role='user' AND owner IS NOT NULL THEN
        PERFORM intelligence_store_source('chat',NEW.message_id::text,owner,
            jsonb_build_object('text',NEW.content,'thread_id',NEW.thread_id,
                'sent_at',NEW.created_at,'outgoing',TRUE));
        SELECT source_id INTO src FROM intelligence_sources
            WHERE kind='chat' AND entity_id=NEW.message_id::text ORDER BY source_id DESC LIMIT 1;
        INSERT INTO knowledge_learning(user_email,source_id,historical)
            VALUES(owner,src,FALSE) ON CONFLICT DO NOTHING;
        -- A reply can resolve an earlier reference; retry only deferred parts in this thread.
        UPDATE knowledge_learning j SET status='pending',attempts=0,next_attempt_at=NOW()
            FROM intelligence_sources s WHERE j.source_id=s.source_id AND j.user_email=owner
            AND j.status='deferred' AND s.kind='chat' AND s.payload->>'thread_id'=NEW.thread_id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION knowledge_capture_note() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM intelligence_store_source('contact_note',NEW.contact_id,NULL,
        jsonb_build_object('text',COALESCE(NEW.comments,''),'contact_id',NEW.contact_id,
            'title',NEW.display_name,'sent_at',NOW()));
    RETURN NEW;
END;
$$;
DO $$ BEGIN
    IF to_regclass('conversation_messages') IS NOT NULL THEN
        CREATE TRIGGER knowledge_chat_change AFTER INSERT OR UPDATE OF content ON conversation_messages
            FOR EACH ROW EXECUTE FUNCTION knowledge_capture_chat();
    END IF;
    CREATE TRIGGER knowledge_note_change AFTER INSERT OR UPDATE OF comments ON contacts
        FOR EACH ROW EXECUTE FUNCTION knowledge_capture_note();
    IF to_regclass('user_facts') IS NOT NULL THEN
        -- The current legacy text is evidence of an imported record, not a fabricated source quote.
        INSERT INTO intelligence_sources(kind,entity_id,owner_email,fingerprint,payload,historical)
        SELECT 'legacy_fact',fact_id,user_email,md5(content),jsonb_build_object(
            'text',content,'thread_id',source_thread_id,'sent_at',NULL,'legacy',TRUE),TRUE
        FROM user_facts WHERE fact_mode='soft';
        INSERT INTO intelligence_knowledge(knowledge_id,user_email,subject_ref,predicate,statement,
            status,context,evidence,category,importance,legacy_fact_id,source_thread_id,created_at,updated_at)
        SELECT f.fact_id,f.user_email,'self','legacy.' || f.fact_id,f.content,'current','unclassified',
            jsonb_build_array(jsonb_build_object('source_id',s.source_id,'part',0,'kind','legacy_fact',
                'entity_id',f.fact_id,'quote',f.content,'sent_at',NULL)),
            f.category,f.importance,f.fact_id,f.source_thread_id,f.created_at,f.updated_at
        FROM user_facts f JOIN intelligence_sources s ON s.kind='legacy_fact' AND s.entity_id=f.fact_id
            AND s.owner_email=f.user_email WHERE f.fact_mode='soft';
    END IF;
END $$;
INSERT INTO knowledge_summaries(user_email,subject_ref)
    SELECT DISTINCT k.user_email,s.subject_ref FROM intelligence_knowledge k
    JOIN knowledge_subjects s USING(knowledge_id) ON CONFLICT DO NOTHING;

-- The array representation also works in disposable PostgreSQL without pgvector.
CREATE FUNCTION knowledge_cosine(a DOUBLE PRECISION[], b DOUBLE PRECISION[])
RETURNS DOUBLE PRECISION LANGUAGE SQL IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE WHEN cardinality(a)=cardinality(b) THEN
        SUM(x*y)/NULLIF(sqrt(SUM(x*x))*sqrt(SUM(y*y)),0) ELSE NULL END
    FROM unnest(a,b) pair(x,y);
$$;
CREATE FUNCTION knowledge_identity_change() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE owner TEXT; ref TEXT;
BEGIN
    SELECT user_email INTO owner FROM messaging_accounts WHERE account_id=NEW.account_id;
    FOREACH ref IN ARRAY ARRAY['identity:'||NEW.identity_id,'contact:'||OLD.contact_id,'contact:'||NEW.contact_id] LOOP
        IF ref IS NOT NULL THEN
            INSERT INTO knowledge_summaries(user_email,subject_ref) VALUES(owner,ref)
            ON CONFLICT(user_email,subject_ref) DO UPDATE SET summary=NULL,
                version=knowledge_summaries.version+1,next_attempt_at=NOW();
        END IF;
    END LOOP;
    UPDATE knowledge_learning j SET status='pending',attempts=0,next_attempt_at=NOW()
        FROM intelligence_sources s WHERE j.user_email=owner AND j.source_id=s.source_id
        AND j.status='deferred' AND s.payload->>'identity_id'=NEW.identity_id;
    RETURN NEW;
END;
$$;
CREATE TRIGGER knowledge_identity_relink AFTER UPDATE OF contact_id ON messaging_identities
    FOR EACH ROW WHEN(OLD.contact_id IS DISTINCT FROM NEW.contact_id) EXECUTE FUNCTION knowledge_identity_change();
CREATE INDEX knowledge_chat_context ON intelligence_sources(owner_email,(payload->>'thread_id'),source_id DESC) WHERE kind='chat';
CREATE INDEX knowledge_message_context ON intelligence_sources(owner_email,(payload->>'conversation_id'),source_id DESC) WHERE kind='message';
