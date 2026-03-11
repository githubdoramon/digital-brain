CREATE TABLE IF NOT EXISTS async_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    user_email TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    status_message TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    result JSONB,
    error TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT async_jobs_status_check
        CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    CONSTRAINT async_jobs_unique_request
        UNIQUE (job_type, user_email, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_async_jobs_lookup
    ON async_jobs (job_type, user_email, dedupe_key);

CREATE INDEX IF NOT EXISTS idx_async_jobs_status
    ON async_jobs (status, updated_at DESC);

DO $$
BEGIN
    IF to_regclass('daily_briefing_jobs') IS NOT NULL THEN
        INSERT INTO async_jobs (
            job_id,
            job_type,
            user_email,
            dedupe_key,
            status,
            status_message,
            payload,
            result,
            error,
            requested_at,
            started_at,
            finished_at,
            created_at,
            updated_at
        )
        SELECT
            job_id,
            'daily_briefing' AS job_type,
            user_email,
            CONCAT(briefing_date::TEXT, '::', timezone) AS dedupe_key,
            status,
            status_message,
            jsonb_build_object(
                'date', briefing_date::TEXT,
                'timezone', timezone,
                'user_email', user_email
            ) AS payload,
            CASE WHEN briefing_id IS NOT NULL
                THEN jsonb_build_object('briefing_id', briefing_id)
                ELSE NULL
            END AS result,
            error,
            requested_at,
            started_at,
            finished_at,
            created_at,
            updated_at
        FROM daily_briefing_jobs
        ON CONFLICT (job_type, user_email, dedupe_key) DO NOTHING;
    END IF;
END $$;
