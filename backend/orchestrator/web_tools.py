from __future__ import annotations

import os
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from langsearch_client import search_web
from observability.logger import get_runtime_logger

DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_FETCH_TIMEOUT_SECONDS = 20
MAX_RESULTS = 10
DEFAULT_MAX_CHARACTERS = 20000
DEFAULT_USER_AGENT = "digital-brain/1.0"

logger = get_runtime_logger(__name__)


def internet_search(query: str, max_results: int | None = None) -> dict[str, Any]:
    """Perform an internet search using the LangSearch API."""

    normalized_query = (query or "").strip()
    if not normalized_query:
        return {
            "error": {
                "message": "Query must not be empty.",
                "code": "empty_query",
            }
        }

    default_limit = _coerce_int(os.getenv("LANGSEARCH_MAX_RESULTS"), fallback=5)
    timeout_seconds = _coerce_int(os.getenv("LANGSEARCH_TIMEOUT"), fallback=DEFAULT_TIMEOUT_SECONDS)
    freshness = os.getenv("LANGSEARCH_FRESHNESS", "noLimit")

    limit = max_results if isinstance(max_results, int) else default_limit
    limit = max(1, min(limit or 5, MAX_RESULTS))

    search_response = search_web(
        query=normalized_query,
        count=limit,
        freshness=freshness,
        summary=True,
        timeout_seconds=timeout_seconds,
        request_label="tool:web_search",
    )
    if search_response.get("error"):
        return search_response

    normalized_results = _normalize_langsearch_response(search_response.get("data") or {})
    normalized_results["query"] = normalized_query
    meta = search_response.get("meta")
    if isinstance(meta, dict):
        normalized_results["request_meta"] = meta
    return normalized_results


def fetch_web_page(
    url: str,
    *,
    include_links: bool | None = None,
    include_images: bool | None = None,
    max_characters: int | None = None,
    include_raw_html: bool | None = None,
) -> dict[str, Any]:
    """Retrieve a web page directly and extract readable content."""

    normalized_url = _normalize_url(url)
    if normalized_url is None:
        return {
            "error": {
                "message": "URL must be a valid HTTP(S) address.",
                "code": "invalid_url",
            }
        }

    default_include_links = _coerce_bool(os.getenv("WEB_FETCH_INCLUDE_LINKS"), fallback=False)
    include_links_flag = default_include_links if include_links is None else _coerce_bool(include_links, fallback=default_include_links)
    default_include_images = _coerce_bool(os.getenv("WEB_FETCH_INCLUDE_IMAGES"), fallback=False)
    include_images_flag = default_include_images if include_images is None else _coerce_bool(include_images, fallback=default_include_images)
    default_include_raw_html = _coerce_bool(os.getenv("WEB_FETCH_INCLUDE_HTML"), fallback=False)
    include_raw_html_flag = default_include_raw_html if include_raw_html is None else _coerce_bool(include_raw_html, fallback=default_include_raw_html)

    max_chars = _coerce_int(os.getenv("WEB_FETCH_MAX_CHARACTERS"), fallback=DEFAULT_MAX_CHARACTERS)
    if max_characters is not None:
        try:
            max_chars = int(max_characters)
        except (TypeError, ValueError):
            max_chars = _coerce_int(os.getenv("WEB_FETCH_MAX_CHARACTERS"), fallback=DEFAULT_MAX_CHARACTERS)
    if max_chars <= 0:
        max_chars = DEFAULT_MAX_CHARACTERS

    timeout_seconds = _coerce_int(os.getenv("WEB_FETCH_TIMEOUT"), fallback=DEFAULT_FETCH_TIMEOUT_SECONDS)
    headers = {
        "User-Agent": os.getenv("WEB_FETCH_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": "text/html,application/xhtml+xml,text/plain,application/json;q=0.9,*/*;q=0.8",
    }

    try:
        response = requests.get(
            normalized_url,
            headers=headers,
            timeout=timeout_seconds,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.Timeout:
        logger.error(
            "[web_fetch] Request timed out url=%s timeout=%ss",
            normalized_url,
            timeout_seconds,
        )
        return {
            "error": {
                "message": "Web page fetch timed out.",
                "code": "timeout",
            }
        }
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response else None
        logger.error(
            "[web_fetch] HTTP error url=%s status=%s timeout=%ss response=%s",
            normalized_url,
            status_code,
            timeout_seconds,
            _preview_response_text(exc.response),
        )
        return {
            "error": {
                "message": "Web page fetch request failed.",
                "code": "http_error",
                "status_code": status_code,
            }
        }
    except requests.RequestException as exc:
        logger.error(
            "[web_fetch] Network error url=%s timeout=%ss error=%s",
            normalized_url,
            timeout_seconds,
            exc,
        )
        return {
            "error": {
                "message": f"Network error during web page fetch: {exc}",
                "code": "network_error",
            }
        }

    content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    final_url = response.url or normalized_url

    if content_type in {"text/plain", "application/json", "application/xml", "text/xml"}:
        raw_content = response.text.strip()
        if len(raw_content) > max_chars:
            raw_content = raw_content[:max_chars]
        document: dict[str, Any] = {
            "url": final_url,
            "content": raw_content,
            "status_code": response.status_code,
        }
        if content_type == "application/json":
            document["title"] = "JSON document"
        return {
            "documents": [document],
            "provider": "direct_fetch",
            "requested_url": normalized_url,
        }

    if content_type and "html" not in content_type:
        logger.error(
            "[web_fetch] Unsupported content type url=%s content_type=%s status=%s",
            normalized_url,
            content_type,
            response.status_code,
        )
        return {
            "error": {
                "message": f"Unsupported content type for web page fetch: {content_type}",
                "code": "unsupported_content_type",
                "content_type": content_type,
            }
        }

    parser = _ReadableHtmlParser(final_url)
    parser.feed(response.text)
    parser.close()

    raw_content = parser.get_text()
    if len(raw_content) > max_chars:
        raw_content = raw_content[:max_chars]

    document = {
        "url": final_url,
        "content": raw_content,
        "status_code": response.status_code,
    }
    if parser.title:
        document["title"] = parser.title
    if include_links_flag and parser.links:
        document["links"] = parser.links
    if include_images_flag and parser.images:
        document["images"] = parser.images
    if include_raw_html_flag:
        raw_html = response.text
        if len(raw_html) > max_chars:
            raw_html = raw_html[:max_chars]
        document["raw_html"] = raw_html

    return {
        "documents": [document],
        "provider": "direct_fetch",
        "requested_url": normalized_url,
    }


def _normalize_langsearch_response(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("data") if isinstance(data, dict) else None
    web_pages = payload.get("webPages") if isinstance(payload, dict) else None
    results_raw = web_pages.get("value") if isinstance(web_pages, dict) else None
    results: list[dict[str, Any]] = []

    if isinstance(results_raw, list):
        for item in results_raw:
            if not isinstance(item, dict):
                continue

            title = (item.get("name") or "").strip()
            url = item.get("url") or item.get("displayUrl")
            snippet = (item.get("summary") or item.get("snippet") or "").strip()
            published = item.get("datePublished")
            crawled = item.get("dateLastCrawled")

            entry: dict[str, Any] = {}
            if title:
                entry["title"] = title
            if url:
                entry["url"] = url
            if snippet:
                entry["snippet"] = snippet[:500]
            if published:
                entry["published_at"] = published
            if crawled:
                entry["last_crawled_at"] = crawled
            if item.get("summary"):
                entry["summary"] = str(item["summary"]).strip()

            if entry:
                results.append(entry)

    query_context = payload.get("queryContext") if isinstance(payload, dict) else None
    return {
        "results": results,
        "summary": None,
        "follow_up_questions": None,
        "provider": "langsearch",
        "response_id": data.get("log_id") if isinstance(data, dict) else None,
        "query_context": query_context if isinstance(query_context, dict) else None,
    }


def _normalize_url(url: str) -> str | None:
    normalized_url = (url or "").strip()
    if not normalized_url:
        return None

    parsed = urlparse(normalized_url)
    if not parsed.scheme:
        normalized_url = f"https://{normalized_url}"
        parsed = urlparse(normalized_url)

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return normalized_url


def _preview_response_text(response: requests.Response | None) -> str:
    if response is None:
        return ""
    text = (response.text or "").strip().replace("\n", " ")
    return text[:400]


def _coerce_int(value: str | None, fallback: int) -> int:
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


class _ReadableHtmlParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title: str | None = None
        self.links: list[dict[str, Any]] = []
        self.images: list[dict[str, Any]] = []
        self._text_parts: list[str] = []
        self._capture_title = False
        self._skip_depth = 0
        self._current_link_href: str | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._capture_title = True
            return
        if tag == "a":
            href = attrs_dict.get("href")
            self._current_link_href = urljoin(self.base_url, href) if href else None
            self._current_link_text = []
            return
        if tag == "img":
            src = attrs_dict.get("src")
            if src:
                image: dict[str, Any] = {"url": urljoin(self.base_url, src)}
                alt = (attrs_dict.get("alt") or "").strip()
                if alt:
                    image["alt"] = alt
                self.images.append(image)
            return
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._capture_title = False
            return
        if tag == "a":
            if self._current_link_href:
                link_text = " ".join(part.strip() for part in self._current_link_text if part.strip()).strip()
                entry: dict[str, Any] = {"url": self._current_link_href}
                if link_text:
                    entry["text"] = link_text
                self.links.append(entry)
            self._current_link_href = None
            self._current_link_text = []
            return
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._capture_title:
            self.title = cleaned if not self.title else f"{self.title} {cleaned}".strip()
            return
        self._text_parts.append(cleaned)
        if self._current_link_href is not None:
            self._current_link_text.append(cleaned)

    def get_text(self) -> str:
        lines = [line.strip() for line in "".join(self._text_parts).splitlines()]
        text = "\n".join(line for line in lines if line)
        return text.strip()
