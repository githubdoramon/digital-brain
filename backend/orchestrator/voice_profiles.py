from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

import contacts as contacts_service
from db import get_conn
from observability.logger import get_runtime_logger
from schemas import (
    ConfirmedSpeakerVoiceObservation,
    SpeakerVoiceConfirmIn,
    SpeakerVoiceMatchAssignment,
    SpeakerVoiceMatchCandidate,
    SpeakerVoiceMatchIn,
    SpeakerVoiceMatchOut,
    VoiceMatchParticipant,
    VoiceSpeakerObservation,
)

logger = get_runtime_logger(__name__)

VOICE_EMBEDDING_DIM = 256
AUTO_SCORE_THRESHOLD = 0.84
AUTO_MARGIN_THRESHOLD = 0.07
SUGGEST_SCORE_THRESHOLD = 0.74
SUGGEST_MARGIN_THRESHOLD = 0.04
PARTICIPANT_SCORE_BOOST = 0.025
MAX_ALTERNATES = 3
CLUSTER_UPDATE_SCORE_THRESHOLD = 0.78
MAX_CLUSTERS_PER_CONTACT = 5


def match_speakers(payload: SpeakerVoiceMatchIn, *, current_user: dict | None = None) -> SpeakerVoiceMatchOut:
    del current_user
    participant_contact_ids = _resolve_participant_contact_ids(payload.participants)
    profiles = _load_voice_profiles()
    assignments: list[SpeakerVoiceMatchAssignment] = []
    proposed_auto_contact_ids: set[str] = set()
    pending_auto: list[tuple[SpeakerVoiceMatchAssignment, str]] = []

    for observation in payload.speaker_observations:
        centroid = _observation_centroid(observation)
        if centroid is None:
            assignments.append(
                SpeakerVoiceMatchAssignment(
                    speaker_id=observation.speaker_id,
                    action="none",
                    reason="no_valid_embeddings",
                )
            )
            continue

        candidates = _rank_candidates(
            centroid,
            profiles,
            participant_contact_ids=participant_contact_ids,
        )
        if not candidates:
            assignments.append(
                SpeakerVoiceMatchAssignment(
                    speaker_id=observation.speaker_id,
                    action="none",
                    reason="no_voice_profiles",
                )
            )
            continue

        top = candidates[0]
        alternates = candidates[1:MAX_ALTERNATES]
        if top.confidence == "high":
            assignment = SpeakerVoiceMatchAssignment(
                speaker_id=observation.speaker_id,
                action="auto_label",
                candidate=top,
                alternates=alternates,
            )
            pending_auto.append((assignment, top.contact_id))
            continue

        assignments.append(
            SpeakerVoiceMatchAssignment(
                speaker_id=observation.speaker_id,
                action="suggest",
                candidate=top,
                alternates=alternates,
                reason="below_auto_threshold",
            )
        )

    duplicate_auto_contacts = {
        contact_id
        for contact_id in [contact_id for _assignment, contact_id in pending_auto]
        if [candidate for _assignment, candidate in pending_auto].count(contact_id) > 1
    }

    for assignment, contact_id in pending_auto:
        if contact_id in duplicate_auto_contacts or contact_id in proposed_auto_contact_ids:
            assignments.append(
                SpeakerVoiceMatchAssignment(
                    speaker_id=assignment.speaker_id,
                    action="suggest",
                    candidate=assignment.candidate,
                    alternates=assignment.alternates,
                    reason="duplicate_auto_contact",
                )
            )
            continue
        proposed_auto_contact_ids.add(contact_id)
        assignments.append(assignment)

    logger.info(
        "[voice_profiles] matched speakers session_id=%s speakers=%d assignments=%d",
        payload.session_id,
        len(payload.speaker_observations),
        len(assignments),
    )
    return SpeakerVoiceMatchOut(status="done", assignments=assignments)


def confirm_speaker_profiles(payload: SpeakerVoiceConfirmIn) -> dict[str, Any]:
    confirmed_count = 0
    rejected_count = 0

    for observation in payload.observations:
        contact_id = _resolve_confirmed_contact_id(observation)
        if not contact_id:
            continue
        embeddings = _valid_embeddings(observation.embeddings, observation.embedding_dim)
        if not embeddings:
            continue
        cluster_id = _upsert_voice_profile(contact_id, observation.embedding_model, embeddings)
        _persist_confirmed_observation(payload.session_id, observation, embeddings, cluster_id, contact_id)
        confirmed_count += len(embeddings)

    for rejected in payload.rejected_matches:
        speaker_id = str(rejected.get("speaker_id") or rejected.get("speakerId") or "").strip()
        suggested_contact_id = str(
            rejected.get("suggested_contact_id") or rejected.get("suggestedContactId") or ""
        ).strip() or None
        corrected_contact_id = str(
            rejected.get("corrected_contact_id") or rejected.get("correctedContactId") or ""
        ).strip() or None
        if not speaker_id:
            continue
        _persist_match_event(
            session_id=payload.session_id,
            speaker_id=speaker_id,
            suggested_contact_id=suggested_contact_id,
            corrected_contact_id=corrected_contact_id,
            score=_float_or_none(rejected.get("score")),
            margin=_float_or_none(rejected.get("margin")),
            status="rejected",
            metadata=rejected,
        )
        rejected_count += 1

    return {
        "confirmed_observation_count": confirmed_count,
        "rejected_match_count": rejected_count,
    }


def _resolve_participant_contact_ids(participants: Sequence[VoiceMatchParticipant]) -> set[str]:
    contact_ids: set[str] = set()
    for participant in participants:
        contact_id = str(participant.contact_id or "").strip()
        if contact_id:
            contact_ids.add(contact_id)
            continue

        email = contacts_service.normalize_email(participant.email or "")
        if email:
            resolved, _created = contacts_service.ensure_contact_for_email(
                email,
                display_name=participant.name,
            )
            if resolved:
                contact_ids.add(resolved)
    return contact_ids


def _resolve_confirmed_contact_id(observation: ConfirmedSpeakerVoiceObservation) -> str | None:
    contact_id = str(observation.contact_id or "").strip()
    if contact_id:
        return contact_id

    email = contacts_service.normalize_email(observation.email or "")
    if email:
        resolved, _created = contacts_service.ensure_contact_for_email(
            email,
            display_name=observation.name,
        )
        if resolved:
            return resolved

    name = str(observation.name or "").strip()
    if not name:
        return None

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT contact_id
            FROM contacts
            WHERE unaccent(lower(display_name)) = unaccent(lower(%s))
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (name,),
        )
        row = cur.fetchone()
    return str(row["contact_id"]) if row else None


def _load_voice_profiles() -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT vpc.cluster_id,
                   vpc.contact_id,
                   vpc.embedding_model,
                   vpc.centroid,
                   vpc.observation_count,
                   c.display_name,
                   c.emails
            FROM voice_profile_clusters vpc
            JOIN contacts c ON c.contact_id = vpc.contact_id
            WHERE vpc.centroid IS NOT NULL
            """
        )
        return [dict(row) for row in cur.fetchall()]


def _rank_candidates(
    centroid: list[float],
    profiles: Sequence[dict[str, Any]],
    *,
    participant_contact_ids: set[str],
) -> list[SpeakerVoiceMatchCandidate]:
    raw: list[dict[str, Any]] = []
    for profile in profiles:
        contact_id = str(profile.get("contact_id") or "").strip()
        profile_centroid = _coerce_vector(profile.get("centroid"))
        if not contact_id or not profile_centroid:
            continue
        score = _cosine_similarity(centroid, profile_centroid)
        is_participant = contact_id in participant_contact_ids
        ranked_score = score + (PARTICIPANT_SCORE_BOOST if is_participant else 0.0)
        raw.append(
            {
                "contact_id": contact_id,
                "cluster_id": profile.get("cluster_id"),
                "name": profile.get("display_name"),
                "email": _first_email(profile.get("emails")),
                "score": score,
                "ranked_score": ranked_score,
                "is_participant": is_participant,
            }
        )

    raw.sort(key=lambda candidate: candidate["ranked_score"], reverse=True)
    best_by_contact: list[dict[str, Any]] = []
    seen_contact_ids: set[str] = set()
    for candidate in raw:
        if candidate["contact_id"] in seen_contact_ids:
            continue
        seen_contact_ids.add(candidate["contact_id"])
        best_by_contact.append(candidate)

    candidates: list[SpeakerVoiceMatchCandidate] = []
    for index, candidate in enumerate(best_by_contact[:MAX_ALTERNATES]):
        next_score = best_by_contact[index + 1]["ranked_score"] if index + 1 < len(best_by_contact) else None
        margin = None if next_score is None else candidate["ranked_score"] - next_score
        confidence = _confidence(candidate["ranked_score"], margin)
        candidates.append(
            SpeakerVoiceMatchCandidate(
                contact_id=candidate["contact_id"],
                name=candidate["name"],
                email=candidate["email"],
                score=round(float(candidate["score"]), 6),
                margin=round(float(margin), 6) if margin is not None else None,
                confidence=confidence,
                is_participant=bool(candidate["is_participant"]),
            )
        )
    return candidates


def _confidence(score: float, margin: float | None) -> str:
    effective_margin = margin if margin is not None else 1.0
    if score >= AUTO_SCORE_THRESHOLD and effective_margin >= AUTO_MARGIN_THRESHOLD:
        return "high"
    if score >= SUGGEST_SCORE_THRESHOLD and effective_margin >= SUGGEST_MARGIN_THRESHOLD:
        return "medium"
    return "low"


def _observation_centroid(observation: VoiceSpeakerObservation) -> list[float] | None:
    embeddings = _valid_embeddings(observation.embeddings, observation.embedding_dim)
    return _centroid(embeddings)


def _valid_embeddings(embeddings: Sequence[Sequence[float]], embedding_dim: int) -> list[list[float]]:
    if embedding_dim != VOICE_EMBEDDING_DIM:
        return []
    valid: list[list[float]] = []
    for embedding in embeddings:
        if len(embedding) != VOICE_EMBEDDING_DIM:
            continue
        vector = [float(value) for value in embedding]
        if all(math.isfinite(value) for value in vector):
            valid.append(_normalize(vector))
    return valid


def _centroid(embeddings: Sequence[Sequence[float]]) -> list[float] | None:
    if not embeddings:
        return None
    dim = len(embeddings[0])
    sums = [0.0] * dim
    count = 0
    for embedding in embeddings:
        if len(embedding) != dim:
            continue
        count += 1
        for idx, value in enumerate(embedding):
            sums[idx] += value
    if count == 0:
        return None
    return _normalize([value / count for value in sums])


def _normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0:
        return [float(value) for value in vector]
    return [float(value) / norm for value in vector]


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a * b for a, b in zip(_normalize(left), _normalize(right)))


def _coerce_vector(value: Any) -> list[float]:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            items = text[1:-1].split(",")
            return [float(item) for item in items if item.strip()]
        return []
    if isinstance(value, Sequence):
        return [float(item) for item in value]
    return []


def _persist_confirmed_observation(
    session_id: str | None,
    observation: ConfirmedSpeakerVoiceObservation,
    embeddings: Sequence[Sequence[float]],
    cluster_id: str | None,
    contact_id: str,
) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        for index, embedding in enumerate(embeddings):
            window = observation.windows[index] if index < len(observation.windows) else None
            metadata = window.model_dump(by_alias=True, mode="json") if window else {}
            cur.execute(
                """
                INSERT INTO voice_observations (
                  observation_id,
                  contact_id,
                  cluster_id,
                  session_id,
                  speaker_id,
                  embedding_model,
                  embedding,
                  window_metadata,
                  source,
                  confirmed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (observation_id) DO NOTHING
                """,
                (
                    f"voice-observation:{session_id or 'unknown'}:{observation.speaker_id}:{uuid4().hex[:12]}",
                    contact_id,
                    cluster_id,
                    session_id,
                    observation.speaker_id,
                    observation.embedding_model,
                    list(embedding),
                    metadata,
                    observation.source or "confirmed_assignment",
                ),
            )
        conn.commit()


def _upsert_voice_profile(
    contact_id: str,
    embedding_model: str,
    embeddings: Sequence[Sequence[float]],
) -> str | None:
    incoming_centroid = _centroid(embeddings)
    if incoming_centroid is None:
        return None
    incoming_count = len(embeddings)

    with get_conn() as conn, conn.cursor() as cur:
        _upsert_voice_profile_summary(
            cur,
            contact_id,
            embedding_model,
            incoming_centroid,
            incoming_count,
        )
        cluster_id = _upsert_voice_profile_cluster(
            cur,
            contact_id,
            embedding_model,
            incoming_centroid,
            incoming_count,
        )
        conn.commit()
    return cluster_id


def _upsert_voice_profile_summary(
    cur: Any,
    contact_id: str,
    embedding_model: str,
    incoming_centroid: Sequence[float],
    incoming_count: int,
) -> None:
    cur.execute(
        """
        SELECT centroid, confirmed_observation_count
        FROM voice_profiles
        WHERE contact_id = %s
        """,
        (contact_id,),
    )
    row = cur.fetchone()
    if row:
        existing = dict(row)
        existing_centroid = _coerce_vector(existing.get("centroid"))
        existing_count = int(existing.get("confirmed_observation_count") or 0)
        combined = _weighted_centroid(
            existing_centroid,
            existing_count,
            incoming_centroid,
            incoming_count,
        )
        total = existing_count + incoming_count
    else:
        combined = _normalize(incoming_centroid)
        total = incoming_count

    cur.execute(
        """
        INSERT INTO voice_profiles (
          contact_id,
          embedding_model,
          centroid,
          observation_count,
          confirmed_observation_count,
          last_observed_at
        )
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (contact_id) DO UPDATE
          SET embedding_model = EXCLUDED.embedding_model,
              centroid = EXCLUDED.centroid,
              observation_count = voice_profiles.observation_count + EXCLUDED.observation_count,
              confirmed_observation_count = EXCLUDED.confirmed_observation_count,
              last_observed_at = NOW(),
              updated_at = NOW()
        """,
        (
            contact_id,
            embedding_model,
            combined,
            incoming_count,
            total,
        ),
    )


def _upsert_voice_profile_cluster(
    cur: Any,
    contact_id: str,
    embedding_model: str,
    incoming_centroid: Sequence[float],
    incoming_count: int,
) -> str:
    cur.execute(
        """
        SELECT cluster_id, centroid, confirmed_observation_count, created_at
        FROM voice_profile_clusters
        WHERE contact_id = %s
        ORDER BY confirmed_observation_count DESC, updated_at DESC
        """,
        (contact_id,),
    )
    clusters = [dict(row) for row in cur.fetchall()]
    nearest = _nearest_cluster(incoming_centroid, clusters)
    if nearest and nearest["score"] >= CLUSTER_UPDATE_SCORE_THRESHOLD:
        cluster = nearest["cluster"]
        cluster_id = str(cluster["cluster_id"])
        existing_centroid = _coerce_vector(cluster.get("centroid"))
        existing_count = int(cluster.get("confirmed_observation_count") or 0)
        combined = _weighted_centroid(
            existing_centroid,
            existing_count,
            incoming_centroid,
            incoming_count,
        )
        cur.execute(
            """
            UPDATE voice_profile_clusters
            SET embedding_model = %s,
                centroid = %s,
                observation_count = observation_count + %s,
                confirmed_observation_count = confirmed_observation_count + %s,
                last_observed_at = NOW(),
                updated_at = NOW()
            WHERE cluster_id = %s
            """,
            (
                embedding_model,
                combined,
                incoming_count,
                incoming_count,
                cluster_id,
            ),
        )
        return cluster_id

    if len(clusters) >= MAX_CLUSTERS_PER_CONTACT:
        weakest = clusters[-1]
        cluster_id = str(weakest["cluster_id"])
        cur.execute(
            """
            UPDATE voice_profile_clusters
            SET embedding_model = %s,
                centroid = %s,
                observation_count = %s,
                confirmed_observation_count = %s,
                last_observed_at = NOW(),
                updated_at = NOW()
            WHERE cluster_id = %s
            """,
            (
                embedding_model,
                _normalize(incoming_centroid),
                incoming_count,
                incoming_count,
                cluster_id,
            ),
        )
        return cluster_id

    cluster_id = f"voice-cluster:{contact_id}:{uuid4().hex[:12]}"
    cur.execute(
        """
        INSERT INTO voice_profile_clusters (
          cluster_id,
          contact_id,
          embedding_model,
          centroid,
          observation_count,
          confirmed_observation_count,
          last_observed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """,
        (
            cluster_id,
            contact_id,
            embedding_model,
            _normalize(incoming_centroid),
            incoming_count,
            incoming_count,
        ),
    )
    return cluster_id


def _nearest_cluster(
    centroid: Sequence[float],
    clusters: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    for cluster in clusters:
        cluster_centroid = _coerce_vector(cluster.get("centroid"))
        if not cluster_centroid:
            continue
        score = _cosine_similarity(centroid, cluster_centroid)
        if best is None or score > best["score"]:
            best = {"cluster": cluster, "score": score}
    return best


def _weighted_centroid(
    existing: Sequence[float],
    existing_count: int,
    incoming: Sequence[float],
    incoming_count: int,
) -> list[float]:
    if not existing or existing_count <= 0:
        return _normalize(incoming)
    total = existing_count + incoming_count
    return _normalize(
        [
            (existing_value * existing_count + incoming_value * incoming_count) / total
            for existing_value, incoming_value in zip(existing, incoming)
        ]
    )


def _persist_match_event(
    *,
    session_id: str | None,
    speaker_id: str,
    suggested_contact_id: str | None,
    corrected_contact_id: str | None,
    score: float | None,
    margin: float | None,
    status: str,
    metadata: dict[str, Any],
) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO speaker_match_events (
              match_event_id,
              session_id,
              speaker_id,
              suggested_contact_id,
              corrected_contact_id,
              score,
              margin,
              status,
              metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"speaker-match:{session_id or 'unknown'}:{speaker_id}:{uuid4().hex[:12]}",
                session_id,
                speaker_id,
                suggested_contact_id,
                corrected_contact_id,
                score,
                margin,
                status,
                metadata,
            ),
        )
        conn.commit()


def _first_email(value: Any) -> str | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            email = contacts_service.normalize_email(str(item or ""))
            if email:
                return email
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
