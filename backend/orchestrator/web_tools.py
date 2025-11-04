from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests


DEFAULT_TAVILY_URL = "https://api.tavily.com/search"
DEFAULT_TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_RESULTS = 10
DEFAULT_MAX_CHARACTERS = 20000


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


def fetch_web_page(
    url: str,
    *,
    include_links: Optional[bool] = None,
    include_images: Optional[bool] = None,
    max_characters: Optional[int] = None,
    include_raw_html: Optional[bool] = None,
) -> Dict[str, Any]:
    """Retrieve the textual contents of a web page using Tavily's extract API."""

    normalized_url = (url or "").strip()
    if not normalized_url:
        return {
            "error": {
                "message": "URL must not be empty.",
                "code": "empty_url",
            }
        }

    parsed = urlparse(normalized_url)
    if not parsed.scheme:
        normalized_url = f"https://{normalized_url}"
        parsed = urlparse(normalized_url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "error": {
                "message": "URL must be a valid HTTP(S) address.",
                "code": "invalid_url",
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

    api_url = os.getenv("TAVILY_EXTRACT_URL", DEFAULT_TAVILY_EXTRACT_URL)
    timeout_seconds = _coerce_int(os.getenv("TAVILY_EXTRACT_TIMEOUT"), fallback=DEFAULT_TIMEOUT_SECONDS)

    default_include_links = _coerce_bool(os.getenv("TAVILY_EXTRACT_INCLUDE_LINKS"), fallback=False)
    include_links_flag = default_include_links if include_links is None else _coerce_bool(include_links, fallback=default_include_links)

    default_include_images = _coerce_bool(os.getenv("TAVILY_EXTRACT_INCLUDE_IMAGES"), fallback=False)
    include_images_flag = default_include_images if include_images is None else _coerce_bool(include_images, fallback=default_include_images)

    default_include_raw_html = _coerce_bool(os.getenv("TAVILY_EXTRACT_INCLUDE_HTML"), fallback=False)
    include_raw_html_flag = default_include_raw_html if include_raw_html is None else _coerce_bool(include_raw_html, fallback=default_include_raw_html)

    default_max_characters = _coerce_int(os.getenv("TAVILY_EXTRACT_MAX_CHARACTERS"), fallback=DEFAULT_MAX_CHARACTERS)
    max_chars = default_max_characters
    if max_characters is not None:
        try:
            max_chars = int(max_characters)
        except (TypeError, ValueError):
            max_chars = default_max_characters
    if max_chars <= 0:
        max_chars = DEFAULT_MAX_CHARACTERS

    payload: Dict[str, Any] = {
        "api_key": api_key,
        "urls": [normalized_url],
        "include_links": include_links_flag,
        "include_images": include_images_flag,
        "include_raw_html": include_raw_html_flag,
    }

    try:
        response = requests.post(api_url, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json()
    except requests.Timeout:
        return {
            "error": {
                "message": "Web page fetch timed out.",
                "code": "timeout",
            }
        }
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response else None
        return {
            "error": {
                "message": "Web page fetch request failed.",
                "code": "http_error",
                "status_code": status_code,
            }
        }
    except requests.RequestException as exc:
        return {
            "error": {
                "message": f"Network error during web page fetch: {exc}",
                "code": "network_error",
            }
        }
    except ValueError:
        return {
            "error": {
                "message": "Failed to decode fetch response.",
                "code": "invalid_json",
            }
        }

    if not isinstance(data, dict):
        return {
            "error": {
                "message": "Unexpected response format from Tavily.",
                "code": "invalid_response",
            }
        }

    api_error = data.get("error")
    if api_error:
        if isinstance(api_error, dict):
            return {"error": api_error}
        return {
            "error": {
                "message": str(api_error),
                "code": "provider_error",
            }
        }

    normalized = _normalize_tavily_extract_response(data, normalized_url, max_chars)
    normalized["provider"] = "tavily"
    normalized["requested_url"] = normalized_url
    return normalized


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


def _coerce_bool(value: Any, fallback: bool = False) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        return fallback
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


def _normalize_tavily_extract_response(
    data: Dict[str, Any],
    fallback_url: str,
    max_characters: int,
) -> Dict[str, Any]:
    documents: List[Dict[str, Any]] = []

    raw_results = data.get("results")
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue

            doc_url = item.get("url") or item.get("source_url") or fallback_url
            title = (item.get("title") or "").strip()
            published = item.get("published_date") or item.get("published_time")

            raw_content = None
            for key in ("content", "markdown", "raw_content", "text"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    raw_content = candidate.strip()
                    break

            summary = item.get("summary")
            if isinstance(summary, str):
                summary = summary.strip() or None

            if isinstance(raw_content, str) and max_characters and len(raw_content) > max_characters:
                raw_content = raw_content[:max_characters]

            doc: Dict[str, Any] = {"url": doc_url}
            if title:
                doc["title"] = title
            if raw_content:
                doc["content"] = raw_content
            if summary:
                doc["summary"] = summary
            if published:
                doc["published_at"] = published

            status_code = item.get("status_code")
            if isinstance(status_code, int):
                doc["status_code"] = status_code

            links_field = item.get("links")
            if isinstance(links_field, list):
                cleaned_links: List[Dict[str, Any]] = []
                for link in links_field:
                    if isinstance(link, dict):
                        link_url = link.get("url") or link.get("href")
                        link_text = link.get("text") or link.get("title")
                        entry: Dict[str, Any] = {}
                        if link_url:
                            entry["url"] = link_url
                        if link_text:
                            entry["text"] = link_text
                        if entry:
                            cleaned_links.append(entry)
                    elif isinstance(link, str) and link.strip():
                        cleaned_links.append({"url": link.strip()})
                if cleaned_links:
                    doc["links"] = cleaned_links

            if doc:
                documents.append(doc)

    response: Dict[str, Any] = {
        "documents": documents,
    }

    response_id = data.get("response_id")
    if isinstance(response_id, str):
        response["response_id"] = response_id

    usage = data.get("usage")
    if isinstance(usage, dict):
        response["usage"] = usage

    return response



