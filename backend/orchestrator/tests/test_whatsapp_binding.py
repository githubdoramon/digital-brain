"""Offline proof that the runtime uses the history-enabled native wheel."""

from importlib.metadata import version

import pytest


def test_native_history_binding_rejects_invalid_request_before_network(tmp_path):
    pytest.importorskip("tryx")
    from tryx.backend import SqliteStore
    from tryx.client import AdvancedClient, Tryx
    from tryx.types import JID

    assert version("tryx") == "1.5.0+digitalbrain.1"
    assert callable(AdvancedClient.fetch_message_history)
    app = Tryx(SqliteStore(str(tmp_path / "offline-session.db")))
    client = app.get_client()
    for count in (0, 101):
        with pytest.raises(ValueError, match="Invalid history request"):
            client.advanced.fetch_message_history(
                JID("15550000001", "s.whatsapp.net"), "anchor", False, 1700000000000, count
            )
    # A valid request requires a running client; this test never connects.
    with pytest.raises(RuntimeError, match="Client is not running"):
        client.advanced.fetch_message_history(
            JID("15550000001", "s.whatsapp.net"), "anchor", False, 1700000000000, 100
        )
