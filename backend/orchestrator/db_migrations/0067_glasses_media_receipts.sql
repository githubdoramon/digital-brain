CREATE TABLE IF NOT EXISTS glasses_media_receipts (
  user_email TEXT NOT NULL,
  capture_key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  album_id TEXT NOT NULL,
  captured_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_email, capture_key)
);
CREATE INDEX IF NOT EXISTS idx_glasses_media_checksum
  ON glasses_media_receipts (user_email, sha256);
