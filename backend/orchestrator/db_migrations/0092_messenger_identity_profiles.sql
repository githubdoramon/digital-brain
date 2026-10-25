-- Provider identifiers remain immutable message foreign keys.
ALTER TABLE messaging_identities
    ADD COLUMN source_username TEXT,
    ADD COLUMN source_phone TEXT,
    ADD COLUMN profile_observed_at TIMESTAMPTZ,
    ADD COLUMN profile_next_refresh_at TIMESTAMPTZ;
CREATE INDEX messaging_identity_refresh ON messaging_identities(account_id, profile_next_refresh_at, identity_id);

-- Recover already-retained direct-chat metadata. All identities are eligible for
-- provider refresh, including people seen only in old group messages.
UPDATE messaging_identities i
SET source_username = c.source_username, source_phone = c.source_phone
FROM messaging_conversations c, messaging_accounts a
WHERE c.account_id = i.account_id AND c.external_id = i.external_id
  AND c.kind = 'direct' AND a.account_id = i.account_id AND a.provider = 'telegram';

CREATE TABLE messaging_identity_mappings (
    account_id TEXT NOT NULL REFERENCES messaging_accounts ON DELETE CASCADE,
    lid TEXT NOT NULL CHECK (lid ~ '^[1-9][0-9]*@lid$'),
    phone TEXT NOT NULL CHECK (phone ~ '^[1-9][0-9]{6,14}@s[.]whatsapp[.]net$'),
    verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    conflicted BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY(account_id, lid, phone)
);
CREATE UNIQUE INDEX messaging_verified_lid ON messaging_identity_mappings(account_id, lid) WHERE NOT conflicted;
CREATE UNIQUE INDEX messaging_verified_phone ON messaging_identity_mappings(account_id, phone) WHERE NOT conflicted;
