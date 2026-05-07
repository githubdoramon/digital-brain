CREATE TABLE IF NOT EXISTS event_photos (
  event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  immich_asset_id TEXT NOT NULL,
  checksum TEXT,
  original_file_name TEXT,
  mime_type TEXT,
  captured_at TIMESTAMPTZ,
  local_asset_id TEXT,
  source TEXT,
  width INTEGER,
  height INTEGER,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (event_id, immich_asset_id)
);

CREATE INDEX IF NOT EXISTS idx_event_photos_asset_id
  ON event_photos (immich_asset_id);

CREATE INDEX IF NOT EXISTS idx_event_photos_checksum
  ON event_photos (checksum);

CREATE TABLE IF NOT EXISTS event_photo_contacts (
  event_id TEXT NOT NULL,
  immich_asset_id TEXT NOT NULL,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
  source TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (event_id, immich_asset_id, contact_id),
  FOREIGN KEY (event_id, immich_asset_id)
    REFERENCES event_photos(event_id, immich_asset_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_photo_contacts_contact_id
  ON event_photo_contacts (contact_id);
