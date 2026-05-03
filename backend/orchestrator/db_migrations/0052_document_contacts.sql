CREATE TABLE IF NOT EXISTS document_contacts (
  document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
  contact_id TEXT NOT NULL REFERENCES contacts(contact_id) ON DELETE CASCADE,
  role TEXT,
  source TEXT,
  confidence TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (document_id, contact_id)
);

CREATE INDEX IF NOT EXISTS idx_document_contacts_document_id
  ON document_contacts (document_id);

CREATE INDEX IF NOT EXISTS idx_document_contacts_contact_id
  ON document_contacts (contact_id);

CREATE INDEX IF NOT EXISTS idx_document_contacts_contact_role
  ON document_contacts (contact_id, role);
