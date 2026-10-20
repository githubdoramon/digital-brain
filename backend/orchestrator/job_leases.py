"""Keep durable job ownership alive during long model requests."""

import threading
from contextlib import contextmanager

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


@contextmanager
def lease_heartbeat(renew, *, interval=60):
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
