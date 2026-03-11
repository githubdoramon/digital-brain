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
from collections import Counter
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from urllib.parse import parse_qsl, urlparse, urlunparse
from uuid import uuid4

import feedparser
import requests

from db import get_conn
from observability.logger import get_runtime_logger
from search_normalization import normalize_search_text

logger = get_runtime_logger(__name__)

_TOPIC_MATCH_CONFIDENCE_THRESHOLD = 1.5


def _ms_since(start: float) -> float:
    return (perf_counter() - start) * 1000


# ---------------------------------------------------------------------------
# Constants / feed registry
# ---------------------------------------------------------------------------

DEFAULT_TAVILY_NEWS_RESULTS = 5
DEFAULT_NEWSDATA_RESULTS = 10

NEWSDATA_API_URL = os.getenv("NEWSDATA_API_URL", "https://newsdata.io/api/1/news")
NEWSDATA_REQUEST_TIMEOUT = int(os.getenv("NEWSDATA_TIMEOUT", "20"))
NEWSDATA_MAX_QUERIES_PER_RUN = int(os.getenv("NEWSDATA_MAX_QUERIES_PER_RUN", "8"))
NEWSDATA_RESULTS_PER_QUERY = int(os.getenv("NEWSDATA_RESULTS_PER_QUERY", str(DEFAULT_NEWSDATA_RESULTS)))

_TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_name",
    "utm_cid",
    "utm_reader",
    "utm_viz_id",
    "utm_pubreferrer",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "spm",
}

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
    max_newsdata_per_query: int = NEWSDATA_RESULTS_PER_QUERY,
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

    # -- 1. NewsData queries (free-tier budgeted) -----------------------------
    newsdata_start = perf_counter()
    newsdata_total = 0
    newsdata_queries = _build_newsdata_queries(topics)
    for query in newsdata_queries[: max(1, NEWSDATA_MAX_QUERIES_PER_RUN)]:
        results = _search_newsdata_news(query, max_results=max_newsdata_per_query)
        for article in results:
            article["topic_matches"] = _match_topics(article, topics)
        newsdata_total += len(results)
        articles.extend(results)
    logger.info(
        "[news] NewsData: %d article(s) from %d query(ies) (%.0fms)",
        newsdata_total,
        min(len(newsdata_queries), max(1, NEWSDATA_MAX_QUERIES_PER_RUN)),
        _ms_since(newsdata_start),
    )

    # -- 2. Tavily topic searches (one search per topic) ---------------------
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

    # -- 3. RSS feeds --------------------------------------------------------
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

    # -- 4. Canonicalize/cluster/persist + deduplicate & sort ----------------
    for article in articles:
        article["url"] = _canonicalize_url(str(article.get("url") or ""))
        article["source_domain"] = _extract_source_domain(str(article.get("url") or ""))

    articles = _attach_story_clusters(articles)
    _persist_story_mentions(articles)

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


def get_cluster_signal_map(
    cluster_ids: list[str],
    *,
    user_email: str | None = None,
) -> dict[str, dict[str, float]]:
    if not cluster_ids:
        return {}
    clean_ids = [cid for cid in cluster_ids if cid]
    if not clean_ids:
        return {}

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  cluster_id,
                  COUNT(*) FILTER (WHERE ingested_at >= NOW() - INTERVAL '1 day') AS mentions_1d,
                  COUNT(*) FILTER (WHERE ingested_at >= NOW() - INTERVAL '7 day') AS mentions_7d
                FROM news_story_mentions
                WHERE cluster_id = ANY(%s)
                GROUP BY cluster_id
                """,
                (clean_ids,),
            )
            mention_rows = cur.fetchall()

            if user_email:
                cur.execute(
                    """
                    SELECT cluster_id, COUNT(*) AS recent_hits
                    FROM daily_briefing_news_items
                    WHERE cluster_id = ANY(%s)
                      AND user_email = %s
                      AND created_at >= NOW() - INTERVAL '3 day'
                    GROUP BY cluster_id
                    """,
                    (clean_ids, user_email),
                )
            else:
                cur.execute(
                    """
                    SELECT cluster_id, COUNT(*) AS recent_hits
                    FROM daily_briefing_news_items
                    WHERE cluster_id = ANY(%s)
                      AND created_at >= NOW() - INTERVAL '3 day'
                    GROUP BY cluster_id
                    """,
                    (clean_ids,),
                )
            seen_rows = cur.fetchall()
    except Exception:
        logger.warning("[news] Failed to load cluster trend signals", exc_info=True)
        return {}

    seen_map = {
        str(dict(row).get("cluster_id") or ""): float(dict(row).get("recent_hits") or 0.0)
        for row in seen_rows
    }
    out: dict[str, dict[str, float]] = {}
    for row in mention_rows:
        row_data = dict(row)
        cluster_id = str(row_data.get("cluster_id") or "")
        if not cluster_id:
            continue
        mentions_1d = float(row_data.get("mentions_1d") or 0.0)
        mentions_7d = float(row_data.get("mentions_7d") or 0.0)
        baseline = max((mentions_7d - mentions_1d) / 6.0, 1.0)
        trend_score = mentions_1d / baseline
        novelty_penalty = seen_map.get(cluster_id, 0.0)
        out[cluster_id] = {
            "mentions_1d": mentions_1d,
            "mentions_7d": mentions_7d,
            "trend_score": trend_score,
            "novelty_penalty": novelty_penalty,
        }
    return out


def _build_newsdata_queries(topics: list[dict[str, Any]]) -> list[str]:
    queries: list[str] = ["world news"]
    for topic in topics:
        label = str(topic.get("label") or "").strip()
        if label:
            queries.append(label)
        for keyword in topic.get("keywords") or []:
            kw = str(keyword).strip()
            if kw:
                queries.append(kw)

    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = normalize_search_text(query)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(query)
    return deduped


def _search_newsdata_news(
    query: str,
    *,
    max_results: int = DEFAULT_NEWSDATA_RESULTS,
) -> list[NewsArticle]:
    api_key = os.getenv("NEWSDATA_API_KEY")
    if not api_key:
        return []

    params = {
        "apikey": api_key,
        "q": query,
        "language": os.getenv("NEWSDATA_LANGUAGE", "en"),
        "size": max(1, min(max_results, 10)),
    }

    try:
        t0 = perf_counter()
        response = requests.get(
            NEWSDATA_API_URL,
            params=params,
            timeout=NEWSDATA_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        logger.debug(
            "[news.newsdata] Search '%s': %d result(s) (%.0fms)",
            query,
            len(payload.get("results") or []),
            _ms_since(t0),
        )
    except Exception:
        logger.warning("[news.newsdata] Query failed for '%s'", query, exc_info=True)
        return []

    articles: list[NewsArticle] = []
    for item in payload.get("results") or []:
        url = str(item.get("link") or item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        source = str(
            item.get("source_id")
            or item.get("source_name")
            or item.get("source")
            or "newsdata"
        ).strip()
        summary = str(item.get("description") or item.get("content") or "").strip()
        published_at = item.get("pubDate") or item.get("published_at")
        articles.append(
            {
                "title": title,
                "url": url,
                "summary": summary,
                "source": source,
                "provider": "newsdata",
                "published_at": published_at,
                "topic_matches": [],
            }
        )
    return articles


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
        url = str(item.get("url") or "").strip()
        source = str(item.get("source") or item.get("source_name") or "").strip()
        if not source:
            source = _extract_source_domain(url)
        if not source:
            source = "unknown"
        articles.append(
            {
                "title": str(item.get("title") or "").strip(),
                "url": url,
                "summary": str(item.get("content") or "").strip(),
                "source": source,
                "provider": "tavily",
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
                "title": str(entry.get("title") or "").strip(),
                "url": str(entry.get("link") or "").strip(),
                "summary": _clean_html(str(entry.get("summary") or entry.get("description") or "")),
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
    """Return confidence-ranked topic labels for article title/summary."""
    title_text = normalize_search_text(str(article.get("title") or ""))
    summary_text = normalize_search_text(str(article.get("summary") or ""))
    article_text = f"{title_text} {summary_text}".strip()
    if not article_text:
        return []

    scored_matches: list[tuple[str, float]] = []
    for topic in topics:
        topic_label = str(topic.get("label") or "").strip()
        if not topic_label:
            continue
        score = 0.0
        for kw in topic.get("keywords") or []:
            keyword_score = _score_keyword_match(str(kw), title_text, summary_text, article_text)
            if keyword_score <= 0:
                continue
            score += keyword_score
        if score > 0:
            scored_matches.append((topic_label, score))

    if not scored_matches:
        return []

    scored_matches.sort(key=lambda item: item[1], reverse=True)
    top_label, top_score = scored_matches[0]
    if top_score < _TOPIC_MATCH_CONFIDENCE_THRESHOLD:
        return []

    matched = [top_label]
    for label, score in scored_matches[1:]:
        if score < _TOPIC_MATCH_CONFIDENCE_THRESHOLD:
            continue
        if (top_score - score) <= 0.5:
            matched.append(label)
        else:
            break
    return matched[:3]


def _score_keyword_match(
    keyword: str,
    title_text: str,
    summary_text: str,
    article_text: str,
) -> float:
    normalized_keyword = normalize_search_text(keyword)
    if not normalized_keyword:
        return 0.0

    tokens = [token for token in normalized_keyword.split() if token]
    if not tokens:
        return 0.0

    phrase_pattern = _keyword_phrase_pattern(tokens)

    if phrase_pattern.search(title_text):
        return 4.0
    if phrase_pattern.search(summary_text):
        return 3.0

    if len(tokens) > 1:
        if all(_whole_word_present(token, article_text) for token in tokens):
            return 2.0
        return 0.0

    token = tokens[0]
    if len(token) < 2:
        return 0.0

    if _whole_word_present(token, article_text):
        return 1.5

    if len(token) >= 6 and token in article_text:
        return 0.5

    return 0.0


def _keyword_phrase_pattern(tokens: list[str]) -> re.Pattern[str]:
    phrase = r"\b" + r"\s+".join(re.escape(token) for token in tokens) + r"\b"
    return re.compile(phrase)


def _whole_word_present(token: str, text: str) -> bool:
    return bool(re.search(rf"\b{re.escape(token)}\b", text))


def _extract_source_domain(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _canonicalize_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if value.startswith("www."):
        value = f"https://{value}"

    try:
        parsed = urlparse(value)
    except Exception:
        return value

    scheme = parsed.scheme.lower() or "https"
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path or ""
    path = re.sub(r"/+", "/", path)
    if path == "/":
        path = ""
    elif len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    query_pairs = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=False):
        if key.lower() in _TRACKING_QUERY_KEYS:
            continue
        query_pairs.append((key, val))
    query_pairs.sort(key=lambda item: (item[0], item[1]))
    query = "&".join(f"{k}={v}" for k, v in query_pairs)
    return urlunparse((scheme, netloc, path, "", query, ""))


def _story_fingerprint(article: NewsArticle) -> str:
    canonical_url = _canonicalize_url(str(article.get("url") or ""))
    title = normalize_search_text(str(article.get("title") or ""))
    summary = normalize_search_text(str(article.get("summary") or ""))
    title_tokens = [token for token in re.split(r"[^a-z0-9]+", title) if len(token) >= 4]
    summary_tokens = [token for token in re.split(r"[^a-z0-9]+", summary) if len(token) >= 4]
    token_stream = [*title_tokens[:8], *summary_tokens[:6]]
    deduped_tokens = list(dict.fromkeys(token_stream))
    token_signature = " ".join(deduped_tokens)
    if token_signature:
        seed = token_signature
    elif title:
        seed = title
    else:
        seed = canonical_url
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _attach_story_clusters(articles: list[NewsArticle]) -> list[NewsArticle]:
    if not articles:
        return []

    clusters: dict[str, NewsArticle] = {}
    for article in articles:
        fingerprint = _story_fingerprint(article)
        cluster_id = f"story:{fingerprint[:20]}"
        article["story_fingerprint"] = fingerprint
        article["cluster_id"] = cluster_id

        existing = clusters.get(cluster_id)
        if not existing:
            clusters[cluster_id] = article
            continue

        merged_topics = list(dict.fromkeys((existing.get("topic_matches") or []) + (article.get("topic_matches") or [])))
        existing["topic_matches"] = merged_topics

        existing_quality = 1 if existing.get("topic_matches") else 0
        incoming_quality = 1 if article.get("topic_matches") else 0
        if incoming_quality > existing_quality:
            clusters[cluster_id] = article
            clusters[cluster_id]["topic_matches"] = merged_topics

    cluster_counts = Counter(str(article.get("cluster_id") or "") for article in articles)
    for article in articles:
        cluster_id = str(article.get("cluster_id") or "")
        article["cluster_size"] = int(cluster_counts.get(cluster_id, 1))
    return articles


def _persist_story_mentions(articles: list[NewsArticle]) -> None:
    if not articles:
        return
    try:
        with get_conn() as conn, conn.cursor() as cur:
            for article in articles:
                cluster_id = str(article.get("cluster_id") or "")
                fingerprint = str(article.get("story_fingerprint") or "")
                if not cluster_id or not fingerprint:
                    continue
                source_domain = str(article.get("source_domain") or "")
                canonical_url = _canonicalize_url(str(article.get("url") or ""))
                title = str(article.get("title") or "Untitled").strip()
                summary = str(article.get("summary") or "").strip()
                source = str(article.get("source") or "unknown").strip()
                provider = str(article.get("provider") or source).strip()
                published_at = _parse_datetime_for_db(article.get("published_at"))
                metadata = {
                    "cluster_size": article.get("cluster_size"),
                    "topic_matches": article.get("topic_matches") or [],
                }

                cur.execute(
                    """
                    INSERT INTO news_story_clusters (
                      cluster_id,
                      story_fingerprint,
                      canonical_title,
                      canonical_url,
                      source_domain,
                      first_seen_at,
                      last_seen_at,
                      mention_count,
                      metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), 1, %s::jsonb)
                    ON CONFLICT (cluster_id) DO UPDATE
                      SET canonical_title = EXCLUDED.canonical_title,
                          canonical_url = COALESCE(EXCLUDED.canonical_url, news_story_clusters.canonical_url),
                          source_domain = COALESCE(EXCLUDED.source_domain, news_story_clusters.source_domain),
                          last_seen_at = NOW(),
                          mention_count = news_story_clusters.mention_count + 1,
                          metadata = EXCLUDED.metadata
                    """,
                    (
                        cluster_id,
                        fingerprint,
                        title,
                        canonical_url or None,
                        source_domain or None,
                        _to_json(metadata),
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO news_story_mentions (
                      mention_id,
                      cluster_id,
                      provider,
                      source,
                      source_domain,
                      article_url,
                      canonical_url,
                      title,
                      summary,
                      topic_matches,
                      published_at,
                      ingested_at,
                      metadata
                    )
                    VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s::jsonb
                    )
                    """,
                    (
                        f"news_mention:{uuid4().hex}",
                        cluster_id,
                        provider,
                        source,
                        source_domain or None,
                        str(article.get("url") or "").strip() or None,
                        canonical_url or None,
                        title,
                        summary or None,
                        article.get("topic_matches") or [],
                        published_at,
                        _to_json(metadata),
                    ),
                )
            conn.commit()
    except Exception:
        logger.warning("[news] Failed persisting story clusters/mentions", exc_info=True)


def _parse_datetime_for_db(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _to_json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value)


def _deduplicate(articles: list[NewsArticle]) -> list[NewsArticle]:
    """Remove duplicate articles based on URL, merging topic_matches."""
    seen: dict[str, NewsArticle] = {}
    for article in articles:
        key = str(article.get("cluster_id") or _article_key(article))
        if key in seen:
            existing = seen[key]
            merged_topics = list(
                dict.fromkeys(existing["topic_matches"] + article["topic_matches"])
            )
            existing["topic_matches"] = merged_topics
            existing["cluster_size"] = max(
                int(existing.get("cluster_size") or 1),
                int(article.get("cluster_size") or 1),
            )
        else:
            seen[key] = article
    return list(seen.values())


def _article_key(article: NewsArticle) -> str:
    """Stable key for dedup — prefer URL, fall back to title hash."""
    url = _canonicalize_url(str(article.get("url") or ""))
    if url:
        return url
    title = normalize_search_text(str(article.get("title") or "").strip())
    return hashlib.sha256(title.encode()).hexdigest()


def _sort_key(article: NewsArticle) -> tuple:
    """Sort articles: topic-matched first, then by recency."""
    has_topics = 1 if article.get("topic_matches") else 0
    cluster_size = int(article.get("cluster_size") or 1)
    published = article.get("published_at") or ""
    return (has_topics, cluster_size, published)


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
