from io import BytesIO

import glasses_captures
import immich_client


def test_prepare_media_hashes_seekable_stream_in_bounded_chunks():
    payload = (b"capture-bytes-" * 100_000) + b"end"
    stream, size_bytes, checksum, immich_checksum = glasses_captures._prepare_media(
        BytesIO(payload)
    )

    assert size_bytes == len(payload)
    assert checksum
    assert immich_checksum
    assert stream.read() == payload


def test_streaming_multipart_body_does_not_materialize_file():
    file_obj = BytesIO(b"video-payload")
    body = immich_client._StreamingMultipartBody(
        prefix=b"prefix",
        file_obj=file_obj,
        file_size=len(b"video-payload"),
        suffix=b"suffix",
    )

    assert len(body) == len(b"prefixvideo-payloadsuffix")
    assert b"".join(body) == b"prefixvideo-payloadsuffix"
