-- External messages deliberately do not share assistant conversation storage.
CREATE TABLE messaging_accounts (
    account_id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider = 'whatsapp'),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    context TEXT NOT NULL DEFAULT 'mixed'
        CHECK (context IN ('work', 'personal', 'mixed', 'unclassified')),
    self_jid TEXT,
    state TEXT NOT NULL DEFAULT 'disconnected',
    error_code TEXT,
    generation BIGINT NOT NULL DEFAULT 0,
    last_capture_at TIMESTAMPTZ,
    capture_failures BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_email, provider), UNIQUE (user_email, account_id)
);
CREATE TABLE messaging_identities (
    identity_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES messaging_accounts ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    contact_id TEXT REFERENCES contacts(contact_id) ON DELETE SET NULL,
    link_origin TEXT CHECK (link_origin IN ('manual', 'identifier')),
    is_self BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (account_id, external_id), UNIQUE (account_id, identity_id)
);
CREATE TABLE messaging_identity_links (
    link_id BIGSERIAL PRIMARY KEY,
    identity_id TEXT NOT NULL REFERENCES messaging_identities ON DELETE CASCADE,
    old_contact_id TEXT,
    new_contact_id TEXT,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE messaging_conversations (
    conversation_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES messaging_accounts ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('direct', 'group')),
    context TEXT CHECK (context IN ('work', 'personal', 'mixed', 'unclassified')),
    UNIQUE (account_id, external_id), UNIQUE (account_id, conversation_id)
);
CREATE TABLE messaging_messages (
    message_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES messaging_accounts ON DELETE CASCADE,
    conversation_id TEXT NOT NULL,
    sender_identity_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_at TIMESTAMPTZ NOT NULL,
    revision_priority SMALLINT NOT NULL DEFAULT 0,
    body TEXT NOT NULL DEFAULT '',
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    outgoing BOOLEAN NOT NULL,
    reply_id TEXT,
    attachments JSONB NOT NULL DEFAULT '[]',
    body_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', body)) STORED,
    FOREIGN KEY (account_id, conversation_id)
        REFERENCES messaging_conversations(account_id, conversation_id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, sender_identity_id)
        REFERENCES messaging_identities(account_id, identity_id) ON DELETE CASCADE,
    UNIQUE (account_id, conversation_id, sender_identity_id, external_id)
);
CREATE TABLE messaging_memberships (
    account_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    identity_id TEXT NOT NULL,
    first_observed_at TIMESTAMPTZ NOT NULL,
    last_observed_at TIMESTAMPTZ NOT NULL,
    evidence TEXT NOT NULL DEFAULT 'sender',
    PRIMARY KEY (conversation_id, identity_id),
    FOREIGN KEY (account_id, conversation_id)
        REFERENCES messaging_conversations(account_id, conversation_id) ON DELETE CASCADE,
    FOREIGN KEY (account_id, identity_id)
        REFERENCES messaging_identities(account_id, identity_id) ON DELETE CASCADE
);
CREATE INDEX messaging_body_fts ON messaging_messages USING GIN(body_tsv);
CREATE INDEX messaging_chronology ON messaging_messages(conversation_id, sent_at, message_id);
CREATE TABLE messaging_revisions (
    message_id TEXT NOT NULL REFERENCES messaging_messages ON DELETE CASCADE,
    revision_hash TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('message', 'edit', 'revoke')),
    source_time TIMESTAMPTZ NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    body TEXT NOT NULL,
    attachments JSONB NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('live', 'history')),
    PRIMARY KEY (message_id, revision_hash)
);
-- Only sanitized projections enter staging. Never store raw protobuf/media here.
CREATE TABLE messaging_inbox (
    inbox_id BIGSERIAL PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES messaging_accounts ON DELETE CASCADE,
    generation BIGINT NOT NULL,
    record_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, record_hash)
);
CREATE INDEX messaging_inbox_due ON messaging_inbox(next_attempt_at, inbox_id)
    WHERE processed_at IS NULL;
