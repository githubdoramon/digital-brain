ALTER TABLE async_jobs DROP CONSTRAINT async_jobs_status_check;
ALTER TABLE async_jobs ADD CONSTRAINT async_jobs_status_check
    CHECK (status IN ('pending','running','succeeded','failed','cancelled'));
