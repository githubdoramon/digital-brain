CREATE TABLE IF NOT EXISTS robot_commands (
    command_id TEXT PRIMARY KEY,
    robot_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sent', 'acknowledged', 'failed', 'expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    acked_at TIMESTAMPTZ,
    error TEXT,
    created_by TEXT,
    FOREIGN KEY (robot_id, module_id)
        REFERENCES robot_modules(robot_id, module_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_robot_commands_robot_module
    ON robot_commands (robot_id, module_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_robot_commands_status
    ON robot_commands (status);
