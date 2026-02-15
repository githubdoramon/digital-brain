"""News feed aggregation module.

Collects news from multiple sources and returns a unified list of articles
filtered by user-defined tracked topics.  Designed to be called by the
daily-briefing pipeline (or any other consumer) to produce a pre-digested
news context that an LLM can later rank/summarize.

Sources:
    1. **Tavily** – keyword search with ``topic="news"`` + ``time_range="day"``
       (requires TAVILY_API_KEY).
    2. **Hacker News** – top stories via hnrss.org RSS (free, no key).
    3. **TechCrunch** – latest articles via RSS (free, no key).
    4. **Google News Portugal** – Portuguese-language headlines via RSS (free).
    5. **BBC World** – global news via RSS (free, broadly unbiased).
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

import feedparser
import requests

from db import get_conn
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


def _ms_since(start: float) -> float:
    return (perf_counter() - start) * 1000


# ---------------------------------------------------------------------------
# Constants / feed registry
# ---------------------------------------------------------------------------

DEFAULT_TAVILY_NEWS_RESULTS = 5

RSS_FEEDS: dict[str, dict[str, str]] = {
    "hacker_news": {
        "url": "https://hnrss.org/frontpage?count=15&points=30",
        "label": "Hacker News",
    },
    "techcrunch": {
        "url": "https://techcrunch.com/feed/",
        "label": "TechCrunch",
    },
    "google_news_pt": {
        "url": "https://news.google.com/rss?hl=pt-PT&gl=PT&ceid=PT:pt-150",
        "label": "Google News Portugal",
    },
    "bbc_world": {
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "label": "BBC World",
    },
}

RSS_FETCH_TIMEOUT = 15  # seconds

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

NewsArticle = dict[str, Any]


def fetch_news(
    *,
    max_tavily_per_topic: int = DEFAULT_TAVILY_NEWS_RESULTS,
    feed_limit_per_source: int = 10,
) -> list[NewsArticle]:
    """Main entry-point: fetch news from all sources, filtered by DB topics.

    Returns a deduplicated, sorted list of :class:`NewsArticle` dicts::

        {
            "title": str,
            "url": str,
            "summary": str,
            "source": str,          # e.g. "tavily", "hacker_news", "bbc_world"
            "published_at": str | None,   # ISO-8601 or None
            "topic_matches": list[str],   # topic labels that matched (may be empty for RSS)
        }
    """
    t0 = perf_counter()
    topics = list_topics(enabled_only=True)
    all_keywords: list[str] = []
    for t in topics:
        all_keywords.extend(t["keywords"])

    logger.info(
        "[news] Starting news aggregation: %d topic(s), %d keyword(s), %d RSS feed(s)",
        len(topics),
        len(all_keywords),
        len(RSS_FEEDS),
    )
    if topics:
        logger.info(
            "[news] Active topics: %s",
            ", ".join(f"{t['label']} ({len(t['keywords'])} kw)" for t in topics),
        )

    articles: list[NewsArticle] = []

    # -- 1. Tavily topic searches (one search per topic) ---------------------
    tavily_start = perf_counter()
    tavily_total = 0
    for topic in topics:
        for kw in topic["keywords"]:
            results = _search_tavily_news(kw, max_results=max_tavily_per_topic)
            for r in results:
                r["topic_matches"] = [topic["label"]]
            tavily_total += len(results)
            articles.extend(results)
    logger.info(
        "[news] Tavily: %d article(s) from %d keyword search(es) (%.0fms)",
        tavily_total,
        len(all_keywords),
        _ms_since(tavily_start),
    )

    # -- 2. RSS feeds --------------------------------------------------------
    rss_start = perf_counter()
    rss_total = 0
    for feed_id, feed_meta in RSS_FEEDS.items():
        rss_articles = _fetch_rss_feed(
            feed_meta["url"],
            source=feed_id,
            label=feed_meta["label"],
            limit=feed_limit_per_source,
        )
        # tag any RSS article whose title/summary matches a tracked keyword
        matched_count = 0
        for article in rss_articles:
            article["topic_matches"] = _match_topics(article, topics)
            if article["topic_matches"]:
                matched_count += 1
        rss_total += len(rss_articles)
        articles.extend(rss_articles)
        logger.info(
            "[news] RSS %s: %d article(s), %d topic-matched",
            feed_meta["label"],
            len(rss_articles),
            matched_count,
        )
    logger.info("[news] RSS total: %d article(s) (%.0fms)", rss_total, _ms_since(rss_start))

    # -- 3. Deduplicate & sort -----------------------------------------------
    before_dedup = len(articles)
    articles = _deduplicate(articles)
    articles.sort(key=_sort_key, reverse=True)

    topic_matched = [a for a in articles if a.get("topic_matches")]
    unmatched = [a for a in articles if not a.get("topic_matches")]
    logger.info(
        "[news] Final: %d article(s) (%d topic-matched, %d general, %d duplicates removed) (%.0fms total)",
        len(articles),
        len(topic_matched),
        len(unmatched),
        before_dedup - len(articles),
        _ms_since(t0),
    )

    return articles


# ---------------------------------------------------------------------------
# Topic CRUD (backed by news_topics table)
# ---------------------------------------------------------------------------


def list_topics(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    """Return all news topics, optionally filtered to enabled ones."""
    query = "SELECT * FROM news_topics"
    params: tuple = ()
    if enabled_only:
        query += " WHERE enabled = TRUE"
    query += " ORDER BY label"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return [dict(row) for row in cur.fetchall()]


def upsert_topic(
    *,
    topic_id: str,
    label: str,
    keywords: list[str],
    enabled: bool = True,
) -> dict[str, Any]:
    """Create or update a news topic."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO news_topics (topic_id, label, keywords, enabled, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (topic_id) DO UPDATE
              SET label = EXCLUDED.label,
                  keywords = EXCLUDED.keywords,
                  enabled = EXCLUDED.enabled,
                  updated_at = NOW()
            RETURNING *
            """,
            (topic_id, label, keywords, enabled),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else {}


def delete_topic(topic_id: str) -> bool:
    """Delete a topic. Returns True if a row was removed."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM news_topics WHERE topic_id = %s",
            (topic_id,),
        )
        conn.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Tavily news search
# ---------------------------------------------------------------------------


def _search_tavily_news(
    query: str,
    *,
    max_results: int = DEFAULT_TAVILY_NEWS_RESULTS,
) -> list[NewsArticle]:
    """Search Tavily with ``topic=news`` and ``time_range=day``."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        logger.debug("TAVILY_API_KEY not set, skipping Tavily news search")
        return []

    api_url = os.getenv("TAVILY_API_URL", "https://api.tavily.com/search")
    timeout = int(os.getenv("TAVILY_TIMEOUT", "30"))

    payload = {
        "api_key": api_key,
        "query": query,
        "topic": "news",
        "time_range": "day",
        "max_results": max(1, min(max_results, 10)),
        "search_depth": "basic",
        "include_answer": False,
        "include_raw_content": False,
    }

    try:
        t0 = perf_counter()
        resp = requests.post(api_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        logger.debug(
            "[news.tavily] Search '%s': %d result(s) (%.0fms)",
            query,
            len(data.get("results") or []),
            _ms_since(t0),
        )
    except Exception:
        logger.warning("Tavily news search failed for '%s'", query, exc_info=True)
        return []

    articles: list[NewsArticle] = []
    for item in data.get("results") or []:
        articles.append(
            {
                "title": (item.get("title") or "").strip(),
                "url": (item.get("url") or "").strip(),
                "summary": (item.get("content") or "").strip(),
                "source": "tavily",
                "published_at": item.get("published_date"),
                "topic_matches": [],
            }
        )
    return articles


# ---------------------------------------------------------------------------
# RSS feed fetcher
# ---------------------------------------------------------------------------


def _fetch_rss_feed(
    feed_url: str,
    *,
    source: str,
    label: str,
    limit: int = 10,
) -> list[NewsArticle]:
    """Parse an RSS feed and return up to *limit* articles."""
    try:
        t0 = perf_counter()
        feed = feedparser.parse(
            feed_url,
            request_headers={"User-Agent": "digital-brain/1.0"},
        )
        logger.debug(
            "[news.rss] Fetched '%s': %d entries (%.0fms)",
            label,
            len(feed.entries),
            _ms_since(t0),
        )
    except Exception:
        logger.warning("Failed to fetch RSS feed '%s'", label, exc_info=True)
        return []

    if feed.bozo and not feed.entries:
        logger.warning("RSS feed '%s' returned no entries (bozo=%s)", label, feed.bozo_exception)
        return []

    articles: list[NewsArticle] = []
    for entry in feed.entries[:limit]:
        published = _parse_rss_date(entry)
        articles.append(
            {
                "title": (entry.get("title") or "").strip(),
                "url": (entry.get("link") or "").strip(),
                "summary": _clean_html(entry.get("summary") or entry.get("description") or ""),
                "source": source,
                "published_at": published,
                "topic_matches": [],
            }
        )
    return articles


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _match_topics(
    article: NewsArticle,
    topics: list[dict[str, Any]],
) -> list[str]:
    """Return topic labels whose keywords appear in the article title or summary."""
    text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    matched: list[str] = []
    for topic in topics:
        for kw in topic.get("keywords") or []:
            if kw.lower() in text:
                matched.append(topic["label"])
                break
    return matched


def _deduplicate(articles: list[NewsArticle]) -> list[NewsArticle]:
    """Remove duplicate articles based on URL, merging topic_matches."""
    seen: dict[str, NewsArticle] = {}
    for article in articles:
        key = _article_key(article)
        if key in seen:
            existing = seen[key]
            merged_topics = list(
                dict.fromkeys(existing["topic_matches"] + article["topic_matches"])
            )
            existing["topic_matches"] = merged_topics
        else:
            seen[key] = article
    return list(seen.values())


def _article_key(article: NewsArticle) -> str:
    """Stable key for dedup — prefer URL, fall back to title hash."""
    url = (article.get("url") or "").strip()
    if url:
        return url
    title = (article.get("title") or "").strip().lower()
    return hashlib.sha256(title.encode()).hexdigest()


def _sort_key(article: NewsArticle) -> tuple:
    """Sort articles: topic-matched first, then by recency."""
    has_topics = 1 if article.get("topic_matches") else 0
    published = article.get("published_at") or ""
    return (has_topics, published)


def _parse_rss_date(entry: Any) -> str | None:
    """Extract an ISO-8601 date from an RSS entry."""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        try:
            dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    return entry.get("published") or entry.get("updated")


def _clean_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()
