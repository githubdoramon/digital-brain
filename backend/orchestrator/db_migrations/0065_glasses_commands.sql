CREATE TABLE IF NOT EXISTS glasses_command_executions (
    user_email TEXT NOT NULL,
    command_id UUID NOT NULL,
    transcript TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('processing', 'completed', 'failed')),
    outcome TEXT,
    response JSONB,
    error JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_email, command_id)
);

CREATE INDEX IF NOT EXISTS idx_glasses_command_executions_updated
    ON glasses_command_executions (updated_at);
