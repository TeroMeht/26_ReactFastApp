"""
Lightweight in-process pub/sub for SSE streams.

Each class is a process-local queue (or store) that an HTTP handler can write
into and an SSE generator can drain. They are deliberately simple - no
external broker, no persistence; the assumption is that there is one backend
worker and the stream is best-effort.

Two flavours live here:

- ``SSEEvent``           -- legacy alarms queue, kept as-is so the existing
                            /api/alarms/* endpoints keep working.
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


class StreamerStatusStore:
    """
    Lifecycle-driven streamer status with local PID liveness detection.

    On start the streamer POSTs its OS PID; the backend stores it and
    marks "running". A lightweight watchdog in main.py lifespan polls
    psutil.pid_exists() every few seconds — if the PID is gone, the
    backend marks "offline" without any cooperation from the streamer
    (so closing the cmd window is detected). The streamer also fires
    /stop on a clean exit as a fast-path notification.

    Status transitions are pushed onto a queue consumed by the SSE
    generator at GET /api/streamer-status/stream.
    """

    _status: str = "offline"  # "running" | "offline" | "error"
    _last_transition_ts: float = 0.0
    _pid: Optional[int] = None
    # One queue per connected SSE client. Transitions are broadcast to every
    # subscriber so multiple tabs / components stay in sync.
    _subscribers: list = []

    @staticmethod
    def mark_running(pid: Optional[int] = None) -> dict:
        """Streamer started. Stores PID for liveness checks. Idempotent."""
        if pid is not None:
            StreamerStatusStore._pid = pid
        if StreamerStatusStore._status != "running":
            StreamerStatusStore._set_status("running")
        return StreamerStatusStore.current()

    @staticmethod
    def mark_offline() -> dict:
        """Streamer is down. Idempotent."""
        if StreamerStatusStore._status != "offline":
            StreamerStatusStore._set_status("offline")
        StreamerStatusStore._pid = None
        return StreamerStatusStore.current()

    @staticmethod
    def pid() -> Optional[int]:
        return StreamerStatusStore._pid

    @staticmethod
    def current() -> dict:
        """Snapshot used by GET /api/streamer-status."""
        return {
            "status": StreamerStatusStore._status,
            "pid": StreamerStatusStore._pid,
            "last_transition_ts": StreamerStatusStore._last_transition_ts,
        }

    @staticmethod
    def subscribe() -> Deque[dict]:
        """Create a new per-client queue. Caller must unsubscribe on close."""
        q: Deque[dict] = deque()
        StreamerStatusStore._subscribers.append(q)
        return q

    @staticmethod
    def unsubscribe(q: Deque[dict]) -> None:
        try:
            StreamerStatusStore._subscribers.remove(q)
        except ValueError:
            pass

    # -- internal helpers --------------------------------------------------
    @staticmethod
    def _set_status(new_status: str) -> None:
        StreamerStatusStore._status = new_status
        StreamerStatusStore._last_transition_ts = time.time()
        evt = {
            "status": new_status,
            "pid": StreamerStatusStore._pid,
            "last_transition_ts": StreamerStatusStore._last_transition_ts,
        }
        for q in StreamerStatusStore._subscribers:
            q.append(evt)
