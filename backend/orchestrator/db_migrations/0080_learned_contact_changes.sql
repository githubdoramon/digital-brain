-- Recurring birthdays can have a known month/day without a known birth year.
ALTER TABLE contacts ADD COLUMN birthday_month SMALLINT;
ALTER TABLE contacts ADD COLUMN birthday_day SMALLINT;
ALTER TABLE contacts ADD CONSTRAINT contacts_birthday_parts CHECK (
    (birthday_month IS NULL AND birthday_day IS NULL)
    OR (birthday IS NULL AND birthday_month IS NOT NULL AND birthday_day IS NOT NULL
        AND birthday_month BETWEEN 1 AND 12 AND birthday_day BETWEEN 1 AND 31
        AND make_date(2000, birthday_month, birthday_day) IS NOT NULL)
);

ALTER TABLE intelligence_proposals DROP CONSTRAINT intelligence_proposals_kind_check;
ALTER TABLE intelligence_proposals ADD CONSTRAINT intelligence_proposals_kind_check
    CHECK (kind IN ('task', 'project', 'task_change', 'contact_change'));

-- Existing observed evidence gets the newly supported contact-structuring pass.
-- This lane still cannot create tasks or projects from historical evidence.
UPDATE knowledge_learning SET status='pending', attempts=0, next_attempt_at=NOW()
WHERE status='done';
