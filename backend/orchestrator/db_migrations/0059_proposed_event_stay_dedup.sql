DROP INDEX IF EXISTS idx_proposed_events_gap_unique;

WITH ranked AS (
    SELECT
        ctid,
        ROW_NUMBER() OVER (
            PARTITION BY user_email, local_date, start_at, COALESCE(ignored_signature, '')
            ORDER BY
                CASE status
                    WHEN 'accepted' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'dismissed' THEN 2
                    WHEN 'ignored' THEN 3
                    ELSE 4
                END,
                updated_at DESC,
                created_at DESC
        ) AS duplicate_rank
    FROM proposed_events
    WHERE status <> 'expired'
)
UPDATE proposed_events AS pe
SET status = 'dismissed',
    reason = CONCAT(
        COALESCE(pe.reason, ''),
        CASE WHEN COALESCE(pe.reason, '') = '' THEN '' ELSE ' ' END,
        'Superseded duplicate proposal.'
    ),
    updated_at = NOW()
FROM ranked
WHERE pe.ctid = ranked.ctid
  AND ranked.duplicate_rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_proposed_events_stay_unique
    ON proposed_events (user_email, local_date, start_at, COALESCE(ignored_signature, ''))
    WHERE status <> 'expired';
