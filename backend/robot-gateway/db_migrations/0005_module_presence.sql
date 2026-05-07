ALTER TABLE robots
    DROP COLUMN IF EXISTS status;

ALTER TABLE robot_modules
    ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMPTZ;
