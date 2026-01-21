from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ContactIn(BaseModel):
    contact_id: str
    display_name: str
    aliases: list[str] | None = []
    birthday: date | None = None
    emails: list[str] | None = []
    phones: list[str] | None = []
    links: list[str] | None = []
    tags: list[str] | None = []
    comments: str | None = None
    external_id: str | None = None
    relationships: list[ContactRelationshipIn] | None = []


class PlaceIn(BaseModel):
    place_id: str
    name: str | None = None
    city: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None
    geohash: str | None = None


class EventIn(BaseModel):
    id: str
    start_date: datetime = Field(alias="startDate")
    end_date: datetime | None = Field(default=None, alias="endDate")
    place_id: str | None = Field(default=None, alias="placeId")
    people: list[str] | None = Field(default_factory=list, alias="people")
    tags: list[str] | None = Field(default_factory=list)
    types: list[str] | None = Field(default_factory=list)
    title: str | None = ""
    summary: str | None = ""
    raw: dict[str, Any] | None = Field(default_factory=dict)
    external_id: str | None = Field(default=None, alias="externalId")


class ExternalEventPayload(BaseModel):
    event: EventIn
    external_type: Literal["google"] = Field(alias="externalType")

    class Config:
        populate_by_name = True


class MeetingIn(BaseModel):
    id: str | None = None
    title: str
    content: str | None = None
    date: datetime
    link: str | None = None
    attendees: list[str] | None = Field(default_factory=list)
    tags: list[str] | None = Field(default_factory=list)


class ContactRelationshipIn(BaseModel):
    relationship_id: str
    from_contact_id: str
    to_contact_id: str
    relationship_type: str
    reciprocal_type: str | None = None


class ExternalPerson(BaseModel):
    id: str
    name: str | None = None
    birth_date: str | None = Field(default=None, alias="birthDate")
    thumbnail_path: str | None = Field(default=None, alias="thumbnailPath")
    face_asset_id: str | None = Field(default=None, alias="faceAssetId")
    is_hidden: bool | None = Field(default=None, alias="isHidden")
    is_favorite: bool | None = Field(default=None, alias="isFavorite")
    color: str | None = None
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")
    owner_id: str | None = Field(default=None, alias="ownerId")
    update_id: str | None = Field(default=None, alias="updateId")

    class Config:
        populate_by_name = True
        extra = "allow"


class ExternalContactPayload(BaseModel):
    person: ExternalPerson
    previous: ExternalPerson | None = None
    actor_id: str | None = Field(default=None, alias="actorId")

    class Config:
        populate_by_name = True
        extra = "allow"


class ExternalWebhookInfo(BaseModel):
    id: str
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: int | None = None
    retries: int | None = None
    backoff_ms: int | None = Field(default=None, alias="backoffMs")

    class Config:
        populate_by_name = True
        extra = "allow"


class ExternalContactWebhook(BaseModel):
    webhook: ExternalWebhookInfo | None = None
    event_name: str = Field(alias="eventName")
    timestamp: datetime
    payload: ExternalContactPayload
    id: str

    class Config:
        extra = "allow"
        populate_by_name = True


class ContactMergeIn(BaseModel):
    primary_contact_id: str
    duplicate_contact_id: str


class TodoIn(BaseModel):
    todo_id: str
    description: str
    status: str | None = "pending"
    due_date: date | None = None
    contact_ids: list[str] | None = []
    event_ids: list[str] | None = []
    place_ids: list[str] | None = []


class DocumentOut(BaseModel):
    document_id: str
    title: str
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    document_date: datetime | None = None
    file_name: str
    file_mime: str | None = None
    file_size: int | None = None
    download_url: str
    created_at: datetime
    updated_at: datetime
    snippet: str | None = None


class DocumentCollection(BaseModel):
    documents: list[DocumentOut] = Field(default_factory=list)


class DocumentDetailOut(DocumentOut):
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    content_preview: str | None = None


class DocumentUpdateIn(BaseModel):
    title: str | None = None
    tags: list[str] | None = Field(default=None)
    description: str | None = None
    document_date: datetime | None = None


class DocumentSearchIn(BaseModel):
    query: str
    tags: list[str] | None = Field(default_factory=list)
    limit: int | None = 20


class PushNotificationsUpdateIn(BaseModel):
    enabled: bool


class UserSettingsOut(BaseModel):
    push_notifications_enabled: bool = Field(alias="pushNotificationsEnabled")
    created_at: datetime | None = Field(alias="createdAt")
    updated_at: datetime | None = Field(alias="updatedAt")

    class Config:
        populate_by_name = True


class DeviceRegisterIn(BaseModel):
    expo_push_token: str = Field(alias="expoPushToken")
    platform: str
    device_name: str | None = Field(default=None, alias="deviceName")
    app_version: str | None = Field(default=None, alias="appVersion")
    os_version: str | None = Field(default=None, alias="osVersion")

    class Config:
        populate_by_name = True


class PushNotificationTestIn(BaseModel):
    title: str
    message: str


class AskIn(BaseModel):
    question: str
    limit: int | None = 3
    session_id: str | None = None  # kept for backward compatibility
    thread_id: str | None = None


class AskOut(BaseModel):
    question: str
    answer: str
    resolution: dict[str, Any]
    search_results: list[dict[str, Any]]
    detailed_events: list[dict[str, Any]]
    document_results: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str | None = None
    thread_id: str | None = None
    thread_title: str | None = None
    is_new_session: bool = False
    # Removed: event_proposal (old event capture system)
    web_results: list[dict[str, Any]] = Field(default_factory=list)
    web_summary: str | None = None
    web_follow_up_questions: list[str] | None = Field(default_factory=list)
    web_query: str | None = None
    web_provider: str | None = None
    web_response_id: str | None = None
    web_documents: list[dict[str, Any]] = Field(default_factory=list)
    command_result: dict[str, Any] | None = None


class ThreadCreate(BaseModel):
    title: str | None = None


class ThreadUpdate(BaseModel):
    title: str | None = None


class ThreadMessageOut(BaseModel):
    message_id: int
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ThreadOut(BaseModel):
    id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime
    last_message_preview: str | None = None


class ThreadDetailOut(ThreadOut):
    messages: list[ThreadMessageOut] = Field(default_factory=list)


class ServiceVersion(BaseModel):
    id: str
    name: str
    version: str = "unknown"
    git_sha: str | None = None
    build_time: datetime | None = None
    image: str | None = None
    sources: list[str] = Field(default_factory=list)
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceVersionCollection(BaseModel):
    generated_at: datetime
    services: list[ServiceVersion]
    manifest_path: str | None = None
    manifest_metadata: dict[str, Any] = Field(default_factory=dict)
    env_entry_count: int = 0
    fallback_count: int = 0


class EventCommandConfirmation(BaseModel):
    """Schema for confirming and creating an event from /event command."""

    preview_id: str
    confirmed: bool
    modifications: dict[str, Any] | None = Field(default_factory=dict)
    skip_entities: dict[str, list[str]] | None = Field(default_factory=dict)


class EventCommandResult(BaseModel):
    """Result of creating an event via /event command."""

    success: bool
    event_id: str | None = None
    created_contacts: list[dict[str, Any]] = Field(default_factory=list)
    created_places: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
