CREATE TABLE IF NOT EXISTS contact_places (
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
  place_id TEXT NOT NULL REFERENCES places(place_id) ON DELETE CASCADE,
  role TEXT,
  source TEXT,
  confidence TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (contact_id, place_id)
);

CREATE INDEX IF NOT EXISTS idx_contact_places_contact_id
  ON contact_places (contact_id);

CREATE INDEX IF NOT EXISTS idx_contact_places_place_id
  ON contact_places (place_id);

CREATE INDEX IF NOT EXISTS idx_contact_places_contact_role
  ON contact_places (contact_id, role);
