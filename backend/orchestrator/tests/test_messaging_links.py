import pytest

from messaging_links import source_link, trusted_url


@pytest.mark.parametrize(
    "provider,chat,message,metadata,expected",
    [
        ("whatsapp", "15550000001@s.whatsapp.net", "ABC", {}, "https://wa.me/15550000001"),
        ("whatsapp", "123456@g.us", "ABC", {}, None),
        ("whatsapp", "123456@lid", "ABC", {}, None),
        ("telegram", "1234@telegram.channel", "82", {}, "https://t.me/c/1234/82"),
        ("telegram", "1234@telegram.channel", "82", {"deleted": True}, None),
        (
            "telegram",
            "1234@telegram.user",
            "82",
            {"source_username": "sample_user"},
            "https://t.me/sample_user",
        ),
        (
            "telegram",
            "1234@telegram.user",
            "82",
            {"source_phone": "+15550000001"},
            "https://t.me/+15550000001",
        ),
        ("telegram", "1234@telegram.user", "82", {}, None),
        ("telegram", "1234@telegram.group", "82", {}, None),
        ("telegram", "1234@telegram.user", "82", {"source_username": "evil.example/path"}, None),
        ("telegram", "1234@telegram.channel", "82?evil=1", {}, None),
    ],
)
def test_only_supported_source_destinations(provider, chat, message, metadata, expected):
    result = source_link(provider, chat, message, **metadata)
    assert (result or {}).get("url") == expected
    if expected:
        assert trusted_url(result["url"])
        assert result["target"] == ("message" if "/c/" in expected else "conversation")


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://t.me.evil.example/user",
        "https://t.me@evil.example/user",
        "https://wa.me/15550000001?text=send",
        "https://t.me/share/url?url=evil",
        None,
    ],
)
def test_untrusted_urls_are_never_link_candidates(url):
    assert not trusted_url(url)
