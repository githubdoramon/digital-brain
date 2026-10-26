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
        live_record,
    )
    from whatsapp_diagnostics import (
        ExcludedWhatsAppSource,
        health_event,
        project_identity,
        report_failure,
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
        from collections import Counter

        counters = Counter(dict.fromkeys((
            "live_received", "live_stage_accepted", "live_stage_rejected",
            "live_empty_or_control", "live_excluded", "live_failed",
            "history_chunks_received", "history_chunks_completed", "history_chunks_failed",
        ), 0))
        last_staged = None

        def own_jid(client):
            return project_identity(client.advanced.get_pn(), "self")

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
            except Exception as exc:
                report_failure(pipe, "bind_account", exc)
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
            nonlocal last_staged
            async with lane:
                counters["live_received"] += 1
                stage = "live_normalize"
                try:
                    record = live_record(event, own_jid(client))
                    if record:
                        stage = "live_stage"
                        accepted = await asyncio.to_thread(stage_records, account_id, generation, [record])
                        counters["live_stage_accepted"] += accepted
                        if accepted:
                            last_staged = time.monotonic()
                        else:
                            counters["live_stage_rejected"] += 1
                    else:
                        counters["live_empty_or_control"] += 1
                except ExcludedWhatsAppSource as exc:
                    counters["live_excluded"] += 1
                    report_failure(pipe, stage, exc)
                except Exception as exc:
                    counters["live_failed"] += 1
                    # Do not leak raw protobuf, callback exception, or message text.
                    report_failure(pipe, stage, exc)
                    await state(ConnectionState.ATTENTION, "capture_incomplete")

        @app.on(EvHistorySync)
        async def history(client, event):
            # Normalize in a blocking worker, never serialize the raw chunk.
            # Live callbacks have their own lane and need not await full history.
            async with history_lane:
                counters["history_chunks_received"] += 1
                try:
                    proto = event.proto
                    if proto.ByteSize() > 32 * 1024 * 1024:
                        raise ValueError("history_chunk_too_large")
                    await asyncio.to_thread(
                        capture_history, account_id, generation, proto, own_jid(client)
                    )
                    counters["history_chunks_completed"] += 1
                except Exception as exc:
                    counters["history_chunks_failed"] += 1
                    report_failure(pipe, "history_capture", exc)
                    await state(ConnectionState.ATTENTION, "history_incomplete")

        async def request_history():
            synced_epoch = -1
            while True:
                await connected_event.wait()
                epoch = connection_epoch
                client = client_holder[0]
                stage = "history_startup"
                try:
                    if synced_epoch != epoch:
                        await asyncio.wait_for(
                            client.advanced.wait_for_startup_sync(90), timeout=95
                        )
                        synced_epoch = epoch
                    if connected_event.is_set() and epoch == connection_epoch:
                        stage = "history_request"
                        await request_once(client, account_id, generation)
                except Exception as exc:
                    counters[stage + "_failed"] += 1
                    report_failure(pipe, stage, exc)
                    await state(ConnectionState.ATTENTION, "history_incomplete")
                await asyncio.sleep(10)

        async def refresh_identities():
            from messaging_identity import (
                observe_mappings,
                refresh_whatsapp_mappings,
                verified_pair,
            )

            while True:
                await connected_event.wait()
                client = client_holder[0]
                try:
                    pair = verified_pair(client.advanced.get_pn(), client.advanced.get_lid())
                    if pair:
                        await asyncio.to_thread(observe_mappings, account_id, generation, [pair])
                    await refresh_whatsapp_mappings(client, account_id, generation)
                except Exception as exc:
                    # Metadata recovery never blocks live/history capture.
                    report_failure(pipe, "identity_refresh", exc)
                await asyncio.sleep(60)

        async def capture_health():
            while True:
                await asyncio.sleep(60)
                with suppress(BrokenPipeError, EOFError, OSError):
                    pipe.send(health_event(counters, connected_event.is_set(), last_staged, time.monotonic()))

        requester = asyncio.create_task(request_history())
        identities = asyncio.create_task(refresh_identities())
        health = asyncio.create_task(capture_health())
        try:
            await app.run()
        finally:
            health.cancel()
            with suppress(asyncio.CancelledError):
                await health
            identities.cancel()
            with suppress(asyncio.CancelledError):
                await identities
            requester.cancel()
            with suppress(asyncio.CancelledError):
                await requester

    try:
        fcntl.flock(session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(main())
    except Exception as exc:
        report_failure(pipe, "session_run", exc)
        # Only a static failure code crosses the process boundary.
        with suppress(BrokenPipeError, EOFError, OSError):
            pipe.send({"kind": "error", "code": "connection_failed"})
    finally:
        pipe.close()
        os.close(session_lock)
