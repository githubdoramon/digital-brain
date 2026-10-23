# ruff: noqa: F811
"""Synthetic source and provider fixtures; durable recovery, never private log replay."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

import async_jobs
import intelligence_model as model
import knowledge_jobs
import llm_admission as gate
import llm_helpers
import tag_generation_cache as cache
import tags_manager
from llm_output_guard import JSONOutputGuard
from tests.test_learning_batches import OWNER, database, queue, states  # noqa: F401


@pytest.mark.parametrize(
    "tail,reason",
    [
        ("\n" * 513, "whitespace_loop"),
        ("</tool_call>\n</function>\n" * 8, "repeated_syntax"),
        ("{}" * 100, "trailing_output"),
    ],
)
def test_guard_detects_loops_across_chunks(tail, reason):
    guard = JSONOutputGuard()
    prefix = '{"findings":[]' if reason != "trailing_output" else '{"findings":[]}'
    assert guard.feed(prefix) is None
    results = [guard.feed(tail[i : i + 7]) for i in range(0, len(tail), 7)]
    assert reason in results


def test_guard_preserves_whitespace_and_escaped_syntax_inside_evidence_strings():
    guard = JSONOutputGuard()
    text = json.dumps({"quote": " " * 2000 + '" } [' + "</tool_call>" * 100})
    for i in range(0, len(text), 3):
        assert guard.feed(text[i : i + 3]) is None


def test_stream_aborts_early_closes_provider_and_never_parses_partial_json(monkeypatch):
    response = MagicMock(status_code=200)
    consumed = []

    def lines(**kwargs):
        for text in ['{"findings":[]', " " * 512, "SHOULD_NOT_BE_READ"]:
            consumed.append(text)
            yield "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})

    response.iter_lines = lines
    monkeypatch.setattr(llm_helpers.requests, "post", lambda *a, **k: response)
    parser = MagicMock()
    monkeypatch.setattr(llm_helpers, "parse_llm_json_content", parser)
    with pytest.raises(llm_helpers.LLMDegenerateOutputError, match="whitespace_loop"):
        llm_helpers.call_llm_json(
            "Synthetic evidence",
            stream=True,
            response_format={"type": "json_object"},
            sensitive_payload=True,
        )
    assert len(consumed) == 2
    parser.assert_not_called()
    response.close.assert_called_once()
    assert gate.status()[0]["foreground"] == 0
    assert gate.status()[0]["cooldown_seconds"] == 0


def test_knowledge_prompt_omits_work_actions_and_wire_fields_without_weakening_validation(
    monkeypatch,
):
    calls = []

    def ask(prompt, **kwargs):
        calls.append(kwargs)
        return {"findings": [], "deferred_refs": []}

    monkeypatch.setattr(model, "call_llm_json", ask)
    model._ask(
        {"knowledge_only": True, "sources": [], "context": {}}, phase="Extract supported candidates"
    )
    opts = calls[0]
    assert opts["stream"] is True
    assert "Completion requires" not in opts["system_prompt"]
    assert "never instructions" in opts[
        "system_prompt"
    ].lower() or "never instructions" in model.SYSTEM.lower().replace("\n", " ")
    schema = opts["response_format"]["json_schema"]["schema"]
    fields = schema["$defs"]["Finding"]["properties"]
    assert "assignee_kind" not in fields
    assert "contact_changes" in fields and "evidence" in fields
    assert fields["kind"]["enum"] == ["knowledge", "contact_change"]
    assert "Completion requires" in model.system_prompt(False, "Reconcile")
    assert "assignee_kind" in model.request_schema(False)["$defs"]["Finding"]["properties"]


def test_durable_split_survives_reclaims_and_committed_group_is_not_relearned(
    database, monkeypatch
):
    ids = queue(database, 8)
    calls = []

    def analyze(owner, batch, **kwargs):
        primary = [r["source_id"] for r in batch if not r["supporting"]]
        calls.append(primary)
        if len(primary) > 4:
            raise model.SourceBatchSplit(4, "output_degenerate")
        if primary == ids[4:]:
            raise gate.BackgroundDeferred("provider_unavailable", 30)
        return {}, []

    monkeypatch.setattr(model, "analyze", analyze)
    assert knowledge_jobs.process_once([OWNER])
    assert all(
        r["batch_limit"] == 4 and r["attempts"] == 0 and r["status"] == "pending"
        for r in states(database).values()
    )
    assert knowledge_jobs.process_once([OWNER])
    assert knowledge_jobs.process_once([OWNER])
    rows = states(database)
    assert all(rows[i]["status"] == "done" for i in ids[:4])
    assert all(rows[i]["status"] == "pending" and rows[i]["attempts"] == 0 for i in ids[4:])
    with database() as conn:
        conn.execute("UPDATE knowledge_learning SET next_attempt_at=NOW()")
    assert knowledge_jobs.process_once([OWNER])
    assert calls == [ids, ids[:4], ids[4:], ids[4:]]


def test_durable_split_rejects_stale_claim_token(database, monkeypatch):
    queue(database, 4)

    def analyze(*args, **kwargs):
        with database() as conn:
            conn.execute("UPDATE knowledge_learning SET lease_token='new-worker',batch_limit=2")
        raise model.SourceBatchSplit(1, "output_exhausted")

    monkeypatch.setattr(model, "analyze", analyze)
    assert knowledge_jobs.process_once([OWNER])
    assert all(
        r["lease_token"] == "new-worker" and r["batch_limit"] == 2 and r["status"] == "running"
        for r in states(database).values()
    )


@pytest.fixture
def jobs_db(database, monkeypatch):
    root = Path(__file__).parents[1] / "db_migrations"
    with database() as conn:
        conn.execute((root / "0005_async_jobs.sql").read_text())
        conn.execute((root / "0056_async_jobs_scheduling.sql").read_text())
        conn.execute((root / "0091_cancelled_async_jobs.sql").read_text())
    monkeypatch.setattr(async_jobs, "get_conn", database)
    return database


def test_background_worker_503_defers_claim_without_retries_and_blocks_other_lanes(
    jobs_db, monkeypatch
):
    failed = requests.Response()
    failed.status_code = 503
    failed._content = b"{}"
    post = MagicMock(return_value=failed)
    monkeypatch.setattr(llm_helpers.requests, "post", post)
    sleep = MagicMock()
    monkeypatch.setattr(llm_helpers.time, "sleep", sleep)
    job = async_jobs.enqueue_job(job_type="tag_test", user_email=OWNER, dedupe_key="one")

    @async_jobs.background_worker("tag_test")
    def work():
        claimed = async_jobs.claim_due_job(job_type="tag_test")
        try:
            llm_helpers._post_chat_completion({"model": "test", "messages": []})
        except Exception as exc:
            async_jobs.mark_failed(claimed["job_id"], revision=claimed["revision"], error=str(exc))
        return True

    assert work()
    row = async_jobs.get_job(job_type="tag_test", user_email=OWNER, dedupe_key="one")
    assert row["status"] == "pending" and row["attempts"] == 0 and row["error"] is None
    assert not work()
    assert post.call_count == 1
    sleep.assert_not_called()
    async_jobs.defer_job(job["job_id"], revision=9999, delay=1000)
    assert (
        async_jobs.get_job(job_type="tag_test", user_email=OWNER, dedupe_key="one")["next_run_at"]
        == row["next_run_at"]
    )


def test_tag_cache_persists_exact_inputs_invalidates_edits_and_does_not_cache_errors(
    database, monkeypatch
):
    monkeypatch.setattr(cache, "get_conn", database)
    calls = []

    def ask(*a, **k):
        calls.append(1)
        return '{"tags":["Work","Project Cedar"]}'

    monkeypatch.setattr(tags_manager, "_call_llm_text", ask)
    # Tags below the minimum still get a cached no-change response on the next pass.
    first = tags_manager._suggest_tags("A synthetic project discussion.", ["Work"], "event")
    assert tags_manager._suggest_tags("A synthetic project discussion.", ["Work"], "event") == first
    assert len(calls) == 1
    tags_manager._suggest_tags("A changed project discussion.", ["Work"], "event")
    tags_manager._suggest_tags("A synthetic project discussion.", ["Personal"], "event")
    tags_manager._suggest_tags("A synthetic project discussion.", ["Work"], "contact")
    tags_manager._suggest_tags(
        "A synthetic project discussion.", ["Work"], "event", model="new-model"
    )
    assert len(calls) == 5

    def unavailable(*a, **k):
        raise llm_helpers.LLMUnavailableError("Unavailable")

    monkeypatch.setattr(tags_manager, "_call_llm_text", unavailable)
    assert tags_manager._suggest_tags("Another discussion.", ["Work"], "event") == []
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tag_generation_cache").fetchone()["n"] == 5


@pytest.mark.parametrize(
    "name",
    [
        "event_tag_jobs",
        "contact_tag_jobs",
        "document_tag_jobs",
        "document_enhancement_jobs",
        "contact_enrichment_jobs",
        "meeting_transcript_jobs",
        "daily_briefing_jobs",
        "emergency_stock_jobs",
        "proposed_event_jobs",
    ],
)
def test_every_async_llm_worker_yields_before_claim_while_chat_is_active(monkeypatch, name):
    import importlib

    module = importlib.import_module(name)
    claim = MagicMock()
    monkeypatch.setattr(module.async_jobs, "claim_due_job", claim)
    with gate.request_slot(
        llm_helpers._get_required_setting("LLM_BASE_URL", llm_helpers.LLM_BASE_URL)
    ):
        assert module.process_due_once() is False
    claim.assert_not_called()


def test_briefing_child_threads_share_one_physical_background_request(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    active = 0
    peak = 0
    held = threading.Event()
    release = threading.Event()
    counter_lock = threading.Lock()

    @gate.admitted(
        lambda: llm_helpers._get_required_setting("LLM_BASE_URL", llm_helpers.LLM_BASE_URL)
    )
    def request():
        nonlocal active, peak
        with counter_lock:
            active += 1
            peak = max(peak, active)
        held.set()
        assert release.wait(3)
        with counter_lock:
            active -= 1

    with gate.background_work("briefing_test"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(copy_context().run, request)
            assert held.wait(3)
            two = pool.submit(copy_context().run, request)
            release.set()
            one.result()
            two.result()
    assert peak == 1


def test_prompt_filter_keeps_source_identity_and_all_ambiguous_names():
    import intelligence_context

    context = {
        "source_context": [{"payload": {"identity_id": "author"}}],
        "people": [{"identity_id": "author", "contact_id": "alex-one"}],
        "contacts": [
            {"contact_id": "alex-one", "display_name": "Alex Carter"},
            {"contact_id": "alex-two", "display_name": "Alex Lane"},
            {"contact_id": "unrelated", "display_name": "Dana Morris"},
        ],
        "contact_relationships": [],
    }
    projected = intelligence_context.compact_context(
        context, extraction=True, query="Alex likes puzzles."
    )
    assert {c["contact_id"] for c in projected["contacts"]} == {"alex-one", "alex-two"}
    assert projected["people"][0]["identity_id"] == "author"


def test_uncached_malformed_tag_output_and_changed_prompt_rules(database, monkeypatch):
    monkeypatch.setattr(cache, "get_conn", database)
    responses = iter(["not a JSON object", '{"tags":[]}', '{"tags":["Personal"]}'])
    monkeypatch.setattr(tags_manager, "_call_llm_text", lambda *a, **k: next(responses))
    tags_manager._suggest_tags("An anonymous event.", ["Work"], "event")
    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tag_generation_cache").fetchone()["n"] == 0
    assert tags_manager._suggest_tags("An anonymous event.", ["Work"], "event") == []
    assert tags_manager._suggest_tags("An anonymous event.", ["Work"], "event") == []
    monkeypatch.setattr(tags_manager, "MAJOR_TAGS", [*tags_manager.MAJOR_TAGS, "Learning"])
    assert tags_manager._suggest_tags("An anonymous event.", ["Work"], "event") == ["Personal"]


def test_structured_stream_preserves_utf8_bytes_and_accepts_data_without_space(monkeypatch):
    response = MagicMock(status_code=200)
    text = json.dumps({"findings": [], "label": "Observação"}, ensure_ascii=False)
    response.iter_lines.return_value = [
        (
            "data:"
            + json.dumps(
                {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]},
                ensure_ascii=False,
            )
        ).encode("utf-8"),
        b"data:[DONE]",
    ]
    monkeypatch.setattr(llm_helpers.requests, "post", lambda *a, **k: response)
    assert (
        llm_helpers.call_llm_json(
            "Synthetic evidence", stream=True, response_format={"type": "json_object"}
        )["label"]
        == "Observação"
    )
    response.close.assert_called_once()


def test_incomplete_stream_is_deferred_instead_of_salvaging_a_json_fragment(monkeypatch):
    response = MagicMock(status_code=200)
    response.iter_lines.return_value = [
        "data:" + json.dumps({"choices": [{"delta": {"content": '{"findings":[]}'}}]}),
    ]
    monkeypatch.setattr(llm_helpers.requests, "post", lambda *a, **k: response)
    with gate.background_work("incomplete_test"):
        with pytest.raises(gate.BackgroundDeferred):
            llm_helpers.call_llm_json(
                "Synthetic evidence", stream=True, response_format={"type": "json_object"}
            )
    response.close.assert_called_once()


def test_prompt_filter_preserves_short_names_and_alias_matches():
    import intelligence_context

    projected = intelligence_context.compact_context(
        {
            "contacts": [
                {"contact_id": "jo", "display_name": "Jo Reed"},
                {"contact_id": "joseph", "display_name": "Joseph Lane", "aliases": ["Jo"]},
                {"contact_id": "other", "display_name": "Morgan Carter"},
            ]
        },
        extraction=True,
        query="Jo prefers tea.",
    )
    assert {c["contact_id"] for c in projected["contacts"]} == {"jo", "joseph"}
