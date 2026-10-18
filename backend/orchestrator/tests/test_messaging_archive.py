"""Contract tests use real WhatsApp protobufs and optional disposable PostgreSQL."""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row

import messaging_archive as archive
from auth import get_current_user
from messaging_normalization import (
    Origin,
    history_records,
    live_record,
    normalize_message,
    stable_id,
)
from routes.messaging import create_messaging_router

OWNER = "reader@example.com"
OTHER = "other@example.com"


def record(
    *,
    kind="message",
    body="The sample quote is ready",
    sender="15550000001@s.whatsapp.net",
    chat="15550000001@s.whatsapp.net",
    time="2026-01-01T12:00:00+00:00",
    origin="live",
    id="ABC",
):
    return {
        "kind": kind,
        "origin": origin,
        "chat": chat,
        "sender": sender,
        "id": id,
        "sent_at": time,
        "effective_at": time,
        "outgoing": False,
        "name": "Sample Contact",
        "title": "",
        "self_jid": "15550000000@s.whatsapp.net",
        "body": body,
        "attachments": [],
        "reply_id": None,
    }


@pytest.fixture
def proto():
    module = pytest.importorskip("tryx.waproto.whatsapp_pb2")
    return module


def test_media_projection_never_contains_attachment_bytes_or_download_keys(proto):
    message = proto.Message()
    message.documentMessage.fileName = "sample-contract.pdf"
    message.documentMessage.caption = "Signed contract"
    message.documentMessage.fileLength = 125
    message.documentMessage.mediaKey = b"secret-key"
    message.documentMessage.jpegThumbnail = b"private-thumbnail"
    message.documentMessage.url = "https://example.com/private-download"
    item = normalize_message(
        proto=message,
        chat="15550000001@s.whatsapp.net",
        sender="15550000001@s.whatsapp.net",
        message_id="ABC",
        sent_at=1700000000,
        outgoing=False,
        origin=Origin.LIVE,
    )
    encoded = json.dumps(item)
    assert "secret" not in encoded and "private" not in encoded and "https" not in encoded
    assert item["body"] == "Signed contract"
    assert item["attachments"] == [
        {"type": "document", "filename": "sample-contract.pdf", "size": 125}
    ]


def test_history_self_group_and_live_share_keys(proto):
    history = proto.HistorySync()
    conversation = history.conversations.add(id="100@g.us", name="Sample group")
    message = conversation.messages.add().message
    message.key.id = "ABC"
    message.key.fromMe = True
    message.messageTimestamp = 1700000000
    message.message.conversation = "Project update"
    own = "15550000000@s.whatsapp.net"
    historical = next(history_records(history, own))
    event = SimpleNamespace(
        data=SimpleNamespace(
            raw_proto=message.message,
            message_info=SimpleNamespace(
                source=SimpleNamespace(chat="100@g.us", sender=own, is_from_me=True),
                id="ABC",
                timestamp=datetime.fromtimestamp(1700000000, timezone.utc),
                push_name="",
            ),
        )
    )
    live = live_record(event, own)
    assert (historical["chat"], historical["sender"], historical["id"]) == (
        live["chat"],
        live["sender"],
        live["id"],
    )
    assert historical["body"] == live["body"]


def test_edit_and_revoke_are_typed_and_attributed_to_original_sender(proto):
    message = proto.Message()
    message.protocolMessage.type = 14
    message.protocolMessage.key.id = "ORIGINAL"
    message.protocolMessage.key.participant = "15550000002@s.whatsapp.net"
    message.protocolMessage.editedMessage.conversation = "Revised quote"
    item = normalize_message(
        proto=message,
        chat="100@g.us",
        sender="15550000001@s.whatsapp.net",
        message_id="EDIT",
        sent_at=1700000000,
        outgoing=False,
        origin=Origin.LIVE,
    )
    assert item["kind"] == "edit" and item["id"] == "ORIGINAL"
    assert item["sender"] == "15550000002@s.whatsapp.net"
    message.protocolMessage.type = 0
    item = normalize_message(
        proto=message,
        chat="100@g.us",
        sender="15550000001@s.whatsapp.net",
        message_id="DELETE",
        sent_at=1700000000,
        outgoing=False,
        origin=Origin.LIVE,
    )
    assert item["kind"] == "revoke" and item["body"] == ""


def test_unattributed_group_revocation_is_not_applied_to_an_arbitrary_sender(proto):
    message = proto.Message()
    message.protocolMessage.type = 0
    message.protocolMessage.key.id = "ORIGINAL"
    with pytest.raises(ValueError, match="unresolved_mutation_sender"):
        normalize_message(
            proto=message,
            chat="100@g.us",
            sender="15550000001@s.whatsapp.net",
            message_id="DELETE",
            sent_at=1700000000,
            outgoing=False,
            origin=Origin.LIVE,
        )


@pytest.fixture
def database(monkeypatch):
    dsn = os.getenv("MESSAGING_TEST_DSN")
    if not dsn:
        pytest.skip("MESSAGING_TEST_DSN must point to a disposable PostgreSQL instance")
    schema = "messaging_test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    @contextmanager
    def connect():
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            yield conn

    with connect() as conn:
        conn.execute(
            "CREATE TABLE contacts(contact_id TEXT PRIMARY KEY, display_name TEXT, phones TEXT[])"
        )
        conn.execute(
            "INSERT INTO contacts VALUES ('sample-contact', 'Sample Contact', ARRAY['+15550000001'])"
        )
        migration = Path(__file__).resolve().parents[1] / "db_migrations/0073_messaging_archive.sql"
        conn.execute(migration.read_text())
        conn.execute(migration.with_name("0074_telegram_archive.sql").read_text())
    monkeypatch.setattr(archive, "get_conn", connect)
    import telegram_sync

    monkeypatch.setattr(telegram_sync, "get_conn", connect)
    yield connect
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def ingest(owner=OWNER, **changes):
    account = archive.account(owner)
    if not account.get("account_id"):
        account = archive.set_connection(owner, True)
    archive.stage_records(account["account_id"], account["generation"], [record(**changes)])
    archive.drain_once()
    return account


def test_live_history_overlap_and_replay_are_idempotent(database):
    ingest()
    ingest(origin="history")
    ingest()
    assert len(archive.search(OWNER, "quote")) == 1
    with database() as conn:
        assert conn.execute("SELECT count(*) FROM messaging_revisions").fetchone()["count"] == 1
        assert (
            conn.execute(
                "SELECT count(*) FROM messaging_inbox WHERE processed_at IS NULL"
            ).fetchone()["count"]
            == 0
        )


def test_same_external_id_across_chat_and_sender_is_not_merged(database):
    ingest(chat="100@g.us")
    ingest(chat="100@g.us", sender="15550000002@s.whatsapp.net")
    ingest(chat="200@g.us")
    assert len(archive.search(OWNER)) == 3


def test_owner_scope_in_search_detail_identity_and_classification(database):
    ingest()
    foreign = archive.set_connection(OTHER, True)
    hit = archive.search(OWNER)[0]
    assert archive.search(OTHER) == []
    assert archive.identities(OTHER) == []
    with pytest.raises(LookupError):
        archive.message_context(OTHER, hit["message_id"])
    identity = archive.identities(OWNER)[0]
    with pytest.raises(LookupError):
        archive.link_identity(OTHER, identity["identity_id"], "sample-contact")
    with pytest.raises(LookupError):
        archive.set_context(OTHER, archive.Context.WORK, hit["conversation_id"])
    assert foreign["account_id"] != archive.account(OWNER)["account_id"]


def test_late_history_does_not_undo_edit_and_deletion_retains_evidence(database):
    ingest(kind="edit", body="Revised amount", time="2026-01-02T12:00:00+00:00")
    ingest(origin="history")
    assert archive.search(OWNER)[0]["body"] == "Revised amount"
    ingest(kind="revoke", body="", time="2026-01-03T12:00:00+00:00")
    ingest(origin="history", body="Late stale copy", time="2026-01-04T12:00:00+00:00")
    assert archive.search(OWNER)[0]["deleted"] is True
    with database() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM messaging_revisions WHERE body = 'Revised amount'"
            ).fetchone()["count"]
            == 1
        )


def test_sender_contact_links_are_correctable_and_searchable(database):
    ingest()
    identity = archive.identities(OWNER)[0]
    archive.link_identity(OWNER, identity["identity_id"], "sample-contact")
    assert len(archive.search(OWNER, contact_ids=["sample-contact"])) == 1
    archive.link_identity(OWNER, identity["identity_id"], None)
    assert archive.search(OWNER, contact_ids=["sample-contact"]) == []
    with database() as conn:
        assert (
            conn.execute("SELECT count(*) FROM messaging_identity_links").fetchone()["count"] == 2
        )


def test_context_inheritance_and_explicit_conversation_override(database):
    ingest()
    conversation = archive.conversations(OWNER)[0]
    archive.set_context(OWNER, archive.Context.WORK)
    assert archive.search(OWNER)[0]["context"] == "work"
    archive.set_context(OWNER, archive.Context.PERSONAL, conversation["conversation_id"])
    archive.set_context(OWNER, archive.Context.MIXED)
    assert archive.search(OWNER)[0]["context"] == "personal"


def test_disconnect_invalidates_late_callbacks_but_keeps_accepted_queue(database):
    account = archive.set_connection(OWNER, True)
    archive.stage_records(account["account_id"], account["generation"], [record()])
    archive.set_connection(OWNER, False)
    assert (
        archive.stage_records(account["account_id"], account["generation"], [record(id="LATE")])
        == 0
    )
    archive.drain_once()
    assert len(archive.search(OWNER)) == 1


def test_context_is_centered_on_old_match_not_latest_messages(database):
    for day in range(1, 19):
        ingest(id=str(day), time=f"2026-01-{day:02d}T12:00:00+00:00")
    hit_id = stable_id(
        archive.account(OWNER)["account_id"],
        "15550000001@s.whatsapp.net",
        "15550000001@s.whatsapp.net",
        "7",
    )
    result = archive.message_context(OWNER, hit_id)
    assert len(result["messages"]) == 11
    assert hit_id in {row["message_id"] for row in result["messages"]}
    assert max(row["sent_at"].day for row in result["messages"]) == 12


def test_api_requires_auth_and_no_store_pairing(database, monkeypatch):
    app = FastAPI()
    app.include_router(create_messaging_router())
    client = TestClient(app)
    assert client.get("/messaging/status").status_code == 401
    app.dependency_overrides[get_current_user] = lambda: {"email": OWNER}
    response = client.get("/messaging/pairing")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"svg": None}
    assert client.put("/messaging/context", json={"context": "invalid"}).status_code == 422


def test_missing_owner_never_falls_back_to_global_data(database):
    ingest()
    from tools.handlers.memory import handle_get_message

    assert handle_get_message({"message_id": archive.search(OWNER)[0]["message_id"]}) == {
        "conversation": None
    }


def test_bad_history_item_does_not_discard_the_remaining_exchange(proto):
    history = proto.HistorySync()
    conversation = history.conversations.add(id="15550000001@s.whatsapp.net")
    bad = conversation.messages.add().message
    bad.key.id = "BAD"
    bad.message.conversation = "Missing date"
    good = conversation.messages.add().message
    good.key.id = "GOOD"
    good.messageTimestamp = 1700000000
    good.message.conversation = "Keep this message"
    errors = []
    rows = list(history_records(history, "", on_error=lambda: errors.append(True)))
    assert [row["id"] for row in rows] == ["GOOD"]
    assert len(errors) == 1


def test_corrected_poison_item_can_retry_without_rolling_back_peers(database):
    account = archive.set_connection(OWNER, True)
    archive.stage_records(
        account["account_id"], account["generation"], [record(id="BAD"), record(id="GOOD")]
    )
    with database() as conn:
        conn.execute("UPDATE messaging_inbox SET payload = '{}' WHERE payload->>'id' = 'BAD'")
    archive.drain_once()
    assert len(archive.search(OWNER)) == 1
    with database() as conn:
        row = conn.execute(
            "SELECT attempts FROM messaging_inbox WHERE processed_at IS NULL"
        ).fetchone()
        assert row["attempts"] == 1
        conn.execute(
            "UPDATE messaging_inbox SET payload = %s WHERE processed_at IS NULL",
            (psycopg.types.json.Jsonb(record(id="BAD")),),
        )
    archive.retry_pending(OWNER)
    archive.drain_once()
    assert len(archive.search(OWNER)) == 2


def test_identity_suggestions_require_matching_identifier_and_confirmation(database):
    ingest()
    identity = archive.identities(OWNER)[0]
    assert identity["contact_id"] is None
    assert identity["suggested_contacts"][0]["contact_id"] == "sample-contact"


def test_pairing_qr_is_owner_scoped_and_never_cached(database, monkeypatch):
    import whatsapp_runtime

    account = archive.set_connection(OWNER, True)
    monkeypatch.setattr(
        whatsapp_runtime,
        "pairing_qr",
        lambda key: "sample-qr-payload" if key == account["account_id"] else None,
    )
    app = FastAPI()
    app.include_router(create_messaging_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": OWNER}
    client = TestClient(app)
    response = client.get("/messaging/pairing")
    assert response.status_code == 200
    assert "<svg" in response.json()["svg"]
    assert response.headers["cache-control"] == "no-store"
    app.dependency_overrides[get_current_user] = lambda: {"email": OTHER}
    assert client.get("/messaging/pairing").json() == {"svg": None}


def test_stopping_native_host_terminates_and_reaps_it():
    import multiprocessing
    import time

    from whatsapp_runtime import Session, _stop_session

    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=time.sleep, args=(60,), daemon=True)
    process.start()
    pid = process.pid
    child.close()
    _stop_session(Session(process, parent, 0, time.monotonic()))
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


def test_memory_tools_search_and_inspect_the_same_owner_scoped_message(database, monkeypatch):
    import sys

    from agent.state import AgentState
    from tools.handlers.memory import handle_get_message, handle_search_memories

    ingest()
    monkeypatch.setitem(sys.modules, "retrieval", SimpleNamespace())
    result = handle_search_memories({"sources": ["message"], "query": "quote"}, user_email=OWNER)
    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert hit["kind"] == "message" and hit["sender_name"] == "Sample Contact"
    state = AgentState(goal="Find the quote")
    detail = handle_get_message({"message_id": hit["message_id"]}, state=state, user_email=OWNER)
    assert detail["conversation"]["message_id"] == hit["message_id"]
    assert detail["conversation"]["messages"][0]["body"] == hit["snippet"]
    assert state.information_candidates[0]["inspected"] is True


def test_message_evidence_requires_inspecting_the_matched_message():
    from agent.state import ToolCallRecord
    from tools.validators.post_execution import GoalCompletionValidator

    validator = GoalCompletionValidator()
    search = ToolCallRecord(
        "search_memories",
        {"query": "quote"},
        {
            "results": [
                {"id": "match", "kind": "message", "title": "Sample group", "score": 1.0},
            ]
        },
        1,
        True,
    )
    args = {
        "goal": "Find the quote",
        "known_facts": ["Found a matching message"],
        "final_content": "The quote is ready.",
    }
    before = validator._check_query_goal_achieved(tool_calls=[search], **args)
    assert before[0] is False and "get_message" in before[2][0]
    wrong = ToolCallRecord(
        "get_message", {}, {"conversation": {"message_id": "other", "messages": [{}]}}, 1, True
    )
    assert validator._check_query_goal_achieved(tool_calls=[search, wrong], **args)[0] is False
    right = ToolCallRecord(
        "get_message", {}, {"conversation": {"message_id": "match", "messages": [{}]}}, 1, True
    )
    assert validator._check_query_goal_achieved(tool_calls=[search, right], **args)[0] is True


def test_chronological_pages_do_not_lose_equal_timestamp_messages(database):
    for index in range(7):
        ingest(id=f"MESSAGE-{index}")
    conversation_id = archive.conversations(OWNER)[0]["conversation_id"]
    first = archive.search(OWNER, conversation_id=conversation_id, limit=3)
    second = archive.search(
        OWNER, conversation_id=conversation_id, limit=3, before_id=first[-1]["message_id"]
    )
    third = archive.search(
        OWNER, conversation_id=conversation_id, limit=3, before_id=second[-1]["message_id"]
    )
    ids = [row["message_id"] for row in first + second + third]
    assert len(ids) == len(set(ids)) == 7


def test_repairing_another_phone_cannot_silently_merge_accounts(database):
    account = ingest()
    other = record(id="ANOTHER-ACCOUNT")
    other["self_jid"] = "15550000009@s.whatsapp.net"
    with pytest.raises(ValueError, match="account_mismatch"):
        archive.stage_records(account["account_id"], account["generation"], [other])
    assert len(archive.search(OWNER)) == 1


def test_contact_scoped_direct_chat_includes_messages_sent_to_that_person(database):
    account = ingest()
    sent = record(id="OUTGOING")
    sent.update(sender=sent["self_jid"], outgoing=True)
    archive.stage_records(account["account_id"], account["generation"], [sent])
    archive.drain_once()
    peer = next(i for i in archive.identities(OWNER) if not i["is_self"])
    archive.link_identity(OWNER, peer["identity_id"], "sample-contact")
    assert len(archive.search(OWNER, contact_ids=["sample-contact"])) == 2


def test_missing_protocol_type_is_not_interpreted_as_revocation(proto):
    message = proto.Message()
    message.protocolMessage.key.id = "ORIGINAL"
    assert (
        normalize_message(
            proto=message,
            chat="15550000001@s.whatsapp.net",
            sender="15550000001@s.whatsapp.net",
            message_id="CONTROL",
            sent_at=1700000000,
            outgoing=False,
            origin=Origin.LIVE,
        )
        is None
    )


def test_web_management_routes_preserve_links_context_and_owner_boundary(database):
    ingest()
    app = FastAPI()
    app.include_router(create_messaging_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": OWNER}
    client = TestClient(app)
    identity = client.get("/messaging/identities").json()["identities"][0]
    conversation = client.get("/messaging/conversations").json()["conversations"][0]
    link_path = f"/messaging/identities/{identity['identity_id']}/contact"
    assert client.put(link_path, json={"contact_id": "sample-contact"}).status_code == 200
    linked = client.get("/messaging/identities?contact_id=sample-contact").json()["identities"]
    assert linked[0]["contact_name"] == "Sample Contact"
    assert client.put("/messaging/context", json={"context": "personal"}).status_code == 200
    assert (
        client.put(
            "/messaging/context",
            json={"context": "work", "conversation_id": conversation["conversation_id"]},
        ).status_code
        == 200
    )
    result = client.get("/messaging/messages?q=quote").json()["messages"][0]
    assert result["context"] == "work"
    assert client.get(f"/messaging/messages/{result['message_id']}").status_code == 200
    app.dependency_overrides[get_current_user] = lambda: {"email": OTHER}
    assert client.get("/messaging/identities").json() == {"identities": []}
    assert client.put(link_path, json={"contact_id": None}).status_code == 404
    assert client.get(f"/messaging/messages/{result['message_id']}").status_code == 404
    app.dependency_overrides[get_current_user] = lambda: {"email": OWNER}
    assert client.put(link_path, json={"contact_id": None}).status_code == 200
    assert client.get("/messaging/identities?contact_id=sample-contact").json() == {
        "identities": []
    }


@pytest.fixture
def telegram_types():
    return pytest.importorskip("telethon.tl.types")


def telegram_record(
    types, *, id=7, user=12, chat=None, body="Telegram project update", edited=False
):
    from telegram_normalization import message_record

    message = types.Message(
        id=id,
        peer_id=chat or types.PeerUser(user),
        from_id=types.PeerUser(user),
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        message=body,
        edit_date=datetime(2026, 1, 2, tzinfo=timezone.utc) if edited else None,
    )
    entity = types.User(id=user, first_name="Telegram Person")
    return message_record(message, 99, entity, entity)


def telegram_ingest(types, **kwargs):
    from messaging_normalization import Provider

    account = archive.account(OWNER, Provider.TELEGRAM)
    if not account.get("account_id"):
        account = archive.set_connection(OWNER, True, Provider.TELEGRAM)
    archive.stage_records(
        account["account_id"], account["generation"], [telegram_record(types, **kwargs)]
    )
    archive.drain_once()
    return account


def test_telegram_official_projection_excludes_media_access_credentials(telegram_types):
    from telegram_normalization import message_record

    t = telegram_types
    media = t.MessageMediaDocument(
        document=t.Document(
            id=1,
            access_hash=123456789,
            file_reference=b"private-reference",
            date=datetime.now(timezone.utc),
            mime_type="application/pdf",
            size=123,
            dc_id=2,
            attributes=[t.DocumentAttributeFilename("plan.pdf")],
        )
    )
    message = t.Message(
        id=1,
        peer_id=t.PeerUser(12),
        from_id=t.PeerUser(12),
        date=datetime.now(timezone.utc),
        message="Project plan",
        media=media,
    )
    record = message_record(message, 99, t.User(id=12, first_name="Person"))
    assert record["attachments"] == [{"type": "document", "filename": "plan.pdf", "size": 123}]
    assert "private-reference" not in json.dumps(record)
    assert "123456789" not in json.dumps(record)
    media.ttl_seconds = 30
    assert message_record(message, 99, t.User(id=12)) is None


def test_telegram_and_whatsapp_isolate_management_and_share_ai_retrieval(database, telegram_types):
    from messaging_normalization import Provider
    from tools.handlers.memory import handle_get_message, handle_search_memories

    ingest(body="Project update on WhatsApp")
    telegram_ingest(telegram_types)
    whatsapp = archive.account(OWNER)
    telegram = archive.account(OWNER, Provider.TELEGRAM)
    assert whatsapp["account_id"] != telegram["account_id"]
    hit = archive.search(OWNER, "project", provider=Provider.TELEGRAM)[0]
    assert hit["provider"] == "telegram"
    assert {row["provider"] for row in archive.search(OWNER, "project")} == {"whatsapp", "telegram"}
    identities = archive.identities(OWNER, provider=Provider.TELEGRAM)
    identity = next(i for i in identities if i["external_id"] == "12@telegram.user")
    with pytest.raises(LookupError):
        archive.link_identity(OWNER, identity["identity_id"], "sample-contact")
    archive.link_identity(OWNER, identity["identity_id"], "sample-contact", Provider.TELEGRAM)
    archive.set_context(OWNER, archive.Context.WORK, provider=Provider.TELEGRAM)
    assert archive.account(OWNER)["context"] == "mixed"
    assert archive.account(OWNER, Provider.TELEGRAM)["context"] == "work"
    result = handle_search_memories({"query": "project", "sources": ["message"]}, user_email=OWNER)
    assert {row["provider"] for row in result["results"]} == {"whatsapp", "telegram"}
    assert handle_get_message({"message_id": hit["message_id"]}, user_email=OWNER)["conversation"][
        "messages"
    ]
    assert archive.message_context(OWNER, hit["message_id"], Provider.TELEGRAM)["messages"]
    with pytest.raises(ValueError, match="provider_mismatch"):
        archive.stage_records(
            whatsapp["account_id"], whatsapp["generation"], [telegram_record(telegram_types)]
        )


def test_telegram_history_edit_replay_and_channel_scoped_deletions(database, telegram_types):
    from messaging_normalization import Provider
    from telegram_sync import stage_deletions

    account = telegram_ingest(telegram_types)
    telegram_ingest(telegram_types, body="Revised project update", edited=True)
    telegram_ingest(telegram_types)
    hit = archive.search(OWNER, provider=Provider.TELEGRAM)[0]
    assert hit["body"] == "Revised project update"
    # Same numeric message ID in a megagroup cannot be revoked by chatless updates.
    telegram_ingest(telegram_types, chat=telegram_types.PeerChannel(25))
    stage_deletions(account["account_id"], account["generation"], [7], None)
    archive.drain_once()
    rows = archive.search(OWNER, provider=Provider.TELEGRAM)
    assert next(r for r in rows if r["conversation_external_id"] == "12@telegram.user")["deleted"]
    assert not next(r for r in rows if r["conversation_external_id"] == "25@telegram.channel")[
        "deleted"
    ]
    stage_deletions(account["account_id"], account["generation"], [7], "25@telegram.channel")
    archive.drain_once()
    assert all(r["deleted"] for r in archive.search(OWNER, provider=Provider.TELEGRAM))
    assert all(r["body"] for r in archive.search(OWNER, provider=Provider.TELEGRAM))


def test_telegram_cursors_and_password_challenges_reject_stale_generations(
    database, telegram_types
):
    import time

    import whatsapp_runtime
    from messaging_normalization import Provider
    from telegram_sync import checkpoint, cursor

    account = telegram_ingest(telegram_types)
    checkpoint(account["account_id"], account["generation"], "12@telegram.user", oldest=7, newest=7)
    checkpoint(account["account_id"], account["generation"], "12@telegram.user", oldest=3, newest=8)
    assert cursor(account["account_id"], "12@telegram.user")["oldest_id"] == 3
    archive.set_connection(OWNER, False, Provider.TELEGRAM)
    checkpoint(account["account_id"], account["generation"], "12@telegram.user", newest=999)
    assert cursor(account["account_id"], "12@telegram.user")["newest_id"] == 8
    whatsapp_runtime._PASSWORDS[account["account_id"]] = (
        "challenge",
        account["generation"],
        None,
        time.monotonic() + 60,
    )
    try:
        with pytest.raises(LookupError):
            whatsapp_runtime.submit_password(
                account["account_id"], account["generation"] + 1, "challenge", "secret"
            )
        whatsapp_runtime.submit_password(
            account["account_id"], account["generation"], "challenge", "secret"
        )
        with pytest.raises(LookupError):
            whatsapp_runtime.submit_password(
                account["account_id"], account["generation"], "challenge", "again"
            )
    finally:
        whatsapp_runtime.clear_qr(account["account_id"])
    assert whatsapp_runtime.password_challenge(account["account_id"]) is None


def test_telegram_web_routes_reject_cross_provider_and_owner_access(
    database, telegram_types, monkeypatch
):
    import time

    import whatsapp_runtime

    account = telegram_ingest(telegram_types)
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "a" * 32)
    app = FastAPI()
    app.include_router(create_messaging_router())
    app.dependency_overrides[get_current_user] = lambda: {"email": OWNER}
    client = TestClient(app)
    status = client.get("/messaging/status?provider=telegram")
    assert status.json()["provider"] == "telegram"
    assert status.json()["messages"] == 1
    assert status.headers["cache-control"] == "no-store"
    assert client.get("/messaging/status?provider=invalid").status_code == 422
    hit = client.get("/messaging/messages?provider=telegram").json()["messages"][0]
    assert client.get(f"/messaging/messages/{hit['message_id']}").status_code == 404
    assert (
        client.get(f"/messaging/messages/{hit['message_id']}?provider=telegram").status_code == 200
    )
    whatsapp_runtime._PASSWORDS[account["account_id"]] = (
        "challenge",
        account["generation"],
        None,
        time.monotonic() + 60,
    )
    try:
        payload = {"challenge": "challenge", "password": "temporary-secret"}
        app.dependency_overrides[get_current_user] = lambda: {"email": OTHER}
        assert client.post("/messaging/telegram/password", json=payload).status_code == 409
        app.dependency_overrides[get_current_user] = lambda: {"email": OWNER}
        response = client.post("/messaging/telegram/password", json=payload)
        assert response.status_code == 200
        assert "secret" not in response.text
        assert (
            client.get("/messaging/status?provider=telegram").json()["password_challenge"]
            == "challenge"
        )
    finally:
        whatsapp_runtime.clear_qr(account["account_id"])


@pytest.mark.asyncio
async def test_telegram_qr_rotation_password_retry_and_session_reuse(telegram_types):
    import asyncio
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError

    from messaging_normalization import ConnectionState
    from telegram_native import authorize

    logins = [
        SimpleNamespace(
            url="tg://login?token=first",
            expires=datetime.now(timezone.utc) + timedelta(seconds=30),
            wait=AsyncMock(side_effect=asyncio.TimeoutError),
        ),
        SimpleNamespace(
            url="tg://login?token=second",
            expires=datetime.now(timezone.utc) + timedelta(seconds=30),
            wait=AsyncMock(side_effect=SessionPasswordNeededError(None)),
        ),
    ]
    client = SimpleNamespace(
        is_user_authorized=AsyncMock(return_value=False),
        qr_login=AsyncMock(side_effect=logins),
        sign_in=AsyncMock(side_effect=[PasswordHashInvalidError(None), True]),
    )

    class Pipe:
        def __init__(self):
            self.events = []
            self.challenge = None
            self.inputs = ["wrong", "correct"]

        def send(self, value):
            self.events.append(value)
            if value["kind"] == "password_required":
                self.challenge = value["challenge"]

        def poll(self):
            return bool(self.inputs)

        def recv(self):
            return {"challenge": self.challenge, "password": self.inputs.pop(0)}

    pipe = Pipe()
    state = AsyncMock()
    await authorize(client, pipe, state)
    assert [e["code"] for e in pipe.events if e["kind"] == "qr"] == [
        "tg://login?token=first",
        "tg://login?token=second",
    ]
    assert "correct" not in json.dumps(pipe.events)
    state.assert_any_await(ConnectionState.PASSWORD_REQUIRED, "password_incorrect")
    assert pipe.events[-1]["kind"] == "clear_password"
    client.is_user_authorized.return_value = True
    await authorize(client, pipe, state)
    assert client.qr_login.await_count == 2


@pytest.mark.asyncio
async def test_telegram_history_checkpoint_never_skips_failed_page(
    database, telegram_types, monkeypatch
):
    from unittest.mock import AsyncMock

    from messaging_normalization import Provider
    from telegram_native import sync_dialog
    from telegram_sync import cursor

    t = telegram_types
    account = archive.set_connection(OWNER, True, Provider.TELEGRAM)
    messages = [
        t.Message(
            id=i,
            peer_id=t.PeerUser(12),
            from_id=t.PeerUser(12),
            date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            message=f"Page message {i}",
        )
        for i in (10, 9)
    ]
    client = SimpleNamespace(get_messages=AsyncMock(return_value=messages))
    dialog = SimpleNamespace(entity=t.User(id=12, first_name="Person"))
    original_stage = archive.stage_records

    def fail(*_args):
        raise RuntimeError("temporary_storage_failure")

    monkeypatch.setattr(archive, "stage_records", fail)
    with pytest.raises(RuntimeError, match="temporary_storage_failure"):
        await sync_dialog(client, dialog, 99, account["account_id"], account["generation"])
    assert cursor(account["account_id"], "12@telegram.user")["newest_id"] == 0
    monkeypatch.setattr(archive, "stage_records", original_stage)
    client.get_messages.side_effect = [messages, []]
    await sync_dialog(client, dialog, 99, account["account_id"], account["generation"])
    archive.drain_once()
    saved = cursor(account["account_id"], "12@telegram.user")
    assert saved["newest_id"] == 10 and saved["oldest_id"] == 9 and saved["backfill_done"]
    assert len(archive.search(OWNER, provider=Provider.TELEGRAM)) == 2


def test_telegram_channel_and_megagroup_kinds_and_disappearing_content(telegram_types):
    from telegram_normalization import message_record, supported_chat

    t = telegram_types
    broadcast = t.Channel(
        id=1,
        title="News",
        photo=t.ChatPhotoEmpty(),
        date=datetime.now(timezone.utc),
        broadcast=True,
    )
    group = t.Channel(
        id=2,
        title="Team",
        photo=t.ChatPhotoEmpty(),
        date=datetime.now(timezone.utc),
        megagroup=True,
    )
    assert supported_chat(broadcast)
    assert supported_chat(group)
    message = t.Message(
        id=1,
        peer_id=t.PeerChannel(2),
        from_id=t.PeerUser(12),
        date=datetime.now(timezone.utc),
        message="Group update",
    )
    assert message_record(message, 99, group)["chat"] == "2@telegram.channel"
    assert message_record(message, 99, broadcast)["conversation_kind"] == "channel"
    assert message_record(message, 99, group)["conversation_kind"] == "group"
    message.ttl_period = 30
    assert message_record(message, 99, group) is None
