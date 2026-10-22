"""Canonical historical source classification shared by seeding and coverage."""

ARCHIVE_HISTORY_SQL = """(s.kind IN ('message','event','document') AND
    (s.historical OR s.observed_at<st.started_at
        OR COALESCE((s.payload->>'sent_at')::timestamptz<st.started_at,FALSE)))"""
ELIGIBLE_SOURCE_SQL = f"""knowledge_source_learnable(s.payload) AND (s.observed_at<=b.seeded_at OR s.kind IN ('chat','contact_note')
    OR {ARCHIVE_HISTORY_SQL})"""


def history_months():
    import os

    return max(1, min(1200, int(os.getenv("KNOWLEDGE_HISTORY_MONTHS", "6"))))


def automatic_source_sql():
    # Source dates, never import dates, determine archive age when known. Undated
    # manual knowledge/contact notes remain eligible; evidence is never deleted.
    return f"""(s.kind NOT IN ('chat','message','event','document') OR
        COALESCE(NULLIF(s.payload->>'sent_at','')::timestamptz,s.observed_at)
            >= NOW()-make_interval(months => {history_months()}))"""
