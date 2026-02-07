"""Regression tests for slash-command stripping before agent execution."""

from __future__ import annotations

import importlib
import sys


def _load_app_module(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL", "llama3.3:70b")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    if "app" in sys.modules:
        return sys.modules["app"]
    return importlib.import_module("app")


def test_strip_command_prefix_handles_any_slash_command(monkeypatch):
    app_module = _load_app_module(monkeypatch)

    assert app_module._strip_command_prefix("/new when did I last meet Gio?") == (
        "when did I last meet Gio?"
    )
    assert app_module._strip_command_prefix("/foo check this context") == "check this context"


def test_strip_command_prefix_keeps_plain_text(monkeypatch):
    app_module = _load_app_module(monkeypatch)

    assert app_module._strip_command_prefix("when did I last meet Gio?") == (
        "when did I last meet Gio?"
    )
