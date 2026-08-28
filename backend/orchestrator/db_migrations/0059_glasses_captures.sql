CREATE TABLE IF NOT EXISTS glasses_captures (
  user_email TEXT NOT NULL,
  capture_id TEXT NOT NULL,
  checksum TEXT NOT NULL,
  immich_asset_id TEXT NOT NULL,
  immich_album_id TEXT,
  original_file_name TEXT,
  mime_type TEXT,
  captured_at TIMESTAMPTZ,
  location JSONB NOT NULL DEFAULT '{}'::JSONB,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_email, capture_id),
  UNIQUE (user_email, checksum)
);

CREATE INDEX IF NOT EXISTS idx_glasses_captures_asset_id
  ON glasses_captures (immich_asset_id);
