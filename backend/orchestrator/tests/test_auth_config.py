from __future__ import annotations

import importlib
import sys

import pytest


def _reload_auth_module():
    sys.modules.pop("auth", None)
    import auth

    return importlib.reload(auth)


def test_auth_requires_non_empty_allowlist(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("DEV_BYPASS_AUTH", raising=False)
    monkeypatch.delenv("DEV_USER_EMAIL", raising=False)
    monkeypatch.delenv("ALLOWED_USERS", raising=False)

    with pytest.raises(ValueError, match="ALLOWED_USERS must be configured and non-empty"):
        _reload_auth_module()


def test_auth_allows_dev_bypass_without_allowlist(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("DEV_BYPASS_AUTH", "true")
    monkeypatch.setenv("DEV_USER_EMAIL", "dev@example.com")
    monkeypatch.delenv("ALLOWED_USERS", raising=False)

    auth_module = _reload_auth_module()

    assert auth_module.DEV_BYPASS_AUTH is True
    assert set() == auth_module.ALLOWED_USERS
