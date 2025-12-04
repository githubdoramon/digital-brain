from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ContactIn(BaseModel):
    contact_id: str
    display_name: str
    aliases: Optional[List[str]] = []
    birthday: Optional[date] = None
    emails: Optional[List[str]] = []
    phones: Optional[List[str]] = []
    links: Optional[List[str]] = []
    tags: Optional[List[str]] = []
    external_id: Optional[str] = None
    relationships: Optional[List[ContactRelationshipIn]] = []


class PlaceIn(BaseModel):
    place_id: str
    name: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    geohash: Optional[str] = None


class EventIn(BaseModel):
    id: str
    start_date: datetime = Field(alias="startDate")
    end_date: Optional[datetime] = Field(default=None, alias="endDate")
    place_id: Optional[str] = Field(default=None, alias="placeId")
    people: Optional[List[str]] = Field(default_factory=list, alias="people")
    tags: Optional[List[str]] = Field(default_factory=list)
    types: Optional[List[str]] = Field(default_factory=list)
    title: Optional[str] = ""
    summary: Optional[str] = ""
    raw: Optional[Dict[str, Any]] = Field(default_factory=dict)
    external_id: Optional[str] = Field(default=None, alias="externalId")


class ExternalEventPayload(BaseModel):
    event: EventIn
    external_type: Literal["google"] = Field(alias="externalType")

    class Config:
        allow_population_by_field_name = True


class MeetingIn(BaseModel):
    id: Optional[str] = None
    title: str
    content: Optional[str] = None
    date: datetime
    link: Optional[str] = None
    attendees: Optional[List[str]] = Field(default_factory=list)
    tags: Optional[List[str]] = Field(default_factory=list)


class ContactRelationshipIn(BaseModel):
    relationship_id: str
    from_contact_id: str
    to_contact_id: str
    relationship_type: str
    reciprocal_type: Optional[str] = None


class ExternalPerson(BaseModel):
    id: str
    name: Optional[str] = None
    birth_date: Optional[str] = Field(default=None, alias="birthDate")
    thumbnail_path: Optional[str] = Field(default=None, alias="thumbnailPath")
    face_asset_id: Optional[str] = Field(default=None, alias="faceAssetId")
    is_hidden: Optional[bool] = Field(default=None, alias="isHidden")
    is_favorite: Optional[bool] = Field(default=None, alias="isFavorite")
    color: Optional[str] = None
    created_at: Optional[datetime] = Field(default=None, alias="createdAt")
    updated_at: Optional[datetime] = Field(default=None, alias="updatedAt")
    owner_id: Optional[str] = Field(default=None, alias="ownerId")
    update_id: Optional[str] = Field(default=None, alias="updateId")

    class Config:
        allow_population_by_field_name = True
        extra = "allow"


class ExternalContactPayload(BaseModel):
    person: ExternalPerson
    previous: Optional[ExternalPerson] = None
    actor_id: Optional[str] = Field(default=None, alias="actorId")

    class Config:
        allow_population_by_field_name = True
        extra = "allow"


class ExternalWebhookInfo(BaseModel):
    id: str
    url: Optional[str] = None
    method: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    timeout: Optional[int] = None
    retries: Optional[int] = None
    backoff_ms: Optional[int] = Field(default=None, alias="backoffMs")

    class Config:
        allow_population_by_field_name = True
        extra = "allow"


class ExternalContactWebhook(BaseModel):
    webhook: Optional[ExternalWebhookInfo] = None
    event_name: str = Field(alias="eventName")
    timestamp: datetime
    payload: ExternalContactPayload
    id: str

    class Config:
        extra = "allow"
        allow_population_by_field_name = True


class ContactMergeIn(BaseModel):
    primary_contact_id: str
    duplicate_contact_id: str


class TodoIn(BaseModel):
    todo_id: str
    description: str
    status: Optional[str] = "pending"
    due_date: Optional[date] = None
    contact_ids: Optional[List[str]] = []
    event_ids: Optional[List[str]] = []
    place_ids: Optional[List[str]] = []


class DocumentOut(BaseModel):
    document_id: str
    title: str
    tags: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    document_date: Optional[datetime] = None
    file_name: str
    file_mime: Optional[str] = None
    file_size: Optional[int] = None
    download_url: str
    created_at: datetime
    updated_at: datetime
    snippet: Optional[str] = None


class DocumentCollection(BaseModel):
    documents: List[DocumentOut] = Field(default_factory=list)


class DocumentDetailOut(DocumentOut):
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)
    content_preview: Optional[str] = None


class DocumentUpdateIn(BaseModel):
    title: Optional[str] = None
    tags: Optional[List[str]] = Field(default=None)
    description: Optional[str] = None
    document_date: Optional[datetime] = None


class DocumentSearchIn(BaseModel):
    query: str
    tags: Optional[List[str]] = Field(default_factory=list)
    limit: Optional[int] = 20


class AskIn(BaseModel):
    question: str
    limit: Optional[int] = 3
    session_id: Optional[str] = None  # kept for backward compatibility
    thread_id: Optional[str] = None


class AskOut(BaseModel):
    question: str
    answer: str
    resolution: Dict[str, Any]
    search_results: List[Dict[str, Any]]
    detailed_events: List[Dict[str, Any]]
    document_results: List[Dict[str, Any]] = Field(default_factory=list)
    session_id: Optional[str] = None
    thread_id: Optional[str] = None
    thread_title: Optional[str] = None
    memories_used: List[str] = Field(default_factory=list)
    web_results: List[Dict[str, Any]] = Field(default_factory=list)
    web_summary: Optional[str] = None
    web_follow_up_questions: Optional[List[str]] = Field(default_factory=list)
    web_query: Optional[str] = None
    web_provider: Optional[str] = None
    web_response_id: Optional[str] = None
    web_documents: List[Dict[str, Any]] = Field(default_factory=list)


class ThreadCreate(BaseModel):
    title: Optional[str] = None


class ThreadUpdate(BaseModel):
    title: Optional[str] = None


class ThreadMessageOut(BaseModel):
    message_id: int
    role: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ThreadOut(BaseModel):
    id: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_message_preview: Optional[str] = None


class ThreadDetailOut(ThreadOut):
    messages: List[ThreadMessageOut] = Field(default_factory=list)


class ServiceVersion(BaseModel):
    id: str
    name: str
    version: str = "unknown"
    git_sha: Optional[str] = None
    build_time: Optional[datetime] = None
    image: Optional[str] = None
    sources: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ServiceVersionCollection(BaseModel):
    generated_at: datetime
    services: List[ServiceVersion]
    manifest_path: Optional[str] = None
    manifest_metadata: Dict[str, Any] = Field(default_factory=dict)
    env_entry_count: int = 0
    fallback_count: int = 0
