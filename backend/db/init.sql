-- Ensure we always use the brain schema (psql meta-commands removed for DBeaver)
CREATE SCHEMA IF NOT EXISTS brain;
SET search_path TO brain, public;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- Contacts (IDs + aliases for fuzzy resolution)
CREATE TABLE IF NOT EXISTS contacts (
  contact_id TEXT PRIMARY KEY,         -- e.g., 'contact:monica#123'
  display_name TEXT NOT NULL,
  aliases TEXT[] DEFAULT '{}'::TEXT[],
  birthday DATE,
  emails TEXT[] DEFAULT '{}'::TEXT[],
  phones TEXT[] DEFAULT '{}'::TEXT[],
  links TEXT[] DEFAULT '{}'::TEXT[],
  tags TEXT[] DEFAULT '{}'::TEXT[],
  comments TEXT,
  external_id TEXT UNIQUE
);

ALTER TABLE contacts
  ADD COLUMN IF NOT EXISTS comments TEXT,
  ADD COLUMN IF NOT EXISTS external_id TEXT UNIQUE;

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
DO $$
DECLARE
  rel_kind CHAR;
  target_schema TEXT := current_schema();
BEGIN
  SELECT c.relkind
  INTO rel_kind
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relname = 'events'
    AND n.nspname = target_schema
  LIMIT 1;

  IF rel_kind = 'v' THEN
    EXECUTE format('DROP VIEW %I.events CASCADE', target_schema);
  ELSIF rel_kind = 'm' THEN
    EXECUTE format('DROP MATERIALIZED VIEW %I.events CASCADE', target_schema);
  END IF;
END;
$$;

-- Drop dependent views so column renames don't fail on legacy schemas.
DROP VIEW IF EXISTS events_with_places CASCADE;

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  start_date TIMESTAMPTZ NOT NULL,
  end_date TIMESTAMPTZ,
  place_id TEXT REFERENCES places(place_id),
  people TEXT[] DEFAULT '{}'::TEXT[],
  tags TEXT[] DEFAULT '{}'::TEXT[],
  types TEXT[] DEFAULT ARRAY['generic']::TEXT[],
  title TEXT,
  summary TEXT,
  raw JSONB DEFAULT '{}'::JSONB,
  external_id TEXT UNIQUE,
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

DO $$
DECLARE
  events_is_table BOOLEAN;
BEGIN
  SELECT EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = current_schema()
      AND table_name = 'events'
      AND table_type = 'BASE TABLE'
  )
  INTO events_is_table;

  IF events_is_table THEN
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = 'events'
        AND column_name = 'ts'
    ) THEN
      EXECUTE 'ALTER TABLE events RENAME COLUMN ts TO start_date';
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = 'events'
        AND column_name = 'end_date'
    ) THEN
      EXECUTE 'ALTER TABLE events ADD COLUMN end_date TIMESTAMPTZ';
    END IF;

    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = 'events'
        AND column_name = 'what_text'
    ) THEN
      EXECUTE 'ALTER TABLE events RENAME COLUMN what_text TO title';
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = 'events'
        AND column_name = 'summary'
    ) THEN
      EXECUTE 'ALTER TABLE events ADD COLUMN summary TEXT';
      -- seed summary with title/legacy text when available
      EXECUTE 'UPDATE events SET summary = title WHERE summary IS NULL';
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = 'events'
        AND column_name = 'external_id'
    ) THEN
      EXECUTE 'ALTER TABLE events ADD COLUMN external_id TEXT UNIQUE';
    END IF;
  END IF;
END;
$$;

-- FTS trigger to keep what_tsv updated
CREATE OR REPLACE FUNCTION events_tsv_update() RETURNS trigger AS $$
BEGIN
  NEW.what_tsv := to_tsvector(
    'english',
    unaccent(coalesce(NEW.title,'') || ' ' || coalesce(NEW.summary,''))
  );
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS events_tsv_trg ON events;
CREATE TRIGGER events_tsv_trg
BEFORE INSERT OR UPDATE OF title, summary ON events
FOR EACH ROW EXECUTE FUNCTION events_tsv_update();

-- Indices
CREATE INDEX IF NOT EXISTS idx_events_start_date ON events (start_date);
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
  description TEXT,
  file_path TEXT NOT NULL,
  file_name TEXT NOT NULL,
  file_mime TEXT,
  file_size BIGINT,
  document_date TIMESTAMPTZ,
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
  NEW.content_tsv := to_tsvector(
    'english',
    unaccent(coalesce(NEW.content, '') || ' ' || coalesce(NEW.description, ''))
  );
  RETURN NEW;
END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS documents_tsv_trg ON documents;
CREATE TRIGGER documents_tsv_trg
BEFORE INSERT OR UPDATE OF content, description ON documents
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

-- Main sessions (quick chat mode - one main session per user)
CREATE TABLE IF NOT EXISTS main_sessions (
  user_email TEXT PRIMARY KEY,
  current_thread_id TEXT REFERENCES conversation_threads(id) ON DELETE SET NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Action logs (system actions such as gate access)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_type WHERE typname = 'action_log_type'
  ) THEN
    CREATE TYPE action_log_type AS ENUM ('person_identified','gate_opened');
  END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS action_logs (
  id TEXT PRIMARY KEY,
  log_type action_log_type NOT NULL,
  raw JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Helpful view
CREATE OR REPLACE VIEW events_with_places AS
SELECT e.*, p.name AS place_name, p.city, p.country, p.lat, p.lon
FROM events e LEFT JOIN places p ON p.place_id = e.place_id;
