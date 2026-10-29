"""At-most-once claims for voice chat, including its regular chat tool effects."""

import fcntl
import hashlib
import os
import time
from pathlib import Path
from uuid import UUID


def claim(user: str, request_id: UUID) -> bool:
    owner = hashlib.sha256(user.lower().encode()).hexdigest()
    root = (
        Path(os.getenv("GLASSES_COMMAND_RECEIPT_DIR", "storage/glasses-commands")) / "chat" / owner
    )
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for path in root.glob("*.receipt"):
            if time.time() - path.stat().st_mtime > 86400:
                path.unlink()
        target = root / f"{request_id}.receipt"
        if target.exists() or sum(1 for _ in root.glob("*.receipt")) >= 4096:
            return False
        with target.open("x") as receipt:
            receipt.flush()
            os.fsync(receipt.fileno())
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    return True
