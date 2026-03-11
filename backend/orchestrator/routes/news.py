from __future__ import annotations

import news_feeds
import news_personalization
from auth import get_current_user
from fastapi import APIRouter, Depends, HTTPException
from observability.logger import get_runtime_logger
from schemas import NewsInteractionsIn, NewsTopicIn

logger = get_runtime_logger(__name__)


def create_news_router() -> APIRouter:
    router = APIRouter()

    @router.get("/news-topics")
    @router.get("/mobile/news-topics")
    def list_news_topics(user: dict = Depends(get_current_user)):
        topics = news_feeds.list_topics()
        return {"topics": topics}

    @router.post("/news-topics")
    @router.post("/mobile/news-topics")
    def upsert_news_topic(
        payload: NewsTopicIn,
        user: dict = Depends(get_current_user),
    ):
        topic = news_feeds.upsert_topic(
            topic_id=payload.topic_id,
            label=payload.label,
            keywords=payload.keywords,
            enabled=payload.enabled,
        )
        return topic

    @router.delete("/news-topics/{topic_id}")
    @router.delete("/mobile/news-topics/{topic_id}")
    def delete_news_topic(
        topic_id: str,
        user: dict = Depends(get_current_user),
    ):
        deleted = news_feeds.delete_topic(topic_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Topic not found")
        return {"ok": True}

    @router.get("/news-topics/preview")
    @router.get("/mobile/news-topics/preview")
    def preview_news(user: dict = Depends(get_current_user)):
        """Fetch news from all sources to preview topic matching."""
        try:
            articles = news_feeds.fetch_news()
        except Exception as exc:
            logger.warning("News preview fetch failed", exc_info=True)
            raise HTTPException(status_code=502, detail=f"News fetch failed: {exc}") from exc
        return {"articles": articles}

    @router.post("/news/interactions")
    @router.post("/mobile/news/interactions")
    def ingest_news_interactions(
        payload: NewsInteractionsIn,
        user: dict = Depends(get_current_user),
    ):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        events = [event.model_dump() for event in payload.events]
        written = news_personalization.record_user_interactions(user_email=user_email, events=events)
        return {
            "ok": True,
            "recorded": written,
        }

    return router
