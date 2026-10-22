"""Bounded asynchronous learning of original evidence selected by authenticated retrieval."""

import intelligence
import learning_ownership
from db import get_conn
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


def queue_retrieved(owner, results):
    if not owner:
        return 0
    refs = []
    for result in results:
        kind = result.get("kind")
        kind = "chat" if kind == "conversation" else kind
        ident = str(result.get("id") or result.get("message_id") or "")
        if kind in {"chat", "message", "event", "document"} and ident and (kind, ident) not in refs:
            refs.append((kind, ident))
        if len(refs) >= 5:
            break
    if not refs:
        return 0
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""WITH selected AS (
                SELECT DISTINCT ON(s.kind,s.entity_id) s.* FROM intelligence_sources s
                JOIN unnest(%s::text[],%s::text[]) ref(kind,id) ON ref.kind=s.kind AND ref.id=s.entity_id
                WHERE (s.owner_email IS NULL OR s.owner_email=%s)
                    AND s.duplicate_of_source_id IS NULL
                ORDER BY s.kind,s.entity_id,s.source_id DESC
            ), parts AS (
                SELECT s.source_id,p.part FROM selected s
                JOIN intelligence_state st ON st.user_email=%s
                CROSS JOIN LATERAL generate_series(0,GREATEST(0,
                    (length(COALESCE(s.payload->>'text',''))-1)/%s)) p(part)
                LEFT JOIN knowledge_learning j ON j.user_email=st.user_email AND j.source_id=s.source_id AND j.part=p.part
                WHERE knowledge_source_learnable(s.payload) AND NOT COALESCE((s.payload->>'deleted')::boolean,FALSE)
                    AND NOT {learning_ownership.automatic_source_sql()}
                    AND (j.source_id IS NULL OR ((j.status IN ('pending','failed') OR (j.status='running' AND (j.lease_until IS NULL OR j.lease_until<=NOW()))) AND j.attempts<3 AND NOT j.on_demand))
                ORDER BY s.source_id,p.part LIMIT 20
            ) INSERT INTO knowledge_learning(user_email,source_id,part,historical,on_demand)
                SELECT %s,source_id,part,TRUE,TRUE FROM parts
            ON CONFLICT(user_email,source_id,part) DO UPDATE SET on_demand=TRUE,historical=TRUE,status='pending',lease_token=NULL,lease_until=NULL,
                next_attempt_at=LEAST(knowledge_learning.next_attempt_at,NOW())
                WHERE (knowledge_learning.status IN ('pending','failed') OR (knowledge_learning.status='running'
                    AND (knowledge_learning.lease_until IS NULL OR knowledge_learning.lease_until<=NOW())))
                AND knowledge_learning.attempts<3""",
                (
                    [k for k, _ in refs],
                    [i for _, i in refs],
                    owner,
                    owner,
                    intelligence.CHUNK_SIZE,
                    owner,
                ),
            )
            count = cur.rowcount
            conn.commit()
        if count:
            logger.info("[knowledge] on_demand_queued source_parts=%d", count)
        return count
    except Exception:
        # Retrieval remains usable during a database/migration outage.
        logger.warning("[knowledge] on_demand_queue_unavailable")
        return 0
