"""Keep durable job ownership alive during long model requests."""

import threading
from contextlib import contextmanager

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

# Independent renewals keep even high-reasoning requests alive without making a
# dead process block the queue for the model's entire timeout.
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 20
LEASE_INTERVAL_SQL = f"INTERVAL '{LEASE_SECONDS} seconds'"


@contextmanager
def lease_heartbeat(renew, *, interval=HEARTBEAT_SECONDS):
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                if not renew():
                    return
            except Exception:
                logger.warning("[learning] lease_renewal_failed")

    thread = threading.Thread(target=loop, name="learning-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)
