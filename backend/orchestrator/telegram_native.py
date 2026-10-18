"""Official MTProto access through Telethon, isolated inside the existing container."""

from __future__ import annotations

import os
from contextlib import suppress


def configured() -> bool:
    value = os.getenv("TELEGRAM_API_ID", "")
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    return (
        value.isdigit()
        and int(value) > 0
        and len(api_hash) == 32
        and all(c in "0123456789abcdefABCDEF" for c in api_hash)
    )


async def authorize(client, pipe, state):
    """No phone/code collection: QR approval plus optional transient 2FA password."""
    import asyncio
    import secrets
    from datetime import datetime, timezone

    from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError

    from messaging_normalization import ConnectionState

    if await client.is_user_authorized():
        return
    while True:
        login = await client.qr_login()
        seconds = max(1, int((login.expires - datetime.now(timezone.utc)).total_seconds()))
        pipe.send({"kind": "qr", "code": login.url, "timeout": seconds})
        await state(ConnectionState.PAIRING)
        try:
            await login.wait(timeout=seconds)
            break
        except asyncio.TimeoutError:
            continue
        except SessionPasswordNeededError:
            pipe.send({"kind": "clear_qr"})
            challenge = secrets.token_urlsafe(24)
            pipe.send({"kind": "password_required", "challenge": challenge})
            await state(ConnectionState.PASSWORD_REQUIRED)
            deadline = asyncio.get_running_loop().time() + 300
            while asyncio.get_running_loop().time() < deadline:
                if not pipe.poll():
                    await asyncio.sleep(0.2)
                    continue
                command = pipe.recv()
                if command.get("challenge") != challenge:
                    continue
                password = command.pop("password", "")
                try:
                    await client.sign_in(password=password)
                    pipe.send({"kind": "clear_password"})
                    return
                except PasswordHashInvalidError:
                    await state(ConnectionState.PASSWORD_REQUIRED, "password_incorrect")
                finally:
                    password = None
            raise TimeoutError("login_expired")
    pipe.send({"kind": "clear_qr"})


async def sync_dialog(client, dialog, self_id, account_id, generation):
    import asyncio

    from telethon import utils

    from messaging_archive import stage_records
    from messaging_history import history_cutoff
    from messaging_normalization import Origin
    from telegram_normalization import message_record, peer_id
    from telegram_sync import checkpoint, cursor

    chat = peer_id(utils.get_peer(dialog.entity))
    saved = await asyncio.to_thread(cursor, account_id, chat)
    cutoff = history_cutoff()

    def boundary_reached(messages):
        return any(message.date and message.date < cutoff for message in messages)

    async def stage(messages):
        records = []
        for message in messages:
            if message.date and message.date < cutoff:
                continue
            sender = await message.get_sender()
            record = message_record(message, self_id, dialog.entity, sender, Origin.HISTORY)
            if record:
                records.append(record)
        if records:
            await asyncio.to_thread(stage_records, account_id, generation, records)

    if not saved["newest_id"]:
        recent = await client.get_messages(dialog.entity, limit=100)
        await stage(recent)
        if recent:
            await asyncio.to_thread(
                checkpoint,
                account_id,
                generation,
                chat,
                oldest=min(m.id for m in recent),
                newest=max(m.id for m in recent),
                done=boundary_reached(recent),
            )
    else:
        # Catch up in ascending order. Never jump over uncommitted pages.
        fresh = [
            m
            async for m in client.iter_messages(
                dialog.entity, min_id=saved["newest_id"], reverse=True, limit=100
            )
        ]
        await stage(fresh)
        if fresh:
            await asyncio.to_thread(
                checkpoint, account_id, generation, chat, newest=max(m.id for m in fresh)
            )
        # Reconcile a bounded recent window for edits missed during downtime.
        await stage(await client.get_messages(dialog.entity, limit=100))
    saved = await asyncio.to_thread(cursor, account_id, chat)
    if not saved["backfill_done"]:
        older = await client.get_messages(dialog.entity, offset_id=saved["oldest_id"], limit=100)
        await stage(older)
        await asyncio.to_thread(
            checkpoint,
            account_id,
            generation,
            chat,
            oldest=min((m.id for m in older), default=0),
            done=not older or boundary_reached(older),
        )


def run(account_id: str, generation: int, session_path: str, pipe, parent_pid: int):
    import asyncio
    import fcntl
    import logging
    import threading
    import time

    os.umask(0o077)
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)

    def watch_parent():
        while os.getppid() == parent_pid:
            time.sleep(1)
        os._exit(0)

    threading.Thread(target=watch_parent, daemon=True).start()
    session_lock = os.open(session_path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)

    async def main():
        from telethon import TelegramClient, events
        from telethon.errors import FloodWaitError
        from telethon.tl.types import PeerChannel

        from messaging_archive import bind_self, runtime_state, stage_records
        from messaging_normalization import ConnectionState, Provider
        from telegram_normalization import message_record, peer_id, supported_chat
        from telegram_sync import stage_deletions

        quiet = logging.getLogger("telegram_private")
        quiet.disabled = True
        quiet.propagate = False
        client = TelegramClient(
            session_path,
            int(os.environ["TELEGRAM_API_ID"]),
            os.environ["TELEGRAM_API_HASH"],
            base_logger=quiet,
            catch_up=True,
            flood_sleep_threshold=60,
            request_retries=2,
            connection_retries=5,
        )
        queue = asyncio.Queue(maxsize=100)

        async def state(value, error=None):
            await asyncio.to_thread(runtime_state, account_id, generation, value, error)

        async def enqueue(event):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                await state(ConnectionState.ATTENTION, "capture_incomplete")

        for kind in (events.NewMessage, events.MessageEdited, events.MessageDeleted):
            client.add_event_handler(enqueue, kind())

        async def capture(self_id):
            while True:
                event = await queue.get()
                try:
                    if isinstance(event, events.MessageDeleted.Event):
                        chat = (
                            peer_id(PeerChannel(getattr(event.original_update, "channel_id", None)))
                            if getattr(event.original_update, "channel_id", None)
                            else None
                        )
                        for start in range(0, len(event.deleted_ids), 100):
                            await asyncio.to_thread(
                                stage_deletions,
                                account_id,
                                generation,
                                event.deleted_ids[start : start + 100],
                                chat,
                            )
                    else:
                        chat = await event.get_chat()
                        sender = await event.get_sender()
                        record = message_record(event.message, self_id, chat, sender)
                        if record:
                            await asyncio.to_thread(stage_records, account_id, generation, [record])
                except Exception:
                    await state(ConnectionState.ATTENTION, "capture_incomplete")
                finally:
                    queue.task_done()

        await client.connect()
        capture_task = None
        try:
            await authorize(client, pipe, state)
            me = await client.get_me()
            if me.bot:
                raise ValueError("personal_account_required")
            await asyncio.to_thread(
                bind_self, account_id, generation, Provider.TELEGRAM, f"{me.id}@telegram.user"
            )
            pipe.send({"kind": "clear_qr"})
            pipe.send({"kind": "clear_password"})
            await state(ConnectionState.CONNECTED)
            capture_task = asyncio.create_task(capture(me.id))
            while client.is_connected():
                try:
                    async for dialog in client.iter_dialogs():
                        if supported_chat(dialog.entity):
                            await sync_dialog(client, dialog, me.id, account_id, generation)
                            await asyncio.sleep(1)
                    await asyncio.sleep(60)
                except FloodWaitError as exc:
                    await state(ConnectionState.CONNECTING, "rate_limited")
                    await asyncio.sleep(max(1, exc.seconds))
                    await state(ConnectionState.CONNECTED)
                except Exception:
                    await state(ConnectionState.ATTENTION, "history_incomplete")
                    await asyncio.sleep(60)
        finally:
            if capture_task:
                capture_task.cancel()
                with suppress(asyncio.CancelledError):
                    await capture_task
            await client.disconnect()

    try:
        fcntl.flock(session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(main())
    except Exception:
        with suppress(BrokenPipeError, EOFError, OSError):
            pipe.send({"kind": "error", "code": "connection_failed"})
    finally:
        pipe.close()
        os.close(session_lock)
