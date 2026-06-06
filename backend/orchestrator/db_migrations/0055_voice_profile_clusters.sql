CREATE TABLE IF NOT EXISTS voice_profile_clusters (
  cluster_id TEXT PRIMARY KEY,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
  embedding_model TEXT NOT NULL,
  centroid VECTOR(256) NOT NULL,
  observation_count INTEGER NOT NULL DEFAULT 0,
  confirmed_observation_count INTEGER NOT NULL DEFAULT 0,
  last_observed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_voice_profile_clusters_contact_id
  ON voice_profile_clusters (contact_id);

CREATE INDEX IF NOT EXISTS idx_voice_profile_clusters_centroid
  ON voice_profile_clusters USING ivfflat (centroid vector_cosine_ops) WITH (lists = 100);

INSERT INTO voice_profile_clusters (
  cluster_id,
  contact_id,
  embedding_model,
  centroid,
  observation_count,
  confirmed_observation_count,
  last_observed_at,
  created_at,
  updated_at
)
SELECT
  'voice-cluster:' || contact_id || ':default',
  contact_id,
  embedding_model,
  centroid,
  observation_count,
  confirmed_observation_count,
  last_observed_at,
  created_at,
  updated_at
FROM voice_profiles
WHERE centroid IS NOT NULL
ON CONFLICT (cluster_id) DO NOTHING;

ALTER TABLE voice_observations
  ADD COLUMN IF NOT EXISTS cluster_id TEXT REFERENCES voice_profile_clusters(cluster_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_voice_observations_cluster_id
  ON voice_observations (cluster_id);
