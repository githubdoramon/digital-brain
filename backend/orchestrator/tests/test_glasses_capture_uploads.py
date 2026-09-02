import pytest

import glasses_capture_uploads


def _payload(size_bytes: int) -> dict[str, object]:
    return {
        "capture_id": "capture-123",
        "filename": "glasses-video.mp4",
        "mime_type": "video/mp4",
        "captured_at": "2026-01-02T03:04:05Z",
        "location": {"lat": 1.0, "lon": 2.0},
        "size_bytes": size_bytes,
    }


def test_chunked_upload_reassembles_then_commits_once(tmp_path, monkeypatch):
    monkeypatch.setattr(glasses_capture_uploads, "ROOT", tmp_path)
    committed: dict[str, object] = {}

    def fake_upload_capture(**kwargs):
        committed["media"] = kwargs["media_bytes"].read()
        committed.update(kwargs)
        return {"immich_asset_id": "asset-123"}

    monkeypatch.setattr(glasses_capture_uploads.glasses_captures, "upload_capture", fake_upload_capture)
    payload = b"large-video-payload"
    session = glasses_capture_uploads.create("user@example.test", _payload(len(payload)))
    session_id = session["session_id"]

    glasses_capture_uploads.store_chunk("user@example.test", session_id, 0, len(payload), payload[:5])
    glasses_capture_uploads.store_chunk("user@example.test", session_id, 5, len(payload), payload[5:])

    assert glasses_capture_uploads.complete("user@example.test", session_id) == {
        "immich_asset_id": "asset-123"
    }
    assert committed["capture_id"] == "capture-123"
    assert committed["captured_at"].isoformat() == "2026-01-02T03:04:05+00:00"
    assert committed["location"] == {"lat": 1.0, "lon": 2.0}
    assert committed["media"] == payload
    assert not (tmp_path / session_id).exists()


def test_chunked_upload_rejects_missing_or_foreign_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr(glasses_capture_uploads, "ROOT", tmp_path)
    session = glasses_capture_uploads.create("user@example.test", _payload(6))
    session_id = session["session_id"]

    with pytest.raises(glasses_capture_uploads.UploadSessionError, match="not available"):
        glasses_capture_uploads.store_chunk("other@example.test", session_id, 0, 6, b"abc")
    with pytest.raises(glasses_capture_uploads.UploadSessionError, match="invalid chunk range"):
        glasses_capture_uploads.store_chunk("user@example.test", session_id, 0, 7, b"abc")
    glasses_capture_uploads.store_chunk("user@example.test", session_id, 0, 6, b"abc")
    with pytest.raises(glasses_capture_uploads.UploadSessionError, match="incomplete"):
        glasses_capture_uploads.complete("user@example.test", session_id)
