CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Contacts (IDs + aliases for fuzzy resolution)
CREATE TABLE IF NOT EXISTS contacts (
  contact_id TEXT PRIMARY KEY,         -- e.g., 'contact:monica#123'
  display_name TEXT NOT NULL,
  aliases TEXT[] DEFAULT '{}'::TEXT[]
);

-- Places (canonical venue rows)
CREATE TABLE IF NOT EXISTS places (
  place_id TEXT PRIMARY KEY,           -- e.g., 'plc_cafe_martim_moniz'
  name TEXT,
  city TEXT,
  country TEXT,
  lat DOUBLE PRECISION,
  lon DOUBLE PRECISION,
  geohash TEXT
);

-- Events (your memories)
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL,
  place_id TEXT REFERENCES places(place_id),
  people TEXT[] DEFAULT '{}'::TEXT[],
  tags TEXT[] DEFAULT '{}'::TEXT[],
  what_text TEXT,
  raw JSONB DEFAULT '{}'::JSONB,
  what_embed VECTOR(768),              -- 768 for nomic-embed-text, 1536 for OpenAI text-embedding-3-*
  what_tsv tsvector
);

-- FTS trigger to keep what_tsv updated
CREATE OR REPLACE FUNCTION events_tsv_update() RETURNS trigger AS $$
BEGIN
  NEW.what_tsv := to_tsvector('english', coalesce(NEW.what_text,''));
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_tsv_trg ON events;
CREATE TRIGGER events_tsv_trg
BEFORE INSERT OR UPDATE OF what_text ON events
FOR EACH ROW EXECUTE FUNCTION events_tsv_update();

-- Indices
CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS idx_events_people ON events USING GIN (people);
CREATE INDEX IF NOT EXISTS idx_events_tags ON events USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_events_what_tsv ON events USING GIN (what_tsv);
-- Vector index (IVFFLAT) – build after some rows exist for best perf.
CREATE INDEX IF NOT EXISTS idx_events_embed ON events USING ivfflat (what_embed) WITH (lists = 100);

-- Helpful view
CREATE OR REPLACE VIEW events_with_places AS
SELECT e.*, p.name AS place_name, p.city, p.country, p.lat, p.lon
FROM events e LEFT JOIN places p ON p.place_id = e.place_id;
