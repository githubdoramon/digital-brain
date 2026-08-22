from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

import contacts as contacts_service
import event_photos as event_photos_service
import events as events_service
import meeting_transcript_jobs
import voice_profiles as voice_profiles_service
from auth import get_current_user, require_service_api_key
from commands.event import confirm_event_command as confirm_event_command_impl
from db import get_conn
from observability.logger import get_runtime_logger
from schemas import (
    EventCommandConfirmation,
    EventCommandResult,
    EventIn,
    ExternalEventPayload,
    MeetingParticipantsResolveIn,
    MeetingTranscriptPayload,
    SpeakerVoiceConfirmIn,
    SpeakerVoiceMatchIn,
)

logger = get_runtime_logger(__name__)


def _clean_id_list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [str(value).strip() for value in values if str(value).strip()]


def _parse_optional_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    return datetime.fromisoformat(normalized)


def _speaker_voice_confirm_summary(payload: SpeakerVoiceConfirmIn) -> list[dict[str, Any]]:
    return [
        {
            "speaker_id": observation.speaker_id,
            "contact_id": observation.contact_id,
            "email": observation.email,
            "has_name": bool(observation.name),
            "embedding_count": len(observation.embeddings),
            "window_count": len(observation.windows),
            "embedding_model": observation.embedding_model,
            "embedding_dim": observation.embedding_dim,
            "source": observation.source,
        }
        for observation in payload.observations
    ]


def create_events_router(
) -> APIRouter:
    router = APIRouter()

    @router.post("/ingest/event")
    @router.post("/mobile/ingest/event")
    def ingest_event(e: EventIn, user: dict = Depends(get_current_user)):
        events_service.ingest_event(e)
        return {"ok": True, "id": e.id}

    @router.post("/ingest/meetings/transcript")
    def ingest_meeting_transcript(
        payload: MeetingTranscriptPayload,
        user: dict = Depends(get_current_user),
    ):
        try:
            job = meeting_transcript_jobs.enqueue_transcript(
                payload,
                current_user=user,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            logger.exception(
                "[meeting_transcript] Queueing failed upload_id=%s session_id=%s transcript_hash=%s",
                payload.upload_id,
                payload.session_id,
                payload.transcript_hash,
            )
            raise
        return {"ok": True, "queued": True, **job}

    @router.post("/meetings/{meeting_id}/summary/rerun")
    def rerun_meeting_summary(
        meeting_id: str,
        user: dict = Depends(get_current_user),
    ):
        event = events_service.get_event_by_id(meeting_id)
        if not event:
            raise HTTPException(status_code=404, detail="Meeting not found")

        raw = event.get("raw") if isinstance(event, dict) else None
        if not isinstance(raw, dict) or raw.get("source") != "meeting_transcript_ingest":
            raise HTTPException(status_code=400, detail="Event has no stored meeting transcript")

        stored_payload = {
            "upload_id": raw.get("upload_id"),
            "session_id": raw.get("session_id"),
            "transcript_hash": raw.get("transcript_hash"),
            "meeting": raw.get("meeting"),
            "participants": raw.get("participants") or [],
            "speaker_identities": raw.get("speaker_identities") or [],
            "transcript": raw.get("transcript"),
        }
        try:
            payload = MeetingTranscriptPayload.model_validate(stored_payload)
            job = meeting_transcript_jobs.enqueue_transcript(
                payload,
                current_user=user,
                force_regenerate=True,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail="Stored meeting transcript is not rerunnable: " + str(exc),
            ) from exc

        return {
            "ok": True,
            "queued": True,
            "rerun": True,
            "event_id": meeting_id,
            **job,
        }

    @router.post("/ingest/event/external")
    def ingest_external_event(
        payload: ExternalEventPayload,
        _: None = Depends(require_service_api_key),
    ):
        try:
            event_id = events_service.ingest_external_event(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "id": event_id}

    @router.post("/participants/resolve")
    def resolve_participants(
        payload: MeetingParticipantsResolveIn,
        user: dict = Depends(get_current_user),
    ):
        return {
            "participants": contacts_service.resolve_meeting_participants(
                payload.participants,
                current_user_email=payload.current_user_email,
                authenticated_user_email=user.get("email"),
            )
        }

    @router.post("/meetings/speakers/match")
    def match_meeting_speakers(
        payload: SpeakerVoiceMatchIn,
        user: dict = Depends(get_current_user),
    ):
        return voice_profiles_service.match_speakers(payload, current_user=user)

    @router.post("/meetings/speakers/confirm")
    def confirm_meeting_speakers(
        payload: SpeakerVoiceConfirmIn,
        user: dict = Depends(get_current_user),
    ):
        user_email = user.get("email")
        speaker_ids = [observation.speaker_id for observation in payload.observations]
        logger.info(
            "[voice_profiles] confirm request session_id=%s observations=%d rejected=%d user=%s speakers=%s",
            payload.session_id,
            len(payload.observations),
            len(payload.rejected_matches),
            user_email,
            speaker_ids,
        )
        try:
            result = voice_profiles_service.confirm_speaker_profiles(payload)
        except Exception:
            logger.exception(
                "[voice_profiles] confirm failed session_id=%s observations=%d rejected=%d user=%s summary=%s",
                payload.session_id,
                len(payload.observations),
                len(payload.rejected_matches),
                user_email,
                _speaker_voice_confirm_summary(payload),
            )
            raise
        logger.info(
            "[voice_profiles] confirm complete session_id=%s confirmed=%d rejected=%d user=%s",
            payload.session_id,
            result.get("confirmed_observation_count", 0),
            result.get("rejected_match_count", 0),
            user_email,
        )
        return {"ok": True, **result}

    @router.get("/events/search")
    @router.get("/mobile/events/search")
    def search_events(
        user: dict = Depends(get_current_user),
        query: str | None = Query(default=None),
        limit: int = Query(default=10, ge=1, le=50),
        offset: int = Query(default=0, ge=0),
        include_future: bool = Query(default=False),
        meeting_only: bool = Query(default=False),
        contact_ids: list[str] | None = Query(default=None),
        place_ids: list[str] | None = Query(default=None),
        event_ids: list[str] | None = Query(default=None),
    ):
        trimmed = (query or "").strip()
        clean_contact_ids = _clean_id_list(contact_ids)
        clean_place_ids = _clean_id_list(place_ids)
        clean_event_ids = _clean_id_list(event_ids)
        where_clauses: list[str] = []
        params: list[Any] = []

        if trimmed:
            like = f"%{trimmed}%"
            where_clauses.append(
                """
                (
                    unaccent(COALESCE(e.title, '')) ILIKE unaccent(%s)
                    OR unaccent(COALESCE(e.summary, '')) ILIKE unaccent(%s)
                )
                """
            )
            params.extend([like, like])

        if not include_future:
            where_clauses.append("(e.start_date IS NULL OR e.start_date <= NOW())")

        if meeting_only:
            where_clauses.append(
                "EXISTS (SELECT 1 FROM unnest(COALESCE(e.types, ARRAY[]::text[])) AS event_type "
                "WHERE lower(event_type) = 'meeting')"
            )

        if clean_contact_ids:
            where_clauses.append(
                """
                (
                    SELECT COUNT(DISTINCT ec.contact_id)
                    FROM event_contacts ec
                    WHERE ec.event_id = e.id
                      AND ec.contact_id = ANY(%s)
                ) = %s
                """
            )
            params.extend([clean_contact_ids, len(clean_contact_ids)])

        if clean_place_ids:
            where_clauses.append("e.place_id = ANY(%s)")
            params.append(clean_place_ids)

        if clean_event_ids:
            where_clauses.append("e.id = ANY(%s)")
            params.append(clean_event_ids)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT e.id,
                       e.title,
                       e.summary,
                       e.start_date,
                       e.end_date
                FROM events e
                {where_sql}
                ORDER BY e.start_date DESC NULLS LAST, e.id DESC
                LIMIT %s OFFSET %s
                """,
                (*params, limit + 1, offset),
            )
            fetched_rows: list[dict[str, Any]] = [dict(row) for row in cur.fetchall()]
        has_more = len(fetched_rows) > limit
        rows = fetched_rows[:limit]
        next_offset = offset + len(rows)
        return {
            "events": rows,
            "has_more": has_more,
            "next_offset": next_offset,
        }

    @router.get("/events/{event_id}")
    @router.get("/mobile/events/{event_id}")
    def get_event_detail(event_id: str, user: dict = Depends(get_current_user)):
        event = events_service.get_event_by_id(event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

        # Keep the enriched people/place/photo representation while including
        # the raw meeting transcript needed by the meeting detail screen.
        enriched_events = events_service.get_events([event_id])
        if enriched_events:
            enriched = enriched_events[0]
            event["people"] = enriched.get("people", event.get("people") or [])
            event["place"] = enriched.get("place")
            event["photos"] = enriched.get("photos", event.get("photos") or [])
        event["action_items"] = events_service.get_event_action_items(event_id)
        return event

    @router.post("/mobile/events/{event_id}/photos")
    async def upload_event_photo(
        event_id: str,
        file: UploadFile = File(...),
        captured_at: str | None = Form(default=None),
        local_asset_id: str | None = Form(default=None),
        source: str | None = Form(default=None),
        debug_client: str | None = Form(default=None),
        user: dict = Depends(get_current_user),
    ):
        try:
            parsed_captured_at = _parse_optional_datetime(captured_at)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="captured_at must be ISO 8601") from exc

        parsed_debug_client: dict[str, Any] | None = None
        if debug_client:
            try:
                candidate = json.loads(debug_client)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="debug_client must be valid JSON") from exc
            if isinstance(candidate, dict):
                parsed_debug_client = candidate

        try:
            image_bytes = await file.read()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed to read image: {exc}") from exc

        logger.info(
            "[event_photos] Incoming upload request: event_id=%s filename=%s content_type=%s size_bytes=%s local_asset_id=%s source=%s client_debug=%s",
            event_id,
            file.filename,
            file.content_type,
            len(image_bytes),
            local_asset_id,
            source,
            parsed_debug_client,
        )

        try:
            photo = event_photos_service.attach_event_photo(
                event_id,
                image_bytes=image_bytes,
                filename=file.filename or "event-photo.jpg",
                mime_type=file.content_type,
                captured_at=parsed_captured_at,
                local_asset_id=local_asset_id,
                source=source,
                client_debug=parsed_debug_client,
            )
        except event_photos_service.EventPhotoError as exc:
            detail = str(exc)
            status_code = 404 if detail == "Event not found" else 400
            raise HTTPException(status_code=status_code, detail=detail) from exc
        except Exception as exc:
            logger.exception("[event_photos] Failed to attach photo: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to attach event photo") from exc

        return {"ok": True, "photo": photo}

    @router.get("/mobile/events/{event_id}/photos/{asset_id}/thumbnail")
    def get_event_photo_thumbnail(
        event_id: str,
        asset_id: str,
        user: dict = Depends(get_current_user),
    ):
        try:
            content, content_type = event_photos_service.fetch_event_photo_thumbnail(event_id, asset_id)
        except event_photos_service.EventPhotoError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("[event_photos] Failed to load thumbnail: %s", exc)
            raise HTTPException(status_code=502, detail="Failed to load event photo thumbnail") from exc
        return Response(content=content, media_type=content_type)

    @router.delete("/mobile/events/{event_id}/photos/{asset_id}")
    def unlink_event_photo(
        event_id: str,
        asset_id: str,
        user: dict = Depends(get_current_user),
    ):
        try:
            deleted = event_photos_service.unlink_event_photo(event_id, asset_id)
        except event_photos_service.EventPhotoError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Event photo not found")
        return {"ok": True}

    @router.delete("/events/{event_id}")
    @router.delete("/mobile/events/{event_id}")
    def delete_event(event_id: str, user: dict = Depends(get_current_user)):
        deleted = events_service.delete_event(event_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Event not found")
        return {"ok": True}

    @router.get("/meetings/{meeting_id}")
    def get_meeting(meeting_id: str, user: dict = Depends(get_current_user)):
        meeting = events_service.get_meeting(meeting_id)
        if not meeting:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return meeting

    @router.post("/commands/event/confirm", response_model=EventCommandResult)
    @router.post("/mobile/commands/event/confirm", response_model=EventCommandResult)
    def confirm_event_command(
        payload: EventCommandConfirmation,
        user: dict = Depends(get_current_user),
    ):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")
        return confirm_event_command_impl(payload, user_email)

    return router
