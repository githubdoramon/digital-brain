"""Fixed, authenticated glasses actions. No transcript or model invocation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
from enum import StrEnum
from pathlib import Path
from uuid import UUID

import requests


class GlassesGateCommand(StrEnum):
    OPEN_FRONT_GATE = "open_front_gate"
    TOGGLE_CAR_GATE = "toggle_car_gate"


class CommandStatus(StrEnum):
    ACCEPTED = "accepted"
    FAILED = "failed"
    UNKNOWN = "unknown"
    EXPIRED = "expired"
    NOT_CONFIGURED = "not_configured"


SCRIPT_ENV = {
    GlassesGateCommand.OPEN_FRONT_GATE: "GLASSES_FRONT_GATE_SCRIPT",
    GlassesGateCommand.TOGGLE_CAR_GATE: "GLASSES_CAR_GATE_SCRIPT",
}


def execute(user: str, command: GlassesGateCommand, request_id: UUID, captured_at_ms: int) -> dict:
    """Reserve before dispatch. An uncertain request is never automatically repeated."""
    command = GlassesGateCommand(command)
    if not -10_000 <= time.time() * 1000 - captured_at_ms <= 90_000:
        return {"status": CommandStatus.EXPIRED}
    base = os.getenv("HA_URL", "").rstrip("/")
    token = os.getenv("HA_TOKEN", "")
    script = os.getenv(SCRIPT_ENV[command], "").strip()
    if not base or not token or not re.fullmatch(r"script\.[a-z0-9_]+", script):
        return {"status": CommandStatus.NOT_CONFIGURED}
    owner = hashlib.sha256(user.lower().encode()).hexdigest()
    root = Path(os.getenv("GLASSES_COMMAND_RECEIPT_DIR", "storage/glasses-commands")) / owner
    root.mkdir(parents=True, exist_ok=True)
    # A per-owner lock also serializes the bounded journal cleanup.
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for receipt in root.glob("*.json"):
            if time.time() - receipt.stat().st_mtime > 86400:
                receipt.unlink()
        path = root / f"{request_id}.json"
        if path.exists():
            prior = json.loads(path.read_text())
            if prior["command"] != command:
                raise ValueError("Command request ID already used")
            return {"status": prior["status"]}
        if sum(1 for _ in root.glob("*.json")) >= 4096:
            return {"status": CommandStatus.FAILED}

        def store(status: CommandStatus) -> None:
            temp = path.with_suffix(".tmp")
            with temp.open("w") as out:
                json.dump({"command": command, "status": status}, out)
                out.flush()
                os.fsync(out.fileno())
            temp.replace(path)
            directory_fd = os.open(root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

        # Survives a crash after HA accepted the request but before its response was saved.
        store(CommandStatus.UNKNOWN)
        try:
            response = requests.post(
                base + "/api/services/script/turn_on",
                headers={"Authorization": f"Bearer {token}"},
                json={"entity_id": script},
                timeout=(5, 10),
                allow_redirects=False,
                stream=True,
            )
            try:
                code = response.status_code
            finally:
                response.close()
            # A 5xx can occur after dispatch; preserve uncertainty rather than replaying a toggle.
            status = (
                CommandStatus.ACCEPTED
                if 200 <= code < 300
                else CommandStatus.FAILED
                if 400 <= code < 500
                else CommandStatus.UNKNOWN
            )
        except requests.RequestException:
            status = CommandStatus.UNKNOWN
        store(status)
        return {"status": status}
