CREATE TABLE IF NOT EXISTS robot_modules (
    module_id TEXT NOT NULL,
    robot_id TEXT NOT NULL REFERENCES robots(robot_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    module_type TEXT NOT NULL DEFAULT 'generic'
        CHECK (module_type IN ('sensor', 'actuator', 'camera', 'microphone', 'speaker', 'generic')),
    status TEXT NOT NULL DEFAULT 'offline'
        CHECK (status IN ('online', 'offline', 'error')),
    capabilities TEXT[] DEFAULT '{}'::TEXT[],
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    last_seen_at TIMESTAMPTZ,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (robot_id, module_id)
);

CREATE INDEX IF NOT EXISTS idx_robot_modules_type
    ON robot_modules (module_type);
