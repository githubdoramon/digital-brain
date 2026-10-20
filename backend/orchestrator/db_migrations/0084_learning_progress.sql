-- Preserve real activity times from this deployment onward. Older completions
-- deliberately remain unknown; migration time is not evidence of recent work.
ALTER TABLE knowledge_learning ADD COLUMN started_at TIMESTAMPTZ;
ALTER TABLE knowledge_learning ADD COLUMN last_success_at TIMESTAMPTZ;
ALTER TABLE knowledge_summaries ADD COLUMN started_at TIMESTAMPTZ;
ALTER TABLE knowledge_summaries ADD COLUMN last_success_at TIMESTAMPTZ;
ALTER TABLE intelligence_runs ADD COLUMN started_at TIMESTAMPTZ;
