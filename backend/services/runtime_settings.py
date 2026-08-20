"""Runtime-derived toggles for the entry flow.

Currently just one flag:

    extended_hours_stop_enabled -- when True, `place_bracket_order`
    swaps the native STP protective leg for a conditional LMT so the
    stop still fires during pre- and after-hours sessions.

Not user-configurable: the value is derived from the current Helsinki
wall-clock time. Before the US-market open (16:30 Europe/Helsinki, which
is 09:30 America/New_York in either DST regime) we are in premarket and
want the extended-hours-capable stop; from 16:30 onward we fall back
to the native STP that IB only honours during RTH.

Kept as a function rather than a constant so callers always see the
value that matches "right now" -- the boundary is crossed live and
we do not want a stale snapshot from process startup.
"""

from datetime import datetime, time

import pytz

from core.config import settings


_TZ = pytz.timezone(settings.TIMEZONE)

# US regular-hours open in local wall time. With TIMEZONE=Europe/Helsinki
# both EET/EEST and America/New_York (EST/EDT) observe DST on similar
# schedules, so the open lands at 16:30 in both regimes with only a
# brief spring-transition mismatch each year.
_PREMARKET_END = time(16, 30)


def get_extended_hours_stop_enabled() -> bool:
    """True while local wall time is before the US-market open."""
    now = datetime.now(_TZ).time()
    return now < _PREMARKET_END
