import asyncio
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_S = 15.0


def _safe_put(q: asyncio.Queue, msg: dict) -> None:
    if not q.full():
        q.put_nowait(msg)


class LightstreamerBroadcaster:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []
        self._lock = threading.Lock()
        self._last_heartbeat: float = 0.0

    def start(self) -> None:
        logger.info("Broadcaster started — awaiting ingest from LS_L1.py")

    def stop(self) -> None:
        logger.info("Broadcaster stopped")

    def heartbeat(self) -> None:
        self._last_heartbeat = time.time()

    def is_live(self) -> bool:
        return (time.time() - self._last_heartbeat) < HEARTBEAT_TIMEOUT_S

    def ingest(self, msg: dict) -> None:
        self._last_heartbeat = time.time()
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            _safe_put(q, msg)

    def add_subscriber(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.append(q)

    def remove_subscriber(self, q: asyncio.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass


broadcaster: Optional[LightstreamerBroadcaster] = None


def get_broadcaster() -> LightstreamerBroadcaster:
    if broadcaster is None:
        raise RuntimeError("LightstreamerBroadcaster not initialised")
    return broadcaster
