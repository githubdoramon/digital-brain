CREATE INDEX messaging_sender_recency ON messaging_messages
    (account_id, sender_identity_id, sent_at DESC, message_id DESC)
    WHERE NOT deleted AND body <> '';
