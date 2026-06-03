"""
Lightweight in-process pub/sub for SSE streams.

Each class is a process-local queue (or store) that an HTTP handler can write
into and an SSE generator can drain. They are deliberately simple - no
external broker, no persistence; the assumption is that there is one backend
worker and the stream is best-effort.

Three flavours live here:

- ``SSEEvent``           -- legacy alarms queue, kept as-is so the existing
                            /api/alarms/* endpoints keep working.
- ``LivestreamSSEEvent`` -- queue of per-symbol CandleRow updates pushed by
                            the streamer (POST /api/livestream/emit).
- ``StreamerStatusStore``-- heartbeat + status tracker for the streamer
                            process. The streamer pings POST
                            /api/streamer-status/heartbeat at a fixed cadence;
                            a backend watchdog flips the status to "offline"
                            when no heartbeat lands inside the threshold.
                            Status transitions are pushed to subscribers.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional

from schemas.api_schemas import CandleRow


class SSEEvent:
    """Alarms queue (existing - kept for backwards compatibility)."""

    EVENTS: Deque = deque()

    @staticmethod
    def add_event(event) -> None:
        SSEEvent.EVENTS.append(event)

    @staticmethod
    def get_event():
        if len(SSEEvent.EVENTS) > 0:
            return SSEEvent.EVENTS.popleft()
        return None

    @staticmethod
    def count() -> int:
        return len(SSEEvent.EVENTS)


class LivestreamSSEEvent:
    """Queue of CandleRow updates from the streamer."""

    EVENTS: Deque[CandleRow] = deque()

    @staticmethod
    def add_event(event: CandleRow) -> None:
        LivestreamSSEEvent.EVENTS.append(event)

    @staticmethod
    def get_event() -> Optional[CandleRow]:
        if len(LivestreamSSEEvent.EVENTS) > 0:
            return LivestreamSSEEvent.EVENTS.popleft()
        return None

    @staticmethod
    def count() -> int:
        return len(LivestreamSSEEvent.EVENTS)


class StreamerStatusStore:
    """
    Holds the streamer's most recent heartbeat timestamp and last published
    status, plus a queue of status-change events for the SSE generator.

    The status is derived from heartbeat freshness:
        last heartbeat within HEARTBEAT_THRESHOLD_SEC -> "running"
        otherwise                                     -> "offline"

    Producers:
      - POST /api/streamer-status/heartbeat       -> record_heartbeat()
      - background watchdog task in main lifespan -> tick()

    Consumer:
      - GET /api/streamer-status/stream           -> get_event() in a loop
    """

    # If we haven't seen a heartbeat for this many seconds, declare the
    # streamer offline. The streamer is expected to ping every 2s, so 6s
    # tolerates two missed pings without flapping.
    HEARTBEAT_THRESHOLD_SEC: float = 6.0

    _last_heartbeat_ts: float = 0.0
    _status: str = "offline"  # "running" | "offline" | "error"
    _events: Deque[dict] = deque()

    @staticmethod
    def record_heartbeat() -> dict:
        """
        Called by POST /api/streamer-status/heartbeat. Updates the
        heartbeat timestamp and, if the status was not already "running",
        flips it and emits a transition event.
        """
        StreamerStatusStore._last_heartbeat_ts = time.time()
        if StreamerStatusStore._status != "running":
            StreamerStatusStore._set_status("running")
        return {"status": StreamerStatusStore._status}

    @staticmethod
    def tick() -> None:
        """
        Called by the backend watchdog every second. If the heartbeat is
        stale, demote the status to "offline" and emit a transition event.
        Never promotes - promotion happens only on receipt of a heartbeat.
        """
        if StreamerStatusStore._status == "offline":
            return  # nothing to do, already offline
        elapsed = time.time() - StreamerStatusStore._last_heartbeat_ts
        if elapsed > StreamerStatusStore.HEARTBEAT_THRESHOLD_SEC:
            StreamerStatusStore._set_status("offline")

    @staticmethod
    def current() -> dict:
        """Best-effort snapshot used by GET /api/streamer-status."""
        return {
            "status": StreamerStatusStore._status,
            "last_heartbeat_ts": StreamerStatusStore._last_heartbeat_ts,
        }

    @staticmethod
    def get_event() -> Optional[dict]:
        if len(StreamerStatusStore._events) > 0:
            return StreamerStatusStore._events.popleft()
        return None

    @staticmethod
    def count() -> int:
        return len(StreamerStatusStore._events)

    # -- internal helpers --------------------------------------------------
    @staticmethod
    def _set_status(new_status: str) -> None:
        StreamerStatusStore._status = new_status
        StreamerStatusStore._events.append(
            {
                "status": new_status,
                "last_heartbeat_ts": StreamerStatusStore._last_heartbeat_ts,
            }
        )
