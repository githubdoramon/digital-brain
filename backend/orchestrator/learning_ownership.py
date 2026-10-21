"""Canonical historical source classification shared by seeding and coverage."""

ARCHIVE_HISTORY_SQL = """(s.kind IN ('message','event','document') AND
    (s.historical OR s.observed_at<st.started_at
        OR COALESCE((s.payload->>'sent_at')::timestamptz<st.started_at,FALSE)))"""
ELIGIBLE_SOURCE_SQL = f"""(s.observed_at<=b.seeded_at OR s.kind IN ('chat','contact_note')
    OR {ARCHIVE_HISTORY_SQL})"""
