from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

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
    ts: datetime
    place_id: Optional[str] = None
    people: Optional[List[str]] = []
    tags: Optional[List[str]] = []
    types: Optional[List[str]] = []
    what_text: Optional[str] = ""
    raw: Optional[Dict[str, Any]] = {}


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


class TodoIn(BaseModel):
    todo_id: str
    description: str
    status: Optional[str] = "pending"
    due_date: Optional[date] = None
    contact_ids: Optional[List[str]] = []
    event_ids: Optional[List[str]] = []
    place_ids: Optional[List[str]] = []


class ResolveIn(BaseModel):
    text: str
    need_contacts: Optional[bool] = True
    need_places: Optional[bool] = True


class SearchIn(BaseModel):
    query: str
    people: Optional[List[str]] = []
    place_ids: Optional[List[str]] = []
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    limit: Optional[int] = 5


class GetIn(BaseModel):
    ids: List[str]


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
