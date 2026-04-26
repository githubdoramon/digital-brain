from __future__ import annotations

from unittest.mock import MagicMock, patch

import web_tools


class TestInternetSearch:
    @patch.dict("os.environ", {"LANGSEARCH_API_KEY": ""}, clear=False)
    def test_returns_error_without_api_key(self):
        result = web_tools.internet_search("latest AI news")

        assert result["error"]["code"] == "missing_credentials"

    @patch("web_tools.search_web")
    @patch.dict("os.environ", {"LANGSEARCH_API_KEY": "test-key", "LANGSEARCH_FRESHNESS": "oneWeek"}, clear=False)
    def test_normalizes_langsearch_results(self, mock_search_web):
        mock_search_web.return_value = {
            "data": {
                "log_id": "log-123",
                "data": {
                    "queryContext": {"originalQuery": "latest AI news"},
                    "webPages": {
                        "value": [
                            {
                                "name": "AI News",
                                "url": "https://example.com/ai",
                                "snippet": "Short snippet",
                                "summary": "Longer summary",
                                "datePublished": "2026-02-15T00:00:00Z",
                            }
                        ]
                    },
                },
            },
            "meta": {"attempt": 1, "status_code": 200},
        }

        result = web_tools.internet_search("latest AI news", max_results=3)

        assert result["provider"] == "langsearch"
        assert result["query"] == "latest AI news"
        assert result["results"][0]["title"] == "AI News"
        assert result["results"][0]["summary"] == "Longer summary"
        assert result["request_meta"]["status_code"] == 200
        assert mock_search_web.call_args.kwargs["freshness"] == "oneWeek"
        assert mock_search_web.call_args.kwargs["count"] == 3


class TestFetchWebPage:
    @patch("web_tools.requests.get")
    def test_extracts_html_content_links_and_images(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            url="https://example.com/story",
            text="""
                <html>
                  <head><title>Example Story</title></head>
                  <body>
                    <article>
                      <p>Hello world</p>
                      <a href="/more">Read more</a>
                      <img src="/hero.png" alt="Hero" />
                    </article>
                  </body>
                </html>
            """,
            headers={"content-type": "text/html; charset=utf-8"},
        )
        mock_get.return_value.raise_for_status = MagicMock()

        result = web_tools.fetch_web_page(
            "https://example.com/story",
            include_links=True,
            include_images=True,
        )

        document = result["documents"][0]
        assert result["provider"] == "direct_fetch"
        assert document["title"] == "Example Story"
        assert "Hello world" in document["content"]
        assert document["links"] == [{"url": "https://example.com/more", "text": "Read more"}]
        assert document["images"] == [{"url": "https://example.com/hero.png", "alt": "Hero"}]

    @patch("web_tools.requests.get")
    def test_returns_plain_text_document(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            url="https://example.com/raw.txt",
            text="plain text body",
            headers={"content-type": "text/plain"},
        )
        mock_get.return_value.raise_for_status = MagicMock()

        result = web_tools.fetch_web_page("https://example.com/raw.txt")

        assert result["documents"][0]["content"] == "plain text body"

    def test_rejects_invalid_url(self):
        result = web_tools.fetch_web_page("ftp://example.com/file.txt")

        assert result["error"]["code"] == "invalid_url"
