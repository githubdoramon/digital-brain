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
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    address: str | None = None
    city: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None
    geohash: str | None = None


class ContactPlaceLinkIn(BaseModel):
    place_id: str
    role: str | None = None
    source: str | None = None
    confidence: str | None = None


class EventIn(BaseModel):
    id: str
    start_date: datetime = Field(alias="startDate")
    end_date: datetime | None = Field(default=None, alias="endDate")
    place_id: str | None = Field(default=None, alias="placeId")
    people: list[str] | None = Field(default_factory=list, alias="people")
    attendees_emails: list[str] | None = Field(default=None, alias="attendeesEmails")
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
    attendees_emails: list[str] | None = Field(default=None, alias="attendeesEmails")
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


class ContactGroupIn(BaseModel):
    name: str
    member_contact_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None


class ContactGroupOut(BaseModel):
    group_id: str
    owner_contact_id: str
    name: str
    description: str | None = None
    status: str
    source: str
    confirmed: bool
    aliases: list[str] = Field(default_factory=list)
    members: list[dict[str, Any]] = Field(default_factory=list)
    member_count: int = 0


class TodoIn(BaseModel):
    todo_id: str
    description: str
    status: str | None = "pending"
    due_date: date | None = None
    contact_ids: list[str] | None = []
    event_ids: list[str] | None = []
    place_ids: list[str] | None = []


class TodoStatusUpdateIn(BaseModel):
    status: str


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
    linked_contacts: list[dict[str, Any]] = Field(default_factory=list)


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
    contact_ids: list[str] | None = Field(default=None)


class DocumentSearchIn(BaseModel):
    query: str
    tags: list[str] | None = Field(default_factory=list)
    contact_ids: list[str] | None = Field(default=None)
    limit: int | None = 20


class ToolRunIn(BaseModel):
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    llm_model: str | None = None
    timeout_seconds: int | None = None


class ToolRunOut(BaseModel):
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    normalized_args: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float
    llm_model: str | None = None
    timeout_seconds: int | None = None


class EvalRunIn(BaseModel):
    flow_id: str
    llm_model: str | None = None
    repetitions: int = Field(default=5, ge=1, le=20)
    discard_first_attempt: bool = True
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    reasoning_effort: str | None = None
    strict_json_schema: bool = True


class PushNotificationsUpdateIn(BaseModel):
    enabled: bool


class NotificationSettingsOut(BaseModel):
    push_notifications_enabled: bool = Field(alias="pushNotificationsEnabled")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

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


class NotificationTypeSettingsOut(BaseModel):
    notification_type: str = Field(alias="notificationType")
    title: str
    enabled: bool
    channels: list[Literal["push", "email"]] = Field(default_factory=list)
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    class Config:
        populate_by_name = True


class NotificationSettingsListOut(BaseModel):
    push_available: bool = Field(alias="pushAvailable")
    types: list[NotificationTypeSettingsOut] = Field(default_factory=list)

    class Config:
        populate_by_name = True


class NotificationTypeChannelsUpdateIn(BaseModel):
    channels: list[Literal["push", "email"]] = Field(default_factory=list)


class DailyBriefingIn(BaseModel):
    date: str
    timezone: str
    user_email: str | None = None


class DailyBriefingEventSummaryDebugIn(BaseModel):
    event_id: str = Field(alias="eventId")
    timezone: str
    user_email: str | None = Field(default=None, alias="userEmail")

    class Config:
        populate_by_name = True


class DailyBriefingNewsItemOut(BaseModel):
    briefing_item_id: str
    briefing_id: str | None = None
    cluster_id: str | None = None
    title: str
    url: str | None = None
    source: str
    source_domain: str | None = None
    section: Literal["topic", "general"]
    topic_label: str | None = None
    rank: int
    score: float | None = None
    brief_summary: str | None = None
    topic_matches: list[str] = Field(default_factory=list)


class DailyBriefingOut(BaseModel):
    status: Literal["ready", "pending", "failed"] = "ready"
    job_id: str | None = None
    message: str | None = None
    briefing_id: str | None = None
    date: str
    timezone: str
    event_count: int = 0
    todo_count: int = 0
    summary: str = ""
    markdown: str = ""
    news_items: list[DailyBriefingNewsItemOut] = Field(default_factory=list)


class ClientLocationIn(BaseModel):
    lat: float
    lon: float
    accuracy_m: float | None = None
    captured_at: datetime | None = None
    source: (
        Literal[
            "gps",
            "network",
            "browser",
            "manual",
            "unknown",
            "mobile_geolocation",
            "expo_location",
        ]
        | None
    ) = None


class UserLocationUpdateIn(ClientLocationIn):
    timezone: str | None = None
    place_name: str | None = None
    city: str | None = None
    country: str | None = None


class UserLocationOut(BaseModel):
    user_email: str
    lat: float
    lon: float
    accuracy_m: float | None = None
    captured_at: datetime
    source: str | None = None
    timezone: str | None = None
    place_name: str | None = None
    city: str | None = None
    country: str | None = None
    updated_at: datetime


class ClientContextIn(BaseModel):
    timezone: str | None = None
    locale: str | None = None
    location: ClientLocationIn | None = None


class UiSubmissionIn(BaseModel):
    block_id: str | None = None
    action_id: str | None = None
    values: dict[str, Any] = Field(default_factory=dict)
    text_fallback: str | None = None


class UiDirectiveBlock(BaseModel):
    id: str
    type: Literal["clarification_form", "choice_buttons", "info_card"]
    title: str | None = None
    description: str | None = None
    submit_label: str | None = None
    action_id: str | None = None
    fields: list[dict[str, Any]] = Field(default_factory=list)
    options: list[dict[str, Any]] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    body: str | None = None


class UiDirectivesOut(BaseModel):
    version: str = "1.0"
    fallback_text: str
    blocks: list[UiDirectiveBlock] = Field(default_factory=list)


class LinkedItemOut(BaseModel):
    entity_type: Literal["event", "document", "contact", "place"]
    entity_id: str
    title: str
    subtitle: str | None = None
    role: str | None = None


class ChatMediaAttachmentIn(BaseModel):
    attachment_id: str = Field(alias="attachment_id")
    file_name: str = Field(alias="file_name")
    mime_type: str | None = Field(default=None, alias="mime_type")
    content_base64: str = Field(alias="content_base64")
    source: str | None = None
    captured_at: datetime | None = None
    local_asset_id: str | None = Field(default=None, alias="local_asset_id")
    width: int | None = None
    height: int | None = None

    class Config:
        populate_by_name = True


class AskIn(BaseModel):
    question: str
    limit: int | None = 30
    session_id: str | None = None  # kept for backward compatibility
    thread_id: str | None = None
    pending_event_id: str | None = None
    client_context: ClientContextIn | None = None
    ui_submission: UiSubmissionIn | None = None
    media_attachments: list[ChatMediaAttachmentIn] = Field(default_factory=list)


class AskOut(BaseModel):
    question: str
    answer: str
    resolution: dict[str, Any]
    search_results: list[dict[str, Any]]
    events_results: list[dict[str, Any]]
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
    ui_directives: UiDirectivesOut | None = None
    command_result: dict[str, Any] | None = None
    pending_event_id: str | None = None
    linked_items: list[LinkedItemOut] = Field(default_factory=list)


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


class MainSessionOut(BaseModel):
    thread_id: str
    thread_title: str | None = None
    is_new_session: bool = False
    pending_event_id: str | None = None
    messages: list[ThreadMessageOut] = Field(default_factory=list)


class AskRunStatusOut(BaseModel):
    run_id: str
    thread_id: str | None = None
    status: str
    updated_at: datetime
    status_message: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


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
    group_confirmations: dict[str, bool] | None = Field(default_factory=dict)


class EventCommandResult(BaseModel):
    """Result of creating or updating an event via /event command."""

    success: bool
    event_id: str | None = None
    operation: str = "create"
    created_contacts: list[dict[str, Any]] = Field(default_factory=list)
    created_places: list[dict[str, Any]] = Field(default_factory=list)
    created_groups: list[dict[str, Any]] = Field(default_factory=list)
    attached_photos: list[dict[str, Any]] = Field(default_factory=list)
    photo_errors: list[str] = Field(default_factory=list)
    error: str | None = None


class ContactCommandConfirmation(BaseModel):
    """Schema for confirming and applying a /contact proposal."""

    preview_id: str
    confirmed: bool
    modifications: dict[str, Any] | None = Field(default_factory=dict)


class ContactCommandResult(BaseModel):
    """Result of applying a /contact command proposal."""

    success: bool
    updated_contact_ids: list[str] = Field(default_factory=list)
    created_contact_ids: list[str] = Field(default_factory=list)
    created_place_ids: list[str] = Field(default_factory=list)
    applied_relationship_ids: list[str] = Field(default_factory=list)
    applied_contact_place_links: list[dict[str, str]] = Field(default_factory=list)
    error: str | None = None


class NewsTopicIn(BaseModel):
    """Create or update a news topic."""

    topic_id: str
    label: str
    keywords: list[str]
    enabled: bool = True


class UserFactOut(BaseModel):
    """User fact as returned from the API."""

    fact_id: str
    user_email: str
    content: str
    category: str
    importance: int
    fact_mode: str = "soft"
    rule_type: str | None = None
    rule_scope: list[str] = Field(default_factory=list)
    rule_payload: dict[str, Any] = Field(default_factory=dict)
    source_thread_id: str | None = None
    access_count: int = 0
    last_accessed_at: datetime | str | None = None
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class UserFactUpdateIn(BaseModel):
    """Partial update for a user fact."""

    content: str | None = None
    category: str | None = None
    importance: int | None = Field(default=None, ge=1, le=10)
    fact_mode: str | None = None
    rule_type: str | None = None
    rule_scope: list[str] | None = None
    rule_payload: dict[str, Any] | None = None


class NewsTopicOut(BaseModel):
    """News topic as returned from the API."""

    topic_id: str
    label: str
    keywords: list[str]
    enabled: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NewsInteractionEventIn(BaseModel):
    event_type: Literal["article_opened", "article_feedback_up", "article_feedback_down"]
    briefing_id: str | None = None
    briefing_item_id: str | None = None
    cluster_id: str | None = None
    topic_label: str | None = None
    source: str | None = None
    source_domain: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NewsInteractionsIn(BaseModel):
    events: list[NewsInteractionEventIn] = Field(default_factory=list)
