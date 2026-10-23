"""Verbatim citations and model-free progress under provider backpressure."""

from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

import intelligence as store
import intelligence_jobs as jobs
import intelligence_model as model
import llm_admission as gate
from learning_validation import LearningValidationError


def source(text="Mira returned the laptop.", **changes):
    return {
        "ref": "1:0",
        "source_id": 1,
        "part": 0,
        "kind": "message",
        "entity_id": "message-1",
        "text": text,
        "payload": {},
        "skip": False,
        **changes,
    }


@pytest.mark.parametrize("span_index", [None, 0])
@pytest.mark.parametrize(
    ("text", "quote"),
    [
        ("Mira calls it “the quiet laptop”.", "Mira calls it 'the quiet laptop'."),
        ("Mira’s laptop isn’t quiet.", "Mira's laptop isn't quiet."),
        ("Mira returned\n\n the\tlaptop.", "Mira returned the laptop."),
        ("Mira paid\u00a0500 credits.", "Mira paid 500 credits."),
    ],
)
def test_formatting_repairs_keep_verbatim_source_evidence(text, quote, span_index):
    row = source(text)
    result = {
        "findings": [{"evidence": [{"ref": row["ref"], "span_index": span_index, "quote": quote}]}]
    }
    model._check_reconciliation_citations(
        result, [{"ref": row["ref"], "text_spans": [text], "exact_quotes": []}]
    )
    expanded = model.expand_citations(result, [row])
    assert store.evidence_for(expanded["findings"][0], [row])[0]["quote"] == text


@pytest.mark.parametrize(
    "quote",
    [
        "Mira kept the laptop.",
        "Mira returned the laptops.",
        "mira returned the laptop.",
        "Mira returned laptop the.",
        "Mira returned the laptop!",
        "Mira returned the laptop for 500 credits.",
    ],
)
def test_semantic_or_non_formatting_changes_still_fail(quote):
    row = source()
    finding = {"evidence": [{"ref": row["ref"], "span_index": 0, "quote": quote}]}
    with pytest.raises(model.InvalidModelOutput):
        model.expand_citations({"findings": [finding]}, [row])
    with pytest.raises(LearningValidationError):
        store.evidence_for(finding, [row])


def test_normalization_cannot_cross_selected_span_or_supplied_source():
    row = source("x" * 1000 + "Mira’s quiet laptop.")
    result = {
        "findings": [
            {"evidence": [{"ref": row["ref"], "span_index": 0, "quote": "Mira's quiet laptop."}]}
        ]
    }
    with pytest.raises(model.InvalidModelOutput):
        model.expand_citations(result, [row])
    result["findings"][0]["evidence"][0]["span_index"] = None
    with pytest.raises(model.InvalidModelOutput):
        model._check_reconciliation_citations(
            result, [{"ref": row["ref"], "text_spans": ["Other evidence."], "exact_quotes": []}]
        )


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(gate, "_STATES", {})
    monkeypatch.setenv("LLM_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setattr(jobs, "_STOP", MagicMock(is_set=lambda: False))
    run = {
        "user_email": "learner@example.com",
        "run_id": "run",
        "cutoff": None,
        "knowledge_only": False,
        "reviewed": True,
        "lease_token": "lease",
    }
    monkeypatch.setattr(jobs, "claim_run", lambda owners: run)
    monkeypatch.setattr(jobs, "_heartbeat", lambda run: nullcontext())
    finish, apply = MagicMock(), MagicMock()
    monkeypatch.setattr(jobs, "finish_run", finish)
    monkeypatch.setattr(store, "apply_batch", apply)
    return run, finish, apply


@pytest.mark.parametrize("blocked", ["foreground", "background", "cooldown"])
def test_skipped_batches_drain_without_registering_for_llm_admission(worker, monkeypatch, blocked):
    run, finish, apply = worker
    state = gate._state("https://models.example.test/v1")
    if blocked == "foreground":
        state.foreground = 1
    elif blocked == "background":
        state.background = True
    else:
        state.cooldown_until = gate.time.monotonic() + 300
    batches = iter(
        [
            [source(skip=True)],
            [source(payload={"deleted": True})],
            [source(payload={"excluded": True})],
            [source(text=" \n ")],
            [],
            [],
        ]
    )
    monkeypatch.setattr(store, "next_batch", lambda *a, **kw: next(batches))
    analyze = MagicMock(side_effect=AssertionError("Skipped data must not invoke the model"))
    monkeypatch.setattr(model, "analyze", analyze)
    assert jobs.process_once([run["user_email"]])
    assert apply.call_count == 4
    finish.assert_called_once_with(run, more=False)
    analyze.assert_not_called()
    assert state.deferred == 0 and not state.waiting


def test_skip_drain_is_bounded_and_yields_the_lease(worker, monkeypatch):
    run, finish, apply = worker
    monkeypatch.setattr(store, "next_batch", lambda *a, **kw: [source(skip=True)])
    assert jobs.process_once([run["user_email"]])
    assert apply.call_count == jobs.MAX_SKIPPED_BATCHES_PER_TURN
    finish.assert_called_once_with(run, more=True)


def test_mixed_batch_waits_for_admission_without_receipting_pending_sources(worker, monkeypatch):
    run, finish, apply = worker
    endpoint = "https://models.example.test/v1"
    gate._state(endpoint).background = True
    skipped = [source(skip=True)]
    mixed = [source(skip=True), source(ref="2:0")]
    batches = iter([skipped, mixed])
    monkeypatch.setattr(store, "next_batch", lambda *a, **kw: next(batches))
    network = MagicMock()

    def analyze(*args, **kwargs):
        with gate.request_slot(endpoint):
            network()
        return {}, []

    monkeypatch.setattr(model, "analyze", analyze)
    connection = MagicMock()
    monkeypatch.setattr(jobs, "get_conn", connection)
    assert jobs.process_once([run["user_email"]])
    network.assert_not_called()
    assert apply.call_count == 1 and apply.call_args.args[2] == skipped
    finish.assert_not_called()
    cursor = (
        connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    )
    assert cursor.execute.call_args.args[1] == ("background_busy", 5, "run", "lease")


def test_skips_do_not_spend_the_model_batch_budget(worker, monkeypatch):
    run, finish, apply = worker
    batches = iter([[source(skip=True)], [source()], [source()]])
    monkeypatch.setattr(store, "next_batch", lambda *a, **kw: next(batches))
    analyze = MagicMock(return_value=({}, []))
    monkeypatch.setattr(model, "analyze", analyze)
    assert jobs.process_once([run["user_email"]])
    assert apply.call_count == 2 and analyze.call_count == 1
    finish.assert_called_once_with(run, more=True)
