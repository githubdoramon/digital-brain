CREATE TABLE IF NOT EXISTS voice_profiles (
  contact_id TEXT PRIMARY KEY REFERENCES contacts(contact_id) ON DELETE CASCADE,
  embedding_model TEXT NOT NULL,
  centroid VECTOR(256) NOT NULL,
  observation_count INTEGER NOT NULL DEFAULT 0,
  confirmed_observation_count INTEGER NOT NULL DEFAULT 0,
  last_observed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_voice_profiles_centroid
  ON voice_profiles USING ivfflat (centroid vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS voice_observations (
  observation_id TEXT PRIMARY KEY,
  contact_id TEXT REFERENCES contacts(contact_id) ON DELETE SET NULL,
  session_id TEXT,
  speaker_id TEXT NOT NULL,
  embedding_model TEXT NOT NULL,
  embedding VECTOR(256) NOT NULL,
  window_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  source TEXT NOT NULL DEFAULT 'confirmed_assignment',
  confirmed_at TIMESTAMPTZ,
  rejected_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_voice_observations_contact_id
  ON voice_observations (contact_id);

CREATE INDEX IF NOT EXISTS idx_voice_observations_session_speaker
  ON voice_observations (session_id, speaker_id);

CREATE TABLE IF NOT EXISTS speaker_match_events (
  match_event_id TEXT PRIMARY KEY,
  session_id TEXT,
  speaker_id TEXT NOT NULL,
  suggested_contact_id TEXT REFERENCES contacts(contact_id) ON DELETE SET NULL,
  corrected_contact_id TEXT REFERENCES contacts(contact_id) ON DELETE SET NULL,
  score DOUBLE PRECISION,
  margin DOUBLE PRECISION,
  status TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_speaker_match_events_session_speaker
  ON speaker_match_events (session_id, speaker_id);
