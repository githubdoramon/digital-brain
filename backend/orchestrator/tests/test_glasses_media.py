import hashlib
from unittest.mock import MagicMock

import pytest

import glasses_media as media


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSES_MEDIA_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(media, "receipt", lambda *args: None)
    data = b"original-photo-bytes"
    metadata = {
        "capture_key": "a" * 64,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "filename": "capture.jpg",
        "mime_type": "image/jpeg",
        "captured_at": "2026-01-02T12:00:00Z",
    }
    result = media.create_session("viewer@example.test", metadata)
    return result["session_id"], metadata, data


def test_resumes_identical_retry_without_duplicating_bytes(session):
    key, metadata, data = session
    assert media.store_chunk("viewer@example.test", key, 0, data[:8])["offset"] == 8
    assert media.store_chunk("viewer@example.test", key, 0, data[:8])["offset"] == 8
    assert media.create_session("viewer@example.test", metadata)["offset"] == 8
    media.store_chunk("viewer@example.test", key, 8, data[8:])
    directory, _ = media.session("viewer@example.test", key)
    assert (directory / "media").read_bytes() == data


def test_rejects_conflicting_retry_and_out_of_order_chunk(session):
    key, _, data = session
    media.store_chunk("viewer@example.test", key, 0, data[:8])
    with pytest.raises(ValueError, match="Conflicting"):
        media.store_chunk("viewer@example.test", key, 0, b"badbytes")
    with pytest.raises(ValueError, match="offset"):
        media.store_chunk("viewer@example.test", key, 9, data[9:])


def test_owner_isolation_and_metadata_immutability(session):
    key, metadata, _ = session
    with pytest.raises(ValueError, match="Unknown"):
        media.store_chunk("other@example.test", key, 0, b"x")
    with pytest.raises(ValueError, match="changed"):
        media.create_session("viewer@example.test", {**metadata, "sha256": "f" * 64})


def test_checksum_failure_retains_original_and_never_contacts_immich(session, monkeypatch):
    key, _, data = session
    wrong = b"x" * len(data)
    media.store_chunk("viewer@example.test", key, 0, wrong)
    upload = MagicMock()
    monkeypatch.setattr(media.immich_client, "upload_asset_stream", upload)
    with pytest.raises(ValueError, match="checksum"):
        media.complete("viewer@example.test", key)
    upload.assert_not_called()
    directory, _ = media.session("viewer@example.test", key)
    assert (directory / "media").read_bytes() == wrong


def test_receipt_recovers_lost_complete_reply(session, monkeypatch):
    key, _, data = session
    media.store_chunk("viewer@example.test", key, 0, data)
    confirmed = {"confirmed": True, "asset_id": "fake-asset", "album_id": "fake-album"}
    monkeypatch.setattr(media, "receipt", lambda *args: confirmed)
    assert media.complete("viewer@example.test", key) == confirmed
    directory, _ = media.session("viewer@example.test", key)
    assert not (directory / "media").exists()


def test_requires_existing_unambiguous_album(monkeypatch):
    monkeypatch.delenv("GLASSES_IMMICH_ALBUM_ID", raising=False)
    monkeypatch.delenv("GLASSES_IMMICH_ALBUM_NAME", raising=False)
    with pytest.raises(ValueError, match="existing glasses album"):
        media.resolve_album(MagicMock())
    monkeypatch.setenv("GLASSES_IMMICH_ALBUM_NAME", "Example captures")
    response = MagicMock()
    response.json.return_value = [
        {"albumName": "Example captures", "id": "first"},
        {"albumName": "Example captures", "id": "second"},
    ]
    monkeypatch.setattr(media.requests, "get", lambda *a, **kw: response)
    with pytest.raises(ValueError, match="uniquely"):
        media.resolve_album(MagicMock())


@pytest.fixture
def immich_flow(session, monkeypatch):
    from contextlib import contextmanager

    key, _, data = session
    media.store_chunk("viewer@example.test", key, 0, data)
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    connection.cursor.return_value.__enter__.return_value = cursor

    @contextmanager
    def database():
        yield connection

    monkeypatch.setattr(media, "get_conn", database)
    monkeypatch.setattr(media, "resolve_album", lambda *a: "fake-album")
    monkeypatch.setattr(media.immich_client, "get_immich_config", MagicMock())
    monkeypatch.setattr(
        media.immich_client, "upload_asset_stream", lambda *a, **kw: {"id": "fake-asset"}
    )
    monkeypatch.setattr(media.immich_client, "fetch_asset", lambda *a, **kw: {"id": "fake-asset"})
    monkeypatch.setattr(media.user_locations, "get_nearest_location", lambda **kw: None)
    response = MagicMock()
    monkeypatch.setattr(media.requests, "put", lambda *a, **kw: response)
    monkeypatch.setattr(media.requests, "get", lambda *a, **kw: response)
    return key, connection, response


def test_album_partial_failure_retains_bytes_and_does_not_commit(immich_flow):
    key, connection, response = immich_flow
    response.json.return_value = {"assets": []}
    with pytest.raises(ValueError, match="membership"):
        media.complete("viewer@example.test", key)
    connection.commit.assert_not_called()
    directory, _ = media.session("viewer@example.test", key)
    assert (directory / "media").exists()


def test_success_commits_receipt_before_releasing_original(immich_flow):
    key, connection, response = immich_flow
    response.json.return_value = {"assets": [{"id": "fake-asset"}]}
    directory, _ = media.session("viewer@example.test", key)
    connection.commit.side_effect = lambda: (
        pytest.fail("bytes removed before commit") if not (directory / "media").exists() else None
    )
    result = media.complete("viewer@example.test", key)
    assert result["confirmed"] is True
    connection.commit.assert_called_once()
    assert not (directory / "media").exists()


def test_staging_reservation_covers_sessions_before_bytes_arrive(session):
    _, metadata, _ = session
    first = {**metadata, "capture_key": "b" * 64, "size": media.MAX_BYTES}
    second = {**metadata, "capture_key": "c" * 64, "size": media.MAX_BYTES}
    media.create_session("viewer@example.test", first)
    with pytest.raises(ValueError, match="quota"):
        media.create_session("viewer@example.test", second)


def test_abandoned_session_expiration_recovers_from_phone_original(session):
    import os
    import time

    key, metadata, data = session
    media.store_chunk("viewer@example.test", key, 0, data[:8])
    directory, _ = media.session("viewer@example.test", key)
    stale = time.time() - 8 * 86400
    os.utime(directory, (stale, stale))
    restored = media.create_session("viewer@example.test", metadata)
    assert restored["session_id"] == key
    assert restored["offset"] == 0
