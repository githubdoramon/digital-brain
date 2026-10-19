-- Keep rejected source parts available without allowing one batch to block the archive.
CREATE TABLE intelligence_failures (
    user_email TEXT NOT NULL REFERENCES intelligence_state ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES intelligence_sources ON DELETE CASCADE,
    part INTEGER NOT NULL,
    review BOOLEAN NOT NULL DEFAULT FALSE,
    knowledge_only BOOLEAN NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1 CHECK (attempts BETWEEN 0 AND 3),
    error_code TEXT NOT NULL,
    next_attempt_at TIMESTAMPTZ,
    PRIMARY KEY (user_email, source_id, part, review)
);
