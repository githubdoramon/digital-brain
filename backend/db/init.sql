CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Contacts (IDs + aliases for fuzzy resolution)
CREATE TABLE IF NOT EXISTS contacts (
  contact_id TEXT PRIMARY KEY,         -- e.g., 'contact:monica#123'
  display_name TEXT NOT NULL,
  aliases TEXT[] DEFAULT '{}'::TEXT[],
  birthday DATE,
  emails TEXT[] DEFAULT '{}'::TEXT[],
  phones TEXT[] DEFAULT '{}'::TEXT[],
  links TEXT[] DEFAULT '{}'::TEXT[],
  tags TEXT[] DEFAULT '{}'::TEXT[]
);

-- Contact relationships (flexible graph between contacts)
CREATE TABLE IF NOT EXISTS contact_relationships (
  relationship_id TEXT PRIMARY KEY,
  from_contact_id TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
  to_contact_id TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
  relationship_type TEXT NOT NULL,
  reciprocal_type TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (from_contact_id <> to_contact_id),
  CHECK (btrim(relationship_type) <> '')
);

CREATE INDEX IF NOT EXISTS idx_contact_relationships_from ON contact_relationships (from_contact_id);
CREATE INDEX IF NOT EXISTS idx_contact_relationships_to ON contact_relationships (to_contact_id);

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
  types TEXT[] DEFAULT ARRAY['generic']::TEXT[],
  what_text TEXT,
  raw JSONB DEFAULT '{}'::JSONB,
  what_embed VECTOR(768),              -- 768 for nomic-embed-text, 1536 for OpenAI text-embedding-3-*
  what_tsv tsvector,
  CHECK (
    types <@ ARRAY[
      'generic',
      'meeting',
      'communication',
      'task',
      'creation',
      'consumption',
      'travel',
      'personal',
      'system',
      'financial',
      'observation',
      'interaction',
      'education',
      'celebration',
      'purchase',
      'health'
    ]::TEXT[]
  )
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

-- Documents (uploaded files with embeddings)
CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  tags TEXT[] DEFAULT '{}'::TEXT[],
  summary TEXT,
  description TEXT,
  file_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_mime TEXT,
  file_size BIGINT,
  content TEXT,
  content_embed VECTOR(768),
  content_tsv tsvector,
  raw_metadata JSONB DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- FTS trigger for documents
CREATE OR REPLACE FUNCTION documents_tsv_update() RETURNS trigger AS $$
BEGIN
  NEW.content_tsv := to_tsvector('english', coalesce(NEW.content, '') || ' ' || coalesce(NEW.summary, '') || ' ' || coalesce(NEW.description, ''));
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS documents_tsv_trg ON documents;
CREATE TRIGGER documents_tsv_trg
BEFORE INSERT OR UPDATE OF content, summary, description ON documents
FOR EACH ROW EXECUTE FUNCTION documents_tsv_update();

-- Indices
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_tags ON documents USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_documents_content_tsv ON documents USING GIN (content_tsv);
CREATE INDEX IF NOT EXISTS idx_documents_embed ON documents USING ivfflat (content_embed) WITH (lists = 100);

-- TODOs (tasks with optional associations)
CREATE TABLE IF NOT EXISTS todos (
  todo_id TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  due_date DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (btrim(description) <> '')
);

CREATE TABLE IF NOT EXISTS todo_contacts (
  todo_id TEXT NOT NULL REFERENCES todos(todo_id) ON DELETE CASCADE,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
  PRIMARY KEY (todo_id, contact_id)
);

CREATE TABLE IF NOT EXISTS todo_events (
  todo_id TEXT NOT NULL REFERENCES todos(todo_id) ON DELETE CASCADE,
  event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  PRIMARY KEY (todo_id, event_id)
);

CREATE TABLE IF NOT EXISTS todo_places (
  todo_id TEXT NOT NULL REFERENCES todos(todo_id) ON DELETE CASCADE,
  place_id TEXT NOT NULL REFERENCES places(place_id) ON DELETE CASCADE,
  PRIMARY KEY (todo_id, place_id)
);

-- Conversation threads and messages for chat history
CREATE TABLE IF NOT EXISTS conversation_threads (
  id TEXT PRIMARY KEY,
  user_email TEXT NOT NULL,
  title TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_threads_user_updated
  ON conversation_threads (user_email, updated_at DESC);

CREATE TABLE IF NOT EXISTS conversation_messages (
  message_id BIGSERIAL PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES conversation_threads(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_thread_created
  ON conversation_messages (thread_id, created_at, message_id);

-- Helpful view
CREATE OR REPLACE VIEW events_with_places AS
SELECT e.*, p.name AS place_name, p.city, p.country, p.lat, p.lon
FROM events e LEFT JOIN places p ON p.place_id = e.place_id;
