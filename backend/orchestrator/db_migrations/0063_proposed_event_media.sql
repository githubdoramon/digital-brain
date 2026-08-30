CREATE TABLE IF NOT EXISTS proposed_event_media (
  proposal_id TEXT NOT NULL REFERENCES proposed_events(proposal_id) ON DELETE CASCADE,
  immich_asset_id TEXT NOT NULL,
  media_type TEXT,
  original_file_name TEXT,
  mime_type TEXT,
  captured_at TIMESTAMPTZ,
  width INTEGER,
  height INTEGER,
  duration_seconds DOUBLE PRECISION,
  distance_m DOUBLE PRECISION,
  temporal_distance_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
  has_gps BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'included',
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (proposal_id, immich_asset_id),
  CHECK (status IN ('included', 'removed'))
);

CREATE INDEX IF NOT EXISTS idx_proposed_event_media_asset
  ON proposed_event_media (immich_asset_id);

CREATE INDEX IF NOT EXISTS idx_proposed_event_media_status
  ON proposed_event_media (proposal_id, status, temporal_distance_seconds);
