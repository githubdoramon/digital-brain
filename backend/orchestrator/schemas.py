from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel


class ContactIn(BaseModel):
    contact_id: str
    display_name: str
    aliases: Optional[List[str]] = []
    birthday: Optional[date] = None
    emails: Optional[List[str]] = []
    phones: Optional[List[str]] = []
    links: Optional[List[str]] = []
    tags: Optional[List[str]] = []
    relationship: Optional[Literal["Myself", "Wife", "Daughter", "Brother", "Mother", "Coworker", "Friend"]] = None


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
    session_id: Optional[str] = None
    user_id: Optional[str] = "default_user"


class AskOut(BaseModel):
    question: str
    answer: str
    resolution: Dict[str, Any]
    search_results: List[Dict[str, Any]]
    detailed_events: List[Dict[str, Any]]
    session_id: Optional[str] = None
    memories_used: Optional[List[str]] = []
