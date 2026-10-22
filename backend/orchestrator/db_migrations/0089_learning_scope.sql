-- Keep all archived evidence and completed knowledge; older sources are demand-driven.
ALTER TABLE knowledge_learning ADD COLUMN IF NOT EXISTS on_demand BOOLEAN NOT NULL DEFAULT FALSE;
-- Larger sparse conversation episodes, preserving previously reduced limits.
ALTER TABLE knowledge_learning DROP CONSTRAINT IF EXISTS knowledge_learning_batch_limit_check;
ALTER TABLE knowledge_learning ADD CONSTRAINT knowledge_learning_batch_limit_check CHECK (batch_limit BETWEEN 1 AND 48);
ALTER TABLE knowledge_learning ALTER COLUMN batch_limit SET DEFAULT 48;
UPDATE knowledge_learning SET batch_limit=48 WHERE batch_limit=12;
