"""Isolated Tryx host in the orchestrator container. No protocol output escapes."""

from __future__ import annotations

import os
from contextlib import suppress


def run(account_id: str, generation: int, session_path: str, pipe, parent_pid: int):
    # Tryx initializes a process-global native logger and offers no stop method.
    # Silence native error text before importing anything that can log identities.
    os.environ["RUST_LOG"] = "off"
    os.umask(0o077)
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    import asyncio
    import fcntl
    import threading
    import time

    def watch_parent():
        while os.getppid() == parent_pid:
            time.sleep(1)
        os._exit(0)  # Never leave an orphan owning the linked-device session.

    threading.Thread(target=watch_parent, daemon=True).start()
    session_lock = os.open(session_path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)

    from messaging_archive import bind_self, runtime_state, stage_records
    from messaging_normalization import (
        ConnectionState,
        Provider,
        jid,
        live_record,
    )
    from whatsapp_history import capture_history, request_once

    async def main():
        from tryx.backend import SqliteStore
        from tryx.client import Tryx
        from tryx.events import (
            EvConnected,
            EvDisconnected,
            EvHistorySync,
            EvLoggedOut,
            EvMessage,
            EvPairingQrCode,
        )

        app = Tryx(SqliteStore(session_path))
        # Bound concurrent database work. No idle media download/processing APIs.
        lane = asyncio.Semaphore(1)
        history_lane = asyncio.Semaphore(1)
        connected_event = asyncio.Event()
        client_holder = []
        connection_epoch = 0

        def own_jid(client):
            return jid(client.advanced.get_pn())

        async def state(value, error=None):
            await asyncio.to_thread(runtime_state, account_id, generation, value, error)

        @app.on(EvPairingQrCode)
        async def qr(_client, event):
            pipe.send({"kind": "qr", "code": event.code, "timeout": event.timeout})
            await state(ConnectionState.PAIRING)

        @app.on(EvConnected)
        async def connected(_client, _event):
            nonlocal connection_epoch
            try:
                own = own_jid(_client)
                if not own:
                    raise ValueError("missing_self_identity")
                await asyncio.to_thread(bind_self, account_id, generation, Provider.WHATSAPP, own)
            except Exception:
                connected_event.clear()
                await state(ConnectionState.ATTENTION, "account_mismatch")
                return
            connection_epoch += 1
            client_holder[:] = [_client]
            connected_event.set()
            pipe.send({"kind": "clear_qr"})
            await state(ConnectionState.CONNECTED)

        @app.on(EvDisconnected)
        async def disconnected(_client, _event):
            connected_event.clear()
            pipe.send({"kind": "clear_qr"})
            await state(ConnectionState.CONNECTING)

        @app.on(EvLoggedOut)
        async def logged_out(_client, _event):
            connected_event.clear()
            pipe.send({"kind": "clear_qr"})
            await state(ConnectionState.NEEDS_PAIRING, "session_expired")

        @app.on(EvMessage)
        async def message(client, event):
            async with lane:
                try:
                    record = live_record(event, own_jid(client))
                    if record:
                        await asyncio.to_thread(stage_records, account_id, generation, [record])
                except Exception:
                    # Do not leak raw protobuf, callback exception, or message text.
                    await state(ConnectionState.ATTENTION, "capture_incomplete")

        @app.on(EvHistorySync)
        async def history(client, event):
            # Normalize in a blocking worker, never serialize the raw chunk.
            # Live callbacks have their own lane and need not await full history.
            async with history_lane:
                try:
                    proto = event.proto
                    if proto.ByteSize() > 32 * 1024 * 1024:
                        raise ValueError("history_chunk_too_large")
                    await asyncio.to_thread(
                        capture_history, account_id, generation, proto, own_jid(client)
                    )
                except Exception:
                    await state(ConnectionState.ATTENTION, "history_incomplete")

        async def request_history():
            synced_epoch = -1
            while True:
                await connected_event.wait()
                epoch = connection_epoch
                client = client_holder[0]
                try:
                    if synced_epoch != epoch:
                        await asyncio.wait_for(
                            client.advanced.wait_for_startup_sync(90), timeout=95
                        )
                        synced_epoch = epoch
                    if connected_event.is_set() and epoch == connection_epoch:
                        await request_once(client, account_id, generation)
                except Exception:
                    await state(ConnectionState.ATTENTION, "history_incomplete")
                await asyncio.sleep(10)

        requester = asyncio.create_task(request_history())
        try:
            await app.run()
        finally:
            requester.cancel()
            with suppress(asyncio.CancelledError):
                await requester

    try:
        fcntl.flock(session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(main())
    except Exception:
        # Only a static failure code crosses the process boundary.
        with suppress(BrokenPipeError, EOFError, OSError):
            pipe.send({"kind": "error", "code": "connection_failed"})
    finally:
        pipe.close()
        os.close(session_lock)
