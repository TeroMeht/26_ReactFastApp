"""
In-process anchor for fallback-anchored lockouts.

Background. The normal tier-1 lockout is anchored to the most recent
losing trade's exit_time (which is stable across requests, so refreshes
don't move it). Only when there's no fill to anchor on -- the test
override path where the threshold is artificially set to fire with no
losses, or any future code path that locks without a fill -- do we have
to fall back to "anchor on now". Without a cache, every poll picks a new
"now" and the cooldown_until slides forward, so the user could refresh
indefinitely.

This module remembers the first cooldown_until we compute for a given
key and returns it on subsequent calls until either:
  - the streak breaks (caller invokes clear()), or
  - the cooldown_until is in the past (caller invokes expire_if_past()).

State lives in-process only -- a backend restart clears it. That's fine:
on restart, if the streak still exists, we'll set a new anchor on the
first call. The point is to prevent _refresh_ from resetting the timer,
not to make the lockout survive a deliberate restart.
"""

from datetime import datetime
from threading import Lock

_cache: dict[str, datetime] = {}
_lock = Lock()


def remember(key: str, candidate: datetime) -> datetime:
    """
    Return the cached cooldown_until for `key`. If none is cached, store
    `candidate` and return it. Subsequent calls return the same anchor.
    """
    with _lock:
        existing = _cache.get(key)
        if existing is None:
            _cache[key] = candidate
            return candidate
        return existing


def clear(key: str) -> None:
    """Drop the cached anchor for `key`. Called when the streak breaks."""
    with _lock:
        _cache.pop(key, None)


def expire_if_past(key: str, now: datetime) -> bool:
    """
    If the cached cooldown_until is in the past, drop it. Returns True if
    the entry was expired (and the caller should treat as unlocked), else
    False.
    """
    with _lock:
        v = _cache.get(key)
        if v is not None and now >= v:
            _cache.pop(key, None)
            return True
        return False
