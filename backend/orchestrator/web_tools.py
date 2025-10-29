from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests


DEFAULT_TAVILY_URL = "https://api.tavily.com/search"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_RESULTS = 10


def internet_search(query: str, max_results: Optional[int] = None) -> Dict[str, Any]:
    """Perform an internet search using the Tavily API.

    Returns a dictionary with the normalized results or an error structure that the
    LLM can interpret. Tavily credentials are optional; if they are missing the
    function returns an informative error instead of raising.
    """

    normalized_query = (query or "").strip()
    if not normalized_query:
        return {
            "error": {
                "message": "Query must not be empty.",
                "code": "empty_query",
            }
        }

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return {
            "error": {
                "message": "TAVILY_API_KEY is not configured.",
                "code": "missing_credentials",
            }
        }

    api_url = os.getenv("TAVILY_API_URL", DEFAULT_TAVILY_URL)
    default_limit = _coerce_int(os.getenv("TAVILY_MAX_RESULTS"), fallback=5)
    timeout_seconds = _coerce_int(os.getenv("TAVILY_TIMEOUT"), fallback=DEFAULT_TIMEOUT_SECONDS)
    search_depth = os.getenv("TAVILY_SEARCH_DEPTH", "basic")

    limit = max_results if isinstance(max_results, int) else default_limit
    limit = max(1, min(limit or 5, MAX_RESULTS))

    payload: Dict[str, Any] = {
        "api_key": api_key,
        "query": normalized_query,
        "max_results": limit,
        "search_depth": search_depth,
        "include_answer": True,
        "include_raw_content": False,
    }

    try:
        response = requests.post(api_url, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()
    except requests.Timeout:
        return {
            "error": {
                "message": "Internet search timed out.",
                "code": "timeout",
            }
        }
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response else None
        return {
            "error": {
                "message": "Internet search request failed.",
                "code": "http_error",
                "status_code": status_code,
            }
        }
    except requests.RequestException as exc:
        return {
            "error": {
                "message": f"Network error during internet search: {exc}",
                "code": "network_error",
            }
        }
    except ValueError:
        return {
            "error": {
                "message": "Failed to decode search response.",
                "code": "invalid_json",
            }
        }

    normalized_results = _normalize_tavily_response(data)
    normalized_results["query"] = normalized_query
    return normalized_results


def _normalize_tavily_response(data: Dict[str, Any]) -> Dict[str, Any]:
    results_raw = data.get("results")
    results: List[Dict[str, Any]] = []
    if isinstance(results_raw, list):
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            url = item.get("url") or item.get("link")
            content = (item.get("content") or item.get("snippet") or "").strip()
            snippet = content[:500]
            score = item.get("score")
            published = item.get("published_date") or item.get("published_time")

            entry: Dict[str, Any] = {}
            if title:
                entry["title"] = title
            if url:
                entry["url"] = url
            if snippet:
                entry["snippet"] = snippet
            if score is not None:
                entry["score"] = score
            if published:
                entry["published_at"] = published

            if entry:
                results.append(entry)

    answer = data.get("answer") or data.get("summary")
    follow_ups = data.get("follow_up_questions")
    return {
        "results": results,
        "summary": answer if isinstance(answer, str) else None,
        "follow_up_questions": follow_ups if isinstance(follow_ups, list) else None,
        "provider": "tavily",
        "response_id": data.get("response_id"),
    }


def _coerce_int(value: Optional[str], fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


