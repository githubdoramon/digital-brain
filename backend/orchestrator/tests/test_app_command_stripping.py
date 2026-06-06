"""Regression tests for slash-command stripping before agent execution."""

from __future__ import annotations

import importlib
import sys


def _load_app_module(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_CHAT_MODEL_FAST", "llama3.2:3b")
    monkeypatch.setenv("LLM_CHAT_MODEL_SMART", "llama3.3:70b")
    monkeypatch.setenv("GOOGLE_CLIENT_IDS", "test-client-id.apps.googleusercontent.com")
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


def test_new_command_resets_explicit_thread_session(monkeypatch):
    app_module = _load_app_module(monkeypatch)

    calls = []

    def fake_ensure_thread(thread_id, user_email, title=None):
        calls.append((thread_id, user_email, title))
        if thread_id is None:
            return {"id": "thread_new"}
        return {"id": thread_id}

    monkeypatch.setattr(app_module.conversations, "ensure_thread", fake_ensure_thread)

    payload = app_module.AskIn(
        question="/new when did I last meet Gio?",
        thread_id="thread_old",
    )
    ctx = app_module._resolve_session_context(payload, "user@example.com")

    assert calls[0][0] is None
    assert ctx.session_id == "thread_new"
    assert ctx.is_new_session is True
    assert ctx.is_reset_only is False
    assert ctx.question == "when did I last meet Gio?"


def test_new_command_only_marks_reset_only_for_explicit_thread(monkeypatch):
    app_module = _load_app_module(monkeypatch)

    monkeypatch.setattr(
        app_module.conversations,
        "ensure_thread",
        lambda thread_id, user_email, title=None: {"id": "thread_new" if thread_id is None else thread_id},
    )

    payload = app_module.AskIn(question="/new", thread_id="thread_old")
    ctx = app_module._resolve_session_context(payload, "user@example.com")

    assert ctx.session_id == "thread_new"
    assert ctx.is_new_session is True
    assert ctx.is_reset_only is True
    assert ctx.question == ""
