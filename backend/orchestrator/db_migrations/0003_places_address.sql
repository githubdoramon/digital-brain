ALTER TABLE places
  ADD COLUMN IF NOT EXISTS address TEXT;

CREATE INDEX IF NOT EXISTS idx_places_address ON places (address);
