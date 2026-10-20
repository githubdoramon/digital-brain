"""Durable per-owner fairness shared by the two background extraction workers."""

MAX_CURRENT_TURNS = 2
INTELLIGENCE_BATCHES_PER_TURN = 1
_TABLES = {"knowledge": "knowledge_backfills", "intelligence": "intelligence_state"}


def history_first(current_streak):
    return current_streak >= MAX_CURRENT_TURNS


def record_claim(cur, queue, owner, historical):
    # Tables are internal constants; the caller holds the owner's scheduling row.
    cur.execute(
        f"""UPDATE {_TABLES[queue]} SET current_streak=CASE WHEN %s THEN 0 ELSE LEAST(current_streak+1,%s) END,
        last_claimed_at=NOW(),last_history_claimed_at=CASE WHEN %s THEN NOW() ELSE last_history_claimed_at END
        WHERE user_email=%s""",
        (historical, MAX_CURRENT_TURNS, historical, owner),
    )


def status(cur, owner):
    result = {
        "max_current_turns": MAX_CURRENT_TURNS,
        "intelligence_batches_per_turn": INTELLIGENCE_BATCHES_PER_TURN,
    }
    for name, table in _TABLES.items():
        cur.execute(
            f"SELECT current_streak,last_claimed_at,last_history_claimed_at FROM {table} WHERE user_email=%s",
            (owner,),
        )
        row = cur.fetchone()
        result[name] = dict(row) if row else None
    return result
