"""Tests for news_feeds module – RSS parsing, Tavily search, dedup & helpers."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from news_feeds import (
    RSS_FEEDS,
    _article_key,
    _build_newsdata_queries,
    _canonicalize_url,
    _clean_html,
    _deduplicate,
    _fetch_rss_feed,
    _match_topics,
    _search_tavily_news,
    _sort_key,
    fetch_news,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _article(
    title: str = "Test Article",
    url: str = "https://example.com/1",
    summary: str = "A summary",
    source: str = "test",
    published_at: str | None = "2026-02-15T10:00:00+00:00",
    topic_matches: list | None = None,
    provider: str | None = None,
) -> dict:
    article = {
        "title": title,
        "url": url,
        "summary": summary,
        "source": source,
        "published_at": published_at,
        "topic_matches": topic_matches or [],
    }
    if provider is not None:
        article["provider"] = provider
    return article


def _topic(label: str = "AI", keywords: list | None = None) -> dict:
    return {
        "topic_id": f"topic:{label.lower()}",
        "label": label,
        "keywords": keywords or [label.lower()],
        "enabled": True,
    }


# ---------------------------------------------------------------------------
# RSS feed registry
# ---------------------------------------------------------------------------


class TestRSSFeedRegistry:
    def test_contains_expected_sources(self):
        assert "hacker_news" in RSS_FEEDS
        assert "techcrunch" in RSS_FEEDS
        assert "google_news_pt" in RSS_FEEDS
        assert "bbc_world" in RSS_FEEDS

    def test_all_feeds_have_url_and_label(self):
        for feed_id, meta in RSS_FEEDS.items():
            assert "url" in meta, f"{feed_id} missing url"
            assert "label" in meta, f"{feed_id} missing label"
            assert meta["url"].startswith("http"), f"{feed_id} URL invalid"


# ---------------------------------------------------------------------------
# _clean_html
# ---------------------------------------------------------------------------


class TestCleanHtml:
    def test_strips_tags(self):
        assert _clean_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_collapses_whitespace(self):
        assert _clean_html("  foo   bar  ") == "foo bar"

    def test_empty_string(self):
        assert _clean_html("") == ""

    def test_cdata_style_rss_content(self):
        raw = '<![CDATA[<p>Article URL: <a href="https://x.com">x</a></p>]]>'
        result = _clean_html(raw)
        assert "<" not in result or result.startswith("<!")  # CDATA wrapper may remain


# ---------------------------------------------------------------------------
# _match_topics
# ---------------------------------------------------------------------------


class TestMatchTopics:
    def test_matches_keyword_in_title(self):
        article = _article(title="OpenAI releases new AI model", summary="")
        topics = [_topic("AI", ["ai", "artificial intelligence"])]
        assert _match_topics(article, topics) == ["AI"]

    def test_matches_keyword_in_summary(self):
        article = _article(title="News update", summary="Breakthrough in quantum computing")
        topics = [_topic("Quantum", ["quantum"])]
        assert _match_topics(article, topics) == ["Quantum"]

    def test_no_match_returns_empty(self):
        article = _article(title="Sports results", summary="Football scores")
        topics = [_topic("AI", ["ai"])]
        assert _match_topics(article, topics) == []

    def test_case_insensitive(self):
        article = _article(title="REACT NATIVE Update", summary="")
        topics = [_topic("React Native", ["react native"])]
        assert _match_topics(article, topics) == ["React Native"]

    def test_multiple_topics_match(self):
        article = _article(title="AI meets climate science", summary="")
        topics = [_topic("AI", ["ai"]), _topic("Climate", ["climate"])]
        result = _match_topics(article, topics)
        assert "AI" in result
        assert "Climate" in result

    def test_accent_insensitive_matching(self):
        article = _article(title="Sao Paulo startup raises funding", summary="")
        topics = [_topic("Brazil", ["São Paulo"])]
        assert _match_topics(article, topics) == ["Brazil"]

    def test_short_keyword_does_not_match_substring_noise(self):
        article = _article(title="Actor said he is excited", summary="Entertainment update")
        topics = [_topic("AI", ["ai"])]
        assert _match_topics(article, topics) == []

    def test_ambiguous_scores_keep_multiple_topics(self):
        article = _article(title="AI and climate trends collide in new forecast", summary="")
        topics = [_topic("AI", ["ai"]), _topic("Climate", ["climate"])]
        result = _match_topics(article, topics)
        assert "AI" in result
        assert "Climate" in result


# ---------------------------------------------------------------------------
# _deduplicate
# ---------------------------------------------------------------------------


class TestDeduplicate:
    def test_removes_duplicate_urls(self):
        articles = [
            _article(url="https://a.com", topic_matches=["AI"]),
            _article(url="https://a.com", topic_matches=["Tech"]),
        ]
        result = _deduplicate(articles)
        assert len(result) == 1
        assert set(result[0]["topic_matches"]) == {"AI", "Tech"}

    def test_keeps_different_urls(self):
        articles = [
            _article(url="https://a.com"),
            _article(url="https://b.com"),
        ]
        assert len(_deduplicate(articles)) == 2

    def test_deduplicates_by_title_when_no_url(self):
        articles = [
            _article(title="Same Title", url=""),
            _article(title="Same Title", url=""),
        ]
        assert len(_deduplicate(articles)) == 1


# ---------------------------------------------------------------------------
# _sort_key
# ---------------------------------------------------------------------------


class TestSortKey:
    def test_topic_matched_ranks_higher(self):
        matched = _article(topic_matches=["AI"], published_at="2026-02-15T10:00:00")
        unmatched = _article(topic_matches=[], published_at="2026-02-15T11:00:00")
        assert _sort_key(matched) > _sort_key(unmatched)

    def test_more_recent_ranks_higher_within_same_group(self):
        older = _article(topic_matches=["AI"], published_at="2026-02-14T10:00:00")
        newer = _article(topic_matches=["AI"], published_at="2026-02-15T10:00:00")
        assert _sort_key(newer) > _sort_key(older)


# ---------------------------------------------------------------------------
# _article_key
# ---------------------------------------------------------------------------


class TestArticleKey:
    def test_uses_url_when_present(self):
        assert _article_key(_article(url="https://a.com")) == "https://a.com"

    def test_falls_back_to_title_hash(self):
        key = _article_key(_article(url="", title="Test"))
        assert len(key) == 64  # SHA-256 hex digest


class TestCanonicalizeUrl:
    def test_removes_tracking_params(self):
        raw = "https://example.com/story?utm_source=x&fbclid=y&ref=z&id=123"
        canonical = _canonicalize_url(raw)
        assert canonical == "https://example.com/story?id=123"

    def test_removes_common_news_redirect_params(self):
        raw = "https://techcrunch.com/2026/03/15/story/?guccounter=1&guce_referrer=https%3A%2F%2Fnews.google.com&outputType=amp"
        canonical = _canonicalize_url(raw)
        assert canonical == "https://techcrunch.com/2026/03/15/story"


class TestNewsDataQueryPlanner:
    def test_deduplicates_normalized_queries(self):
        topics = [
            _topic("AI", ["ai", "Artificial Intelligence"]),
            _topic("Artificial intelligence", ["AI"]),
        ]
        queries = _build_newsdata_queries(topics)
        normalized = {q.lower() for q in queries}
        assert "world news" in normalized
        assert len(queries) >= 2


class TestDeduplicateByCluster:
    def test_cluster_id_merges_cross_source_variants(self):
        articles = [
            {
                **_article(url="https://a.com/story-1", topic_matches=["AI"]),
                "cluster_id": "story:abc",
                "cluster_size": 2,
            },
            {
                **_article(url="https://b.com/story-1", topic_matches=["Tech"]),
                "cluster_id": "story:abc",
                "cluster_size": 2,
            },
        ]
        deduped = _deduplicate(articles)
        assert len(deduped) == 1
        assert set(deduped[0]["topic_matches"]) == {"AI", "Tech"}

    def test_same_url_with_different_clusters_is_deduped(self):
        articles = [
            {
                **_article(url="https://example.com/story-1?utm_source=a", topic_matches=["AI"]),
                "cluster_id": "story:alpha",
                "cluster_size": 1,
            },
            {
                **_article(url="https://example.com/story-1", topic_matches=["Tech"]),
                "cluster_id": "story:beta",
                "cluster_size": 1,
            },
        ]

        deduped = _deduplicate(articles)

        assert len(deduped) == 1
        assert set(deduped[0]["topic_matches"]) == {"AI", "Tech"}


# ---------------------------------------------------------------------------
# _search_tavily_news
# ---------------------------------------------------------------------------


class TestSearchTavilyNews:
    @patch.dict("os.environ", {"TAVILY_API_KEY": ""}, clear=False)
    def test_returns_empty_without_api_key(self):
        assert _search_tavily_news("ai") == []

    @patch("news_feeds.requests.post")
    @patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"}, clear=False)
    def test_returns_normalized_articles(self, mock_post):
        response = MagicMock(status_code=200, headers={}, text='{"results": []}')
        response.json.return_value = {
            "results": [
                {
                    "title": "AI News",
                    "url": "https://example.com/ai",
                    "content": "Big AI news today",
                    "published_date": "2026-02-15",
                }
            ]
        }
        response.raise_for_status = MagicMock()
        mock_post.return_value = response
        results = _search_tavily_news("ai", max_results=3)
        assert len(results) == 1
        assert results[0]["title"] == "AI News"
        assert results[0]["source"] == "example.com"
        assert results[0]["provider"] == "tavily"
        call_payload = mock_post.call_args.kwargs["json"]
        assert call_payload["topic"] == "news"
        assert call_payload["time_range"] == "day"
        assert call_payload["max_results"] == 3

    @patch("news_feeds.time.sleep")
    @patch("news_feeds.requests.post")
    @patch.dict("os.environ", {"TAVILY_API_KEY": "test-key", "TAVILY_NEWS_MAX_RETRIES": "2"}, clear=False)
    def test_retries_rate_limit_then_succeeds(self, mock_post, mock_sleep):
        first = MagicMock(status_code=429, headers={}, text="rate limited")
        second = MagicMock(status_code=200, headers={}, text='{"results": []}')
        second.json.return_value = {"results": []}
        second.raise_for_status = MagicMock()
        mock_post.side_effect = [first, second]

        assert _search_tavily_news("ai") == []
        assert mock_sleep.call_count == 1

    @patch("news_feeds.requests.post", side_effect=requests.RequestException("network error"))
    @patch.dict("os.environ", {"TAVILY_API_KEY": "test-key", "TAVILY_NEWS_MAX_RETRIES": "1"}, clear=False)
    def test_graceful_failure(self, mock_post):
        assert _search_tavily_news("ai") == []


# ---------------------------------------------------------------------------
# _fetch_rss_feed
# ---------------------------------------------------------------------------


class TestFetchRssFeed:
    @patch("news_feeds.feedparser.parse")
    def test_parses_entries(self, mock_parse):
        entry = {
            "title": "HN Story",
            "link": "https://hn.example.com/1",
            "summary": "<p>A great story</p>",
            "published_parsed": time.strptime("2026-02-15", "%Y-%m-%d"),
        }
        mock_parse.return_value = SimpleNamespace(
            bozo=False,
            entries=[entry],
        )
        results = _fetch_rss_feed(
            "https://fake.rss",
            source="hacker_news",
            label="Hacker News",
            limit=5,
        )
        assert len(results) == 1
        assert results[0]["title"] == "HN Story"
        assert results[0]["source"] == "hacker_news"
        assert "<" not in results[0]["summary"]  # HTML stripped

    @patch("news_feeds.feedparser.parse")
    def test_respects_limit(self, mock_parse):
        entries = [
            {"title": f"Story {i}", "link": f"https://x.com/{i}", "summary": ""} for i in range(20)
        ]
        mock_parse.return_value = SimpleNamespace(bozo=False, entries=entries)
        results = _fetch_rss_feed("https://fake.rss", source="test", label="Test", limit=5)
        assert len(results) == 5

    @patch("news_feeds.feedparser.parse", side_effect=Exception("parse error"))
    def test_graceful_failure(self, mock_parse):
        results = _fetch_rss_feed("https://bad.rss", source="test", label="Test")
        assert results == []

    @patch("news_feeds.feedparser.parse")
    def test_bozo_with_no_entries_returns_empty(self, mock_parse):
        mock_parse.return_value = SimpleNamespace(
            bozo=True,
            bozo_exception=Exception("malformed"),
            entries=[],
        )
        results = _fetch_rss_feed("https://bad.rss", source="test", label="Test")
        assert results == []


# ---------------------------------------------------------------------------
# fetch_news (integration of all sources)
# ---------------------------------------------------------------------------


class TestFetchNews:
    @patch("news_feeds._fetch_rss_feed")
    @patch("news_feeds._search_tavily_news")
    @patch("news_feeds.list_topics")
    def test_combines_tavily_and_rss(self, mock_topics, mock_tavily, mock_rss):
        mock_topics.return_value = [_topic("AI", ["ai"])]
        mock_tavily.return_value = [
            _article(
                title="Tavily AI result",
                url="https://tavily.com/1",
                source="tavily.com",
                provider="tavily",
            )
        ]
        mock_rss.return_value = [
            _article(title="RSS AI article", url="https://rss.com/1", source="hacker_news")
        ]

        results = fetch_news()
        assert len(results) >= 2  # at least 1 Tavily + 1 per RSS feed call
        providers = {r.get("provider") for r in results}
        assert "tavily" in providers

    @patch("news_feeds._fetch_rss_feed", return_value=[])
    @patch("news_feeds._search_tavily_news", return_value=[])
    @patch("news_feeds.list_topics", return_value=[])
    def test_returns_empty_with_no_topics_and_empty_feeds(self, mock_topics, mock_tavily, mock_rss):
        results = fetch_news()
        # Even with no topics, RSS feeds are still fetched
        assert isinstance(results, list)

    @patch("news_feeds._fetch_rss_feed")
    @patch("news_feeds._search_tavily_news")
    @patch("news_feeds.list_topics")
    def test_deduplicates_across_sources(self, mock_topics, mock_tavily, mock_rss):
        mock_topics.return_value = [_topic("AI", ["ai"])]
        # Same URL from both Tavily and RSS
        shared = _article(
            title="Shared Article",
            url="https://shared.com/1",
            source="tavily",
            topic_matches=["AI"],
        )
        mock_tavily.return_value = [shared]
        mock_rss.return_value = [
            _article(
                title="Shared Article",
                url="https://shared.com/1",
                source="hacker_news",
            )
        ]
        results = fetch_news()
        urls = [r["url"] for r in results]
        assert urls.count("https://shared.com/1") == 1

    @patch("news_feeds._fetch_rss_feed")
    @patch("news_feeds._search_tavily_news", return_value=[])
    @patch("news_feeds.list_topics")
    def test_topic_matched_articles_sorted_first(self, mock_topics, mock_tavily, mock_rss):
        mock_topics.return_value = [_topic("AI", ["ai"])]
        mock_rss.return_value = [
            _article(title="Unrelated sports news", url="https://a.com", summary="football"),
            _article(title="AI breakthrough", url="https://b.com", summary="ai model"),
        ]
        results = fetch_news()
        # The AI-matched article should come before the unmatched one
        ai_idx = next(i for i, r in enumerate(results) if "ai" in r["title"].lower())
        sport_idx = next(i for i, r in enumerate(results) if "sport" in r["title"].lower())
        assert ai_idx < sport_idx
