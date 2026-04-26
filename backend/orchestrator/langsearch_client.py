from __future__ import annotations

import os
import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from observability.logger import get_runtime_logger

DEFAULT_LANGSEARCH_URL = "https://api.langsearch.com/v1/web-search"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY_SECONDS = 1.5
DEFAULT_RETRY_MAX_DELAY_SECONDS = 8.0
DEFAULT_MIN_INTERVAL_SECONDS = 1.05
MAX_RESULTS = 10

logger = get_runtime_logger(__name__)
_rate_limit_lock = threading.Lock()
_next_request_time_monotonic = 0.0


def search_web(
    *,
    query: str,
    count: int,
    freshness: str,
    summary: bool,
    timeout_seconds: int,
    request_label: str,
) -> dict[str, Any]:
    normalized_query = (query or "").strip()
    if not normalized_query:
        return _error_result("Query must not be empty.", code="empty_query")

    api_key = os.getenv("LANGSEARCH_API_KEY")
    if not api_key:
        logger.warning("[langsearch] Missing API key for %s request", request_label)
        return _error_result("LANGSEARCH_API_KEY is not configured.", code="missing_credentials")

    api_url = os.getenv("LANGSEARCH_API_URL", DEFAULT_LANGSEARCH_URL)
    max_retries = max(1, _coerce_int(os.getenv("LANGSEARCH_MAX_RETRIES"), fallback=DEFAULT_MAX_RETRIES))
    base_delay = max(0.0, _coerce_float(os.getenv("LANGSEARCH_RETRY_BASE_DELAY_SECONDS"), fallback=DEFAULT_RETRY_BASE_DELAY_SECONDS))
    max_delay = max(base_delay or DEFAULT_RETRY_BASE_DELAY_SECONDS, _coerce_float(os.getenv("LANGSEARCH_RETRY_MAX_DELAY_SECONDS"), fallback=DEFAULT_RETRY_MAX_DELAY_SECONDS))
    min_interval_seconds = max(0.0, _coerce_float(os.getenv("LANGSEARCH_MIN_INTERVAL_SECONDS"), fallback=DEFAULT_MIN_INTERVAL_SECONDS))

    payload = {
        "query": normalized_query,
        "count": max(1, min(count, MAX_RESULTS)),
        "summary": summary,
        "freshness": freshness,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error: dict[str, Any] | None = None
    for attempt in range(1, max_retries + 1):
        _wait_for_rate_limit_slot(min_interval_seconds)
        started_at = time.perf_counter()
        response: requests.Response | None = None
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=timeout_seconds)
            elapsed_ms = (time.perf_counter() - started_at) * 1000

            if _is_retryable_status(response.status_code):
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                preview = _preview_response_text(response)
                last_error = _error_result(
                    "LangSearch request failed.",
                    code="rate_limited" if response.status_code == 429 else "service_unavailable",
                    status_code=response.status_code,
                    retry_after_seconds=retry_after,
                    response_preview=preview,
                )
                if attempt < max_retries:
                    delay = _compute_retry_delay(
                        attempt=attempt,
                        retry_after_seconds=retry_after,
                        base_delay=base_delay,
                        max_delay=max_delay,
                    )
                    logger.warning(
                        "[langsearch] %s request retryable HTTP status=%s elapsed=%.1fms timeout=%ss attempt=%d/%d retry_in=%.2fs query=%r response=%s",
                        request_label,
                        response.status_code,
                        elapsed_ms,
                        timeout_seconds,
                        attempt,
                        max_retries,
                        delay,
                        normalized_query,
                        preview,
                    )
                    time.sleep(delay)
                    continue

                logger.error(
                    "[langsearch] %s request failed after retries status=%s elapsed=%.1fms timeout=%ss attempts=%d query=%r response=%s",
                    request_label,
                    response.status_code,
                    elapsed_ms,
                    timeout_seconds,
                    attempt,
                    normalized_query,
                    preview,
                )
                return last_error

            response.raise_for_status()
            data = response.json()
            logger.info(
                "[langsearch] %s request succeeded status=%s elapsed=%.1fms attempt=%d/%d query=%r",
                request_label,
                response.status_code,
                elapsed_ms,
                attempt,
                max_retries,
                normalized_query,
            )
            return {
                "data": data,
                "meta": {
                    "attempt": attempt,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                },
            }
        except requests.Timeout:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            last_error = _error_result("LangSearch request timed out.", code="timeout")
            if attempt < max_retries:
                delay = _compute_retry_delay(attempt=attempt, retry_after_seconds=None, base_delay=base_delay, max_delay=max_delay)
                logger.warning(
                    "[langsearch] %s request timed out elapsed=%.1fms timeout=%ss attempt=%d/%d retry_in=%.2fs query=%r",
                    request_label,
                    elapsed_ms,
                    timeout_seconds,
                    attempt,
                    max_retries,
                    delay,
                    normalized_query,
                )
                time.sleep(delay)
                continue

            logger.error(
                "[langsearch] %s request timed out after retries elapsed=%.1fms timeout=%ss attempts=%d query=%r",
                request_label,
                elapsed_ms,
                timeout_seconds,
                attempt,
                normalized_query,
            )
            return last_error
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            preview = _preview_response_text(exc.response)
            logger.error(
                "[langsearch] %s request failed with non-retryable HTTP status=%s timeout=%ss attempt=%d/%d query=%r response=%s",
                request_label,
                status_code,
                timeout_seconds,
                attempt,
                max_retries,
                normalized_query,
                preview,
            )
            return _error_result(
                "LangSearch request failed.",
                code="http_error",
                status_code=status_code,
                response_preview=preview,
            )
        except requests.RequestException as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            last_error = _error_result(
                f"Network error during LangSearch request: {exc}",
                code="network_error",
            )
            if attempt < max_retries:
                delay = _compute_retry_delay(attempt=attempt, retry_after_seconds=None, base_delay=base_delay, max_delay=max_delay)
                logger.warning(
                    "[langsearch] %s request network failure type=%s elapsed=%.1fms timeout=%ss attempt=%d/%d retry_in=%.2fs query=%r",
                    request_label,
                    type(exc).__name__,
                    elapsed_ms,
                    timeout_seconds,
                    attempt,
                    max_retries,
                    delay,
                    normalized_query,
                )
                time.sleep(delay)
                continue

            logger.error(
                "[langsearch] %s request failed after retries type=%s elapsed=%.1fms timeout=%ss attempts=%d query=%r error=%s",
                request_label,
                type(exc).__name__,
                elapsed_ms,
                timeout_seconds,
                attempt,
                normalized_query,
                exc,
            )
            return last_error
        except ValueError:
            preview = _preview_response_text(response)
            logger.error(
                "[langsearch] %s request returned invalid JSON timeout=%ss attempt=%d/%d query=%r response=%s",
                request_label,
                timeout_seconds,
                attempt,
                max_retries,
                normalized_query,
                preview,
            )
            return _error_result(
                "Failed to decode LangSearch response.",
                code="invalid_json",
                response_preview=preview,
            )

    return last_error or _error_result("LangSearch request failed.", code="unknown_error")


def _wait_for_rate_limit_slot(min_interval_seconds: float) -> None:
    global _next_request_time_monotonic
    if min_interval_seconds <= 0:
        return

    while True:
        sleep_for = 0.0
        with _rate_limit_lock:
            now = time.monotonic()
            if now >= _next_request_time_monotonic:
                _next_request_time_monotonic = now + min_interval_seconds
                return
            sleep_for = _next_request_time_monotonic - now
        if sleep_for > 0:
            time.sleep(sleep_for)


def _compute_retry_delay(
    *,
    attempt: int,
    retry_after_seconds: float | None,
    base_delay: float,
    max_delay: float,
) -> float:
    if retry_after_seconds is not None and retry_after_seconds > 0:
        return min(retry_after_seconds, max_delay)

    exponential = base_delay * (2 ** (attempt - 1))
    jitter = random.uniform(0.0, max(0.25, base_delay * 0.25))
    return min(exponential + jitter, max_delay)


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed_seconds = float(value)
        if parsed_seconds >= 0:
            return parsed_seconds
    except (TypeError, ValueError):
        pass

    try:
        parsed_dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return max(0.0, parsed_dt.timestamp() - time.time())


def _preview_response_text(response: requests.Response | None) -> str:
    if response is None:
        return ""
    text = (response.text or "").strip().replace("\n", " ")
    return text[:400]


def _error_result(message: str, *, code: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {
            "message": message,
            "code": code,
        }
    }
    if extra:
        payload["error"].update(extra)
    return payload


def _coerce_int(value: str | None, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_float(value: str | None, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
