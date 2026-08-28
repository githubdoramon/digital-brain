ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS enhancement_status TEXT NOT NULL DEFAULT 'complete',
  ADD COLUMN IF NOT EXISTS enhancement_error TEXT;

CREATE INDEX IF NOT EXISTS idx_documents_enhancement_status
  ON documents (enhancement_status, created_at DESC);

UPDATE documents
SET enhancement_status = 'pending'
WHERE content IS NULL OR content_embed IS NULL;
