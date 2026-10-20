# ruff: noqa: F811
"""Unchanged contacts avoid writes and model work using real PostgreSQL transactions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest

import contacts
import events
from schemas import ContactIn, ContactRelationshipIn
from tests.test_intelligence import database  # noqa: F401


@pytest.fixture
def store(database, monkeypatch):
    calls = {"embeddings": [], "tag_jobs": []}

    def embed(value):
        calls["embeddings"].append(value)
        return [float(len(calls["embeddings"]))]

    monkeypatch.setattr(contacts, "get_conn", database)
    monkeypatch.setattr(contacts, "_generate_contact_embedding", embed)
    monkeypatch.setattr(contacts, "_enqueue_contact_tag_enrichment", calls["tag_jobs"].append)
    return calls


def contact(**changes):
    return ContactIn(
        **{
            "contact_id": "contact:alex",
            "display_name": "Alex Carter",
            "aliases": ["A. Carter"],
            "birthday": "--05-12",
            "emails": ["alex@example.com"],
            "phones": ["+1 555 555 0101"],
            "links": ["https://example.com/alex"],
            "tags": ["Work", "Client"],
            "comments": "Project coordinator at Acme.",
            "external_id": "person:alex",
            **changes,
        }
    )


def saved(database):
    with database() as conn:
        return conn.execute(
            "SELECT *, xmin::text AS version FROM contacts WHERE contact_id = 'contact:alex'"
        ).fetchone()


def reset_calls(store):
    for calls in store.values():
        calls.clear()


def test_new_contact_generates_embedding_and_queues_tags(database, store):
    contacts.ingest_contact(contact())

    assert saved(database)["comments_embed"] == [1.0]
    assert len(store["embeddings"]) == 1
    assert store["tag_jobs"] == ["contact:alex"]


@pytest.mark.parametrize("birthday", [None, "--05-12", "1984-05-12"])
def test_unchanged_save_preserves_notes_link_and_birthday_without_work(database, store, birthday):
    contacts.ingest_contact(contact(birthday=birthday))
    before = saved(database)
    reset_calls(store)

    for _ in range(3):
        contacts.ingest_contact(
            contact(
                birthday=birthday,
                comments=None,
                external_id=None,
                tags=["Work", "Client", " work "],
            )
        )

    assert saved(database) == before
    assert store == {"embeddings": [], "tag_jobs": []}


def test_repeated_attendee_resolution_does_not_rewrite_sparse_contact(database, store):
    with database() as conn:
        conn.execute(
            """
            INSERT INTO contacts(contact_id, display_name, emails, tags, birthday_month, birthday_day)
            VALUES('contact:alex', 'Alex Carter', ARRAY['alex@example.com'],
                   ARRAY['meeting-attendee', 'Work'], 5, 12)
            """
        )
    before = saved(database)

    # Each event starts its own attendee cache, as it does during calendar sync.
    for _ in range(5):
        ids, _domains = events._resolve_attendee_contacts(
            ["alex@example.com"], contact_cache={}, current_user=None
        )
        assert ids == ["contact:alex"]

    assert saved(database) == before
    assert store == {"embeddings": [], "tag_jobs": []}


def test_meeting_attendee_tag_is_added_once(database, store):
    contacts.ingest_contact(contact())
    reset_calls(store)

    assert contacts.ensure_contact_for_email(" ALEX@EXAMPLE.COM ") == ("contact:alex", False)
    after_first_resolution = saved(database)
    assert after_first_resolution["tags"] == ["Work", "Client", "meeting-attendee"]
    assert len(store["embeddings"]) == 1
    assert store["tag_jobs"] == ["contact:alex"]
    reset_calls(store)

    for _ in range(3):
        assert contacts.ensure_contact_for_email("alex@example.com") == ("contact:alex", False)

    assert saved(database) == after_first_resolution
    assert store == {"embeddings": [], "tag_jobs": []}


@pytest.mark.parametrize(
    ("changes", "needs_embedding", "needs_tags"),
    [
        ({"display_name": "Alex Brooks"}, True, True),
        ({"aliases": ["Alex C."]}, True, True),
        ({"comments": "Designer at Beacon."}, True, True),
        ({"comments": ""}, True, True),
        ({"tags": ["Work", "Designer"]}, True, True),
        ({"emails": ["alex.alt@example.com"]}, False, True),
        ({"phones": ["+1 555 555 0102"]}, False, True),
        ({"links": ["https://example.org/alex"]}, False, True),
        ({"birthday": "--06-13"}, False, False),
        ({"birthday": "1984-05-12"}, False, False),
        ({"birthday": None}, False, False),
        ({"external_id": "person:replacement"}, False, False),
    ],
)
def test_real_changes_only_trigger_affected_work(
    database, store, changes, needs_embedding, needs_tags
):
    contacts.ingest_contact(contact())
    before = saved(database)
    reset_calls(store)

    contacts.ingest_contact(contact(**changes))
    after = saved(database)

    assert after["version"] != before["version"]
    for field, expected in changes.items():
        if field == "birthday":
            assert contacts.get_contact("contact:alex")["birthday"] == expected
        else:
            assert after[field] == expected
    assert len(store["embeddings"]) == int(needs_embedding)
    assert store["tag_jobs"] == (["contact:alex"] if needs_tags else [])
    if not needs_embedding:
        assert after["comments_embed"] == before["comments_embed"]
    reset_calls(store)

    contacts.ingest_contact(contact(**changes))

    assert saved(database) == after
    assert store == {"embeddings": [], "tag_jobs": []}


def test_relationship_only_changes_are_saved_without_contact_work(database, store):
    contacts.ingest_contact(contact())
    before = saved(database)
    reset_calls(store)
    relationship = ContactRelationshipIn(
        relationship_id="rel:alex-colleague",
        from_contact_id="contact:alex",
        to_contact_id="alex",
        relationship_type="Coworker",
        reciprocal_type="Coworker",
    )

    contacts.ingest_contact(contact(relationships=[relationship]))
    relationship.relationship_type = "Manager"
    relationship.reciprocal_type = "Direct Report"
    contacts.ingest_contact(contact(relationships=[relationship]))

    with database() as conn:
        saved_relationship = conn.execute(
            "SELECT * FROM contact_relationships WHERE relationship_id = 'rel:alex-colleague'"
        ).fetchone()
    assert saved_relationship["relationship_type"] == "Manager"
    assert saved_relationship["reciprocal_type"] == "Direct Report"
    assert saved(database) == before
    assert store == {"embeddings": [], "tag_jobs": []}


def test_failed_relationship_rolls_back_contact_change_without_queueing(database, store):
    contacts.ingest_contact(contact())
    before = saved(database)
    reset_calls(store)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        contacts.ingest_contact(
            contact(
                display_name="Alex Brooks",
                relationships=[
                    ContactRelationshipIn(
                        relationship_id="rel:missing",
                        from_contact_id="contact:alex",
                        to_contact_id="missing-contact",
                        relationship_type="Coworker",
                    )
                ],
            )
        )

    assert saved(database) == before
    assert store["tag_jobs"] == []


def test_concurrent_identical_updates_only_generate_work_once(database, store):
    contacts.ingest_contact(contact())
    reset_calls(store)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(contacts.ingest_contact, [contact(display_name="Alex Brooks")] * 4))

    assert saved(database)["display_name"] == "Alex Brooks"
    assert len(store["embeddings"]) == 1
    assert store["tag_jobs"] == ["contact:alex"]


def test_concurrent_relationship_saves_allow_foreign_key_checks(database, store, monkeypatch):
    contacts.ingest_contact(contact())
    contacts.ingest_contact(
        contact(
            contact_id="contact:robin",
            display_name="Robin Lake",
            emails=["robin@example.com"],
            external_id="person:robin",
        )
    )
    reset_calls(store)
    both_locked = Barrier(2)
    upsert_relationships = contacts._upsert_contact_relationships

    def save_relationships(cur, contact_id, relationships):
        both_locked.wait(timeout=5)
        upsert_relationships(cur, contact_id, relationships)

    monkeypatch.setattr(contacts, "_upsert_contact_relationships", save_relationships)
    updates = []
    for source, target in [("contact:alex", "contact:robin"), ("contact:robin", "contact:alex")]:
        value = contacts.get_contact(source)
        value["relationships"] = [
            ContactRelationshipIn(
                relationship_id=f"rel:{source}",
                from_contact_id=source,
                to_contact_id=target,
                relationship_type="Coworker",
                reciprocal_type="Coworker",
            )
        ]
        updates.append(ContactIn.model_validate(value))

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(contacts.ingest_contact, updates))

    with database() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM contact_relationships").fetchone()["n"] == 2
    assert store == {"embeddings": [], "tag_jobs": []}
