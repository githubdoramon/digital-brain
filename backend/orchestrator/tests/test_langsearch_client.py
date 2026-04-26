from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from langsearch_client import search_web


class TestSearchWeb:
    @patch.dict("os.environ", {"LANGSEARCH_API_KEY": ""}, clear=False)
    def test_returns_missing_credentials_error(self):
        result = search_web(
            query="latest AI news",
            count=3,
            freshness="oneDay",
            summary=True,
            timeout_seconds=10,
            request_label="test",
        )

        assert result["error"]["code"] == "missing_credentials"

    @patch("langsearch_client.time.sleep")
    @patch("langsearch_client.requests.post")
    @patch.dict(
        "os.environ",
        {
            "LANGSEARCH_API_KEY": "test-key",
            "LANGSEARCH_MAX_RETRIES": "3",
            "LANGSEARCH_MIN_INTERVAL_SECONDS": "0",
        },
        clear=False,
    )
    def test_retries_rate_limit_then_succeeds(self, mock_post, mock_sleep):
        rate_limited = MagicMock(status_code=429, headers={"Retry-After": "2"}, text="slow down")
        success = MagicMock(status_code=200, headers={}, text='{"ok":true}')
        success.json.return_value = {"data": {"webPages": {"value": []}}}
        success.raise_for_status = MagicMock()
        mock_post.side_effect = [rate_limited, success]

        result = search_web(
            query="latest AI news",
            count=3,
            freshness="oneDay",
            summary=True,
            timeout_seconds=10,
            request_label="test",
        )

        assert "error" not in result
        assert result["meta"]["attempt"] == 2
        assert mock_sleep.call_count == 1
        assert mock_sleep.call_args.args[0] == 2

    @patch("langsearch_client.time.sleep")
    @patch("langsearch_client.requests.post")
    @patch.dict(
        "os.environ",
        {
            "LANGSEARCH_API_KEY": "test-key",
            "LANGSEARCH_MAX_RETRIES": "2",
            "LANGSEARCH_MIN_INTERVAL_SECONDS": "0",
        },
        clear=False,
    )
    def test_returns_rate_limited_error_after_retries(self, mock_post, mock_sleep):
        first = MagicMock(status_code=429, headers={}, text="slow down")
        second = MagicMock(status_code=429, headers={}, text="slow down again")
        mock_post.side_effect = [first, second]

        result = search_web(
            query="latest AI news",
            count=3,
            freshness="oneDay",
            summary=True,
            timeout_seconds=10,
            request_label="test",
        )

        assert result["error"]["code"] == "rate_limited"
        assert result["error"]["status_code"] == 429
        assert mock_sleep.call_count == 1

    @patch("langsearch_client.time.sleep")
    @patch("langsearch_client.requests.post")
    @patch.dict(
        "os.environ",
        {
            "LANGSEARCH_API_KEY": "test-key",
            "LANGSEARCH_MAX_RETRIES": "2",
            "LANGSEARCH_MIN_INTERVAL_SECONDS": "0",
        },
        clear=False,
    )
    def test_retries_timeout_then_succeeds(self, mock_post, mock_sleep):
        success = MagicMock(status_code=200, headers={}, text='{"ok":true}')
        success.json.return_value = {"data": {"webPages": {"value": []}}}
        success.raise_for_status = MagicMock()
        mock_post.side_effect = [requests.Timeout(), success]

        result = search_web(
            query="latest AI news",
            count=3,
            freshness="oneDay",
            summary=True,
            timeout_seconds=10,
            request_label="test",
        )

        assert "error" not in result
        assert result["meta"]["attempt"] == 2
        assert mock_sleep.call_count == 1
