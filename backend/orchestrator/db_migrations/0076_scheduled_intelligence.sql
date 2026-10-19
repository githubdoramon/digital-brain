-- Source snapshots are private evidence, never exported as telemetry.
CREATE TABLE intelligence_state (
    user_email TEXT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    seeded BOOLEAN NOT NULL DEFAULT FALSE,
    last_success_at TIMESTAMPTZ
);
CREATE TABLE intelligence_sources (
    source_id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('message', 'event', 'document')),
    entity_id TEXT NOT NULL,
    owner_email TEXT,
    fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    historical BOOLEAN NOT NULL DEFAULT FALSE,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX intelligence_sources_owner ON intelligence_sources(owner_email, source_id);
CREATE INDEX intelligence_sources_entity ON intelligence_sources(kind, entity_id, source_id DESC);
CREATE TABLE intelligence_processed (
    user_email TEXT NOT NULL REFERENCES intelligence_state ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES intelligence_sources ON DELETE CASCADE,
    part INTEGER NOT NULL DEFAULT 0,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_email, source_id, part)
);
CREATE TABLE intelligence_runs (
    run_id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL REFERENCES intelligence_state ON DELETE CASCADE,
    slot TEXT NOT NULL,
    cutoff TIMESTAMPTZ NOT NULL,
    knowledge_only BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    lease_token TEXT,
    lease_until TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batches INTEGER NOT NULL DEFAULT 0,
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    error_code TEXT,
    UNIQUE(user_email, slot)
);
CREATE TABLE intelligence_knowledge (
    knowledge_id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    subject_ref TEXT NOT NULL,
    predicate TEXT NOT NULL,
    statement TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('current', 'historical', 'uncertain', 'conflicted')),
    applicable_at TEXT,
    valid_from TIMESTAMPTZ,
    valid_until TIMESTAMPTZ,
    context TEXT NOT NULL DEFAULT 'unclassified',
    evidence JSONB NOT NULL,
    manual BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_email, subject_ref, predicate)
);
CREATE TABLE intelligence_knowledge_revisions (
    revision_id BIGSERIAL PRIMARY KEY,
    knowledge_id TEXT NOT NULL REFERENCES intelligence_knowledge ON DELETE CASCADE,
    snapshot JSONB NOT NULL,
    learned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE intelligence_projects (
    project_id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE intelligence_proposals (
    proposal_id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('task', 'project', 'task_change')),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'dismissed', 'expired')),
    dedupe_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    evidence JSONB NOT NULL,
    reason TEXT NOT NULL,
    accepted_id TEXT,
    resolution_evidence JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (user_email, dedupe_key)
);
CREATE INDEX intelligence_proposals_pending ON intelligence_proposals(user_email, status);
ALTER TABLE todos ADD COLUMN assignee_kind TEXT NOT NULL DEFAULT 'user'
    CHECK (assignee_kind IN ('user', 'contact', 'identity', 'unknown'));
ALTER TABLE todos ADD COLUMN assignee_ref TEXT;
ALTER TABLE todos ADD COLUMN project_id TEXT REFERENCES intelligence_projects ON DELETE SET NULL;
ALTER TABLE todos ADD COLUMN analysis_owner TEXT;
ALTER TABLE todos ADD COLUMN intelligence_manual_at TIMESTAMPTZ;
CREATE TABLE intelligence_task_evidence (
    todo_id TEXT NOT NULL REFERENCES todos ON DELETE CASCADE,
    user_email TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('created', 'completed', 'changed')),
    evidence JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE messaging_conversations ADD COLUMN analysis_excluded BOOLEAN NOT NULL DEFAULT FALSE;
-- Old observations do not establish direct authorship for task completion.
ALTER TABLE messaging_messages ADD COLUMN forwarded BOOLEAN NOT NULL DEFAULT TRUE;

CREATE FUNCTION intelligence_store_source(k TEXT, ident TEXT, owner TEXT, data JSONB)
RETURNS VOID LANGUAGE SQL AS $$
    INSERT INTO intelligence_sources(kind, entity_id, owner_email, fingerprint, payload)
    SELECT k, ident, owner, md5(data::text), data
    WHERE md5(data::text) IS DISTINCT FROM (
        SELECT fingerprint FROM intelligence_sources
        WHERE kind = k AND entity_id = ident ORDER BY source_id DESC LIMIT 1);
$$;
CREATE FUNCTION intelligence_message_payload(m messaging_messages) RETURNS JSONB
LANGUAGE SQL STABLE AS $$
    SELECT jsonb_build_object(
        'text', m.body, 'sent_at', m.sent_at, 'effective_at', m.effective_at,
        'deleted', m.deleted, 'outgoing', m.outgoing,
        'forwarded', m.forwarded,
        'original_known', m.revision_priority = 0 OR EXISTS (
            SELECT 1 FROM messaging_revisions r WHERE r.message_id=m.message_id AND r.kind='message'),
        'identity_id', m.sender_identity_id, 'conversation_id', m.conversation_id,
        'context', COALESCE(c.context, a.context), 'title', c.title,
        'excluded', c.analysis_excluded OR c.kind = 'channel',
        'attachments', m.attachments)
    FROM messaging_conversations c JOIN messaging_accounts a USING(account_id)
    WHERE c.conversation_id = m.conversation_id;
$$;
CREATE FUNCTION intelligence_capture_message() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE owner TEXT;
BEGIN
    SELECT user_email INTO owner FROM messaging_accounts WHERE account_id = NEW.account_id;
    PERFORM intelligence_store_source('message', NEW.message_id, owner,
        intelligence_message_payload(NEW));
    RETURN NEW;
END;
$$;
CREATE TRIGGER intelligence_message_change AFTER INSERT OR UPDATE OF body, deleted, effective_at,
    sent_at, attachments, forwarded ON messaging_messages FOR EACH ROW
    EXECUTE FUNCTION intelligence_capture_message();

CREATE FUNCTION intelligence_event_payload(e events) RETURNS JSONB LANGUAGE SQL IMMUTABLE AS $$
    SELECT jsonb_build_object('text', COALESCE(e.title, '') || E'\n' || COALESCE(e.summary, ''),
        'sent_at', e.start_date, 'deleted', e.is_cancelled, 'title', e.title);
$$;
CREATE FUNCTION intelligence_document_payload(d documents) RETURNS JSONB LANGUAGE SQL IMMUTABLE AS $$
    SELECT jsonb_build_object('text', COALESCE(d.title, '') || E'\n' ||
        COALESCE(d.description, '') || E'\n' || COALESCE(d.content, ''),
        'sent_at', COALESCE(d.document_date, d.created_at), 'deleted', FALSE, 'title', d.title);
$$;
CREATE FUNCTION intelligence_capture_shared() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'events' THEN
        PERFORM intelligence_store_source('event', NEW.id, NULL, intelligence_event_payload(NEW));
    ELSE
        PERFORM intelligence_store_source('document', NEW.document_id, NULL,
            intelligence_document_payload(NEW));
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER intelligence_event_change AFTER INSERT OR UPDATE OF title, summary, start_date,
    is_cancelled ON events FOR EACH ROW EXECUTE FUNCTION intelligence_capture_shared();
CREATE TRIGGER intelligence_document_change AFTER INSERT OR UPDATE OF title, description, content,
    document_date ON documents FOR EACH ROW EXECUTE FUNCTION intelligence_capture_shared();

CREATE FUNCTION intelligence_protect_manual_task() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('brain.intelligence_write', TRUE) IS DISTINCT FROM 'on' THEN
        NEW.intelligence_manual_at := NOW();
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER intelligence_manual_task BEFORE UPDATE OF description, status, due_date,
    assignee_kind, assignee_ref, project_id ON todos FOR EACH ROW
    EXECUTE FUNCTION intelligence_protect_manual_task();
