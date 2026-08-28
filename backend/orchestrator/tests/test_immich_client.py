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
