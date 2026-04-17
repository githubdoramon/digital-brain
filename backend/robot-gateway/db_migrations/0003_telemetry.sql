CREATE TABLE IF NOT EXISTS robot_telemetry (
    id BIGSERIAL PRIMARY KEY,
    robot_id TEXT NOT NULL,
    module_id TEXT NOT NULL,
    measured_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    payload_type TEXT NOT NULL DEFAULT 'generic',
    FOREIGN KEY (robot_id, module_id)
        REFERENCES robot_modules(robot_id, module_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_robot_telemetry_robot_module_time
    ON robot_telemetry (robot_id, module_id, measured_at DESC);

CREATE INDEX IF NOT EXISTS idx_robot_telemetry_received
    ON robot_telemetry (received_at DESC);

CREATE INDEX IF NOT EXISTS idx_robot_telemetry_payload_type
    ON robot_telemetry (payload_type);
