from datetime import datetime, timezone
from unittest.mock import Mock, patch

import immich_client


def _response(payload):
    response = Mock()
    response.json.return_value = payload
    response.text = ""
    response.raise_for_status.return_value = None
    return response


def test_ensure_album_uses_bulk_ids_for_existing_album():
    config = immich_client.ImmichConfig(
        base_url="https://immich.example",
        api_key="test-key",
        face_api_key=None,
    )
    albums_response = _response([{"id": "album-1", "albumName": "Captures"}])
    put_response = _response({})

    with (
        patch.object(immich_client.requests, "get", return_value=albums_response),
        patch.object(immich_client.requests, "put", return_value=put_response) as put,
    ):
        album = immich_client.ensure_album("Captures", "asset-1", config)

    assert album["id"] == "album-1"
    assert album["assetIds"] == ["asset-1"]
    assert put.call_args.kwargs["json"] == {"ids": ["asset-1"]}


def test_ensure_album_includes_immich_error_body():
    config = immich_client.ImmichConfig(
        base_url="https://immich.example",
        api_key="test-key",
        face_api_key=None,
    )
    albums_response = _response([{"id": "album-1", "albumName": "Captures"}])
    failed_response = _response({"message": "invalid asset"})
    failed_response.text = '{"message":"invalid asset"}'
    error = immich_client.requests.HTTPError("400 Client Error")
    error.response = failed_response
    failed_response.raise_for_status.side_effect = error

    with (
        patch.object(immich_client.requests, "get", return_value=albums_response),
        patch.object(immich_client.requests, "put", return_value=failed_response),
    ):
        try:
            immich_client.ensure_album("Captures", "asset-1", config)
        except immich_client.ImmichClientError as exc:
            assert "invalid asset" in str(exc)
        else:
            raise AssertionError("ensure_album should report the Immich response body")


def test_search_assets_by_time_paginates_metadata_results():
    config = immich_client.ImmichConfig(
        base_url="https://immich.example",
        api_key="test-key",
        face_api_key=None,
    )
    first_page = _response({"assets": {"items": [{"id": "asset-1"}], "nextPage": 2}})
    second_page = _response({"assets": {"items": [{"id": "asset-2"}]}})

    with patch.object(
        immich_client.requests,
        "post",
        side_effect=[first_page, second_page],
    ) as post:
        assets = immich_client.search_assets_by_time(
            taken_after=datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc),
            taken_before=datetime(2026, 8, 30, 11, 0, tzinfo=timezone.utc),
            config=config,
        )

    assert [asset["id"] for asset in assets] == ["asset-1", "asset-2"]
    assert post.call_count == 2
    assert post.call_args_list[0].kwargs["json"] == {
        "takenAfter": "2026-08-30T10:00:00Z",
        "takenBefore": "2026-08-30T11:00:00Z",
        "withDeleted": False,
        "withArchived": False,
        "withExif": True,
        "size": 1000,
        "page": 1,
    }
