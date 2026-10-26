"""Read-only export of retained rejection details and exact source chunks."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_conn
from intelligence import CHUNK_SIZE


def snapshot(owner):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout = '30s'")
        cur.execute(
            """WITH failures AS (
            SELECT 'knowledge' AS pipeline,user_email,source_id,part,attempts,
                error_code,error_details,failed_at,batch_limit,next_attempt_at,
                FALSE AS review,status FROM knowledge_learning
            WHERE user_email=%s AND error_code IS NOT NULL AND status<>'done'
            UNION ALL
            SELECT 'scheduled',user_email,source_id,part,attempts,error_code,
                error_details,failed_at,batch_limit,next_attempt_at,review,
                CASE WHEN next_attempt_at IS NULL THEN 'paused' ELSE 'retrying' END
            FROM intelligence_failures f WHERE user_email=%s AND NOT knowledge_only
                AND (review OR NOT EXISTS (SELECT 1 FROM intelligence_processed p
                    WHERE p.user_email=f.user_email AND p.source_id=f.source_id AND p.part=f.part))
        ) SELECT f.*,s.kind,s.entity_id,s.observed_at,
            s.payload->>'sent_at' AS sent_at,s.payload->>'title' AS title,
            substring(COALESCE(s.payload->>'text','') FROM f.part*%s+1 FOR %s) AS source_chunk
        FROM failures f JOIN intelligence_sources s USING(source_id)
        WHERE (s.owner_email IS NULL OR s.owner_email=%s) AND s.duplicate_of_source_id IS NULL
        ORDER BY pipeline,source_id,part,review""",
            (owner, owner, CHUNK_SIZE, CHUNK_SIZE + 400, owner),
        )
        rows = [dict(row) for row in cur.fetchall()]
    return {
        "counts": dict(Counter(f"{row['pipeline']}:{row['error_code']}" for row in rows)),
        "failures": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.owner), default=str, ensure_ascii=False, indent=2))
