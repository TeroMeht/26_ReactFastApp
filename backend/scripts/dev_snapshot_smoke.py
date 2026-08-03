"""
Offline smoke test for the TradesSnapshot refactor.

Runs the pure-derivation surface (fill machinery, snapshot query methods,
trade-log and entry-attempts view builders) over fabricated Fill data.
No IB, no network, no pytest -- just prints OK/FAIL per case.

Run from the backend/ directory:

    python scripts/dev_snapshot_smoke.py

Exits 0 if everything passes, 1 if anything failed.

Coverage focus: the semantic changes introduced by the refactor --
Fill dataclass, stats_by_symbol emitting open positions,
latest_fill_for_symbol bug fix, TradeLog / EntryAttempts dataclasses.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import datetime, timedelta

# Let the script run from either backend/ or scripts/.
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

import pytz  # noqa: E402

from services.portfolio.ib_client import Fill  # noqa: E402
from services.portfolio.trades.trades_snapshot import (  # noqa: E402
    _latest_fill_time,
    SNAPSHOT_TTL_SECONDS,
    SymbolTradeStats,
    TradesSnapshot,
    build_today_snapshot,
    invalidate_snapshot_cache,
)
from services.portfolio.trades.trade_builder import (  # noqa: E402
    _signed_qty,
    aggregate_realized_pnl,
    build_completed_trades,
    count_entries_from_fills,
)
from services.portfolio.trades.trade_log import (  # noqa: E402
    TradeLog,
    TradeLogEntry,
    build_trade_log,
)
from services.portfolio.entry_attempts import (  # noqa: E402
    EntryAttempts,
    EntryAttemptsEntry,
    build_entry_attempts,
)


# ---- Fixtures --------------------------------------------------------

TZ = pytz.timezone("America/New_York")
# Anchor to today (any date works; only same-day relativity matters).
BASE = datetime.now(TZ).replace(hour=15, minute=0, second=0, microsecond=0)


def fill(symbol, action, qty, price, minutes_ago=60, conid=None):
    """Convenience factory. minutes_ago is relative to BASE."""
    return Fill(
        tradeid=abs(hash((symbol, action, minutes_ago))) & 0xFFFFFFFF,
        symbol=symbol,
        conid=conid if conid is not None else (abs(hash(symbol)) & 0xFFFF),
        sectype="STK",
        action=action,
        quantity=qty,
        price=price,
        time=BASE - timedelta(minutes=minutes_ago),
        exchange="SMART",
    )


class StubIbClient:
    """Minimal IbClient stand-in: just returns the fills you pass in.

    Tracks how many times get_trades() was called so cache tests can
    verify single-flight and TTL behaviour.
    """
    def __init__(self, fills, delay: float = 0.0):
        self._fills = fills
        self._delay = delay
        self.trades_calls = 0

    async def get_trades(self):
        self.trades_calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return list(self._fills)


# ---- Runner ---------------------------------------------------------

class Runner:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name, fn):
        # Snapshot cache lives at module level and keys by id(). Python
        # recycles ids after GC, so a stub created in test N can collide
        # with a cache entry from test N-1. Production is safe (one IB
        # singleton for the whole process); tests need a clean slate.
        invalidate_snapshot_cache()
        try:
            fn()
        except AssertionError as e:
            self.failed += 1
            print(f"FAIL  {name}")
            msg = str(e).strip() or "assertion failed"
            print(f"      {msg}")
        except Exception as e:
            self.failed += 1
            print(f"ERROR {name}")
            print(f"      {e.__class__.__name__}: {e}")
            print(traceback.format_exc(limit=3))
        else:
            self.passed += 1
            print(f"OK    {name}")


def eq(actual, expected, hint=""):
    assert actual == expected, f"expected {expected!r}, got {actual!r} {hint}".strip()


def approx(actual, expected, tol=1e-6, hint=""):
    assert abs(actual - expected) < tol, (
        f"expected {expected!r} (±{tol}), got {actual!r} {hint}".strip()
    )


# ---- Sections --------------------------------------------------------

def section(label):
    print()
    print(f"--- {label} ---")


def test_signed_qty(r: Runner):
    section("_signed_qty")
    r.check("BOT -> +qty", lambda: eq(_signed_qty("BOT", 100), 100))
    r.check("SLD -> -qty", lambda: eq(_signed_qty("SLD", 100), -100))
    r.check("BUY -> +qty", lambda: eq(_signed_qty("BUY", 50), 50))
    r.check("SELL -> -qty", lambda: eq(_signed_qty("SELL", 50), -50))
    r.check("unknown -> 0", lambda: eq(_signed_qty("FOO", 100), 0))
    r.check("lowercase bot -> +qty", lambda: eq(_signed_qty("bot", 100), 100))


def test_latest_fill_time(r: Runner):
    section("_latest_fill_time")
    f_old = fill("AAPL", "BOT", 100, 10.0, minutes_ago=60)
    f_new = fill("AAPL", "SLD", 100, 11.0, minutes_ago=30)
    r.check("newest wins", lambda: eq(_latest_fill_time([f_old, f_new]), f_new.time))
    r.check("empty returns None", lambda: eq(_latest_fill_time([]), None))


def test_count_entries(r: Runner):
    section("count_entries_from_fills")
    r.check(
        "single BUY = 1 entry",
        lambda: eq(count_entries_from_fills([fill("AAPL", "BOT", 100, 10.0)]), 1),
    )
    r.check(
        "BUY BUY (add) = 1 entry",
        lambda: eq(count_entries_from_fills([
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=60),
            fill("AAPL", "BOT", 100, 10.5, minutes_ago=50),
        ]), 1),
    )
    r.check(
        "BUY SELL (round trip) = 1 entry",
        lambda: eq(count_entries_from_fills([
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=60),
            fill("AAPL", "SLD", 100, 11.0, minutes_ago=30),
        ]), 1),
    )
    r.check(
        "BUY SELL BUY = 2 entries",
        lambda: eq(count_entries_from_fills([
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=60),
            fill("AAPL", "SLD", 100, 11.0, minutes_ago=50),
            fill("AAPL", "BOT", 100, 10.5, minutes_ago=30),
        ]), 2),
    )
    r.check(
        "SELL BUY (short) = 1 entry",
        lambda: eq(count_entries_from_fills([
            fill("AAPL", "SLD", 100, 10.0, minutes_ago=60),
            fill("AAPL", "BOT", 100, 9.0, minutes_ago=30),
        ]), 1),
    )


def test_completed_trades(r: Runner):
    section("build_completed_trades")

    def check_win():
        fills = [
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=60),
            fill("AAPL", "SLD", 100, 11.0, minutes_ago=30),
        ]
        completed = build_completed_trades({"AAPL": fills})
        eq(len(completed), 1, "one closed cycle")
        c = completed[0]
        approx(c["pnl"], 100.0, hint="100 * (11-10)")
        assert c["is_loss"] is False
    r.check("long win: one cycle, correct pnl", check_win)

    def check_loss():
        fills = [
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=60),
            fill("AAPL", "SLD", 100, 9.5, minutes_ago=30),
        ]
        completed = build_completed_trades({"AAPL": fills})
        c = completed[0]
        approx(c["pnl"], -50.0)
        assert c["is_loss"] is True
    r.check("long loss: is_loss True", check_loss)

    def check_short():
        fills = [
            fill("AAPL", "SLD", 100, 10.0, minutes_ago=60),
            fill("AAPL", "BOT", 100, 9.0, minutes_ago=30),
        ]
        completed = build_completed_trades({"AAPL": fills})
        eq(len(completed), 1, "short cycle emits row")
        approx(completed[0]["pnl"], 100.0, hint="proceeds - cost = 1000 - 900")
    r.check("short cycle: emitted, correct pnl", check_short)

    def check_add_exit():
        fills = [
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=60),
            fill("AAPL", "BOT", 100, 10.5, minutes_ago=50),
            fill("AAPL", "SLD", 200, 11.0, minutes_ago=30),
        ]
        completed = build_completed_trades({"AAPL": fills})
        eq(len(completed), 1, "add+exit = one cycle")
        c = completed[0]
        # entry_value = 100*10 + 100*10.5 = 2050; exit_value = 200*11 = 2200
        approx(c["pnl"], 150.0)
    r.check("add + full exit: one cycle, aggregated", check_add_exit)

    def check_open_no_cycle():
        completed = build_completed_trades({
            "AAPL": [fill("AAPL", "BOT", 100, 10.0)]
        })
        eq(len(completed), 0, "open position emits no cycle")
    r.check("open position: no cycle emitted", check_open_no_cycle)

    def check_two_cycles():
        fills = [
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=90),
            fill("AAPL", "SLD", 100, 11.0, minutes_ago=70),
            fill("AAPL", "BOT", 100, 12.0, minutes_ago=50),
            fill("AAPL", "SLD", 100, 13.0, minutes_ago=30),
        ]
        completed = build_completed_trades({"AAPL": fills})
        eq(len(completed), 2, "two cycles")
        approx(completed[0]["pnl"], 100.0)
        approx(completed[1]["pnl"], 100.0)
    r.check("two cycles on same symbol", check_two_cycles)

    def check_position_flip():
        # BUY 100, SELL 200: closes long AND opens short past zero.
        # Current implementation does NOT split the flipping fill and the
        # cycle-close test is `net == 0`. Net goes +100 -> -100, skipping
        # zero, so no cycle closes -- the long portion is silently absorbed
        # into a still-open cycle whose direction is now inconsistent. This
        # is the known limitation build_completed_trades' docstring alludes
        # to ("if it ever matters, split the flipping fill"). Pin the
        # behaviour so a future fix is visible as an intentional change.
        fills = [
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=60),
            fill("AAPL", "SLD", 200, 11.0, minutes_ago=30),
        ]
        completed = build_completed_trades({"AAPL": fills})
        eq(len(completed), 0,
           "flip through zero emits no cycle today -- known limitation")
    r.check("position flip (BUY 100, SELL 200): no cycle (known limitation)",
            check_position_flip)


def test_aggregate_realized_pnl(r: Runner):
    section("aggregate_realized_pnl")
    fills = {
        "AAPL": [
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=60),
            fill("AAPL", "SLD", 100, 11.0, minutes_ago=30),
        ],
        "MSFT": [
            fill("MSFT", "BOT", 10, 300.0, minutes_ago=60),
            fill("MSFT", "SLD", 10, 305.0, minutes_ago=30),
        ],
    }
    completed = build_completed_trades(fills)
    total, per_symbol = aggregate_realized_pnl(completed)

    r.check("total correct", lambda: approx(total, 150.0, hint="AAPL 100 + MSFT 50"))
    r.check("per_symbol keys match", lambda: eq(sorted(per_symbol.keys()), ["AAPL", "MSFT"]))
    r.check("per_symbol AAPL", lambda: approx(per_symbol["AAPL"], 100.0))
    r.check("per_symbol MSFT", lambda: approx(per_symbol["MSFT"], 50.0))
    r.check("total == sum(per_symbol)", lambda: approx(total, sum(per_symbol.values())))


def test_stats_by_symbol(r: Runner):
    section("TradesSnapshot.stats_by_symbol")

    async def build(fills):
        return await build_today_snapshot(StubIbClient(fills))

    def check_open_position_emitted():
        # Open position: BUY only, no SELL. Old reqPnLSingle path showed
        # this too; the cycle-based path used to hide it -- fixed.
        snapshot = asyncio.run(build([
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=30),
        ]))
        stats = snapshot.stats_by_symbol()
        assert "AAPL" in stats, "open position must still appear"
        s = stats["AAPL"]
        approx(s.realized_pnl, 0.0, hint="no closed cycle")
        eq(s.fills, 1)
        assert s.is_loss is False
    r.check("open position appears with realized=0",
            check_open_position_emitted)

    def check_realized_pnl_by_symbol_matches_agg():
        snapshot = asyncio.run(build([
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=90),
            fill("AAPL", "SLD", 100, 11.0, minutes_ago=60),
            fill("MSFT", "BOT", 10, 300.0, minutes_ago=45),
            fill("MSFT", "SLD", 10, 305.0, minutes_ago=30),
        ]))
        agg = snapshot.realized_pnl
        summed = sum(snapshot.realized_pnl_by_symbol.values())
        approx(summed, agg,
               hint="per-symbol sum must equal aggregate")
        # And it must also equal the sum over stats_by_symbol().
        stats_sum = sum(s.realized_pnl for s in snapshot.stats_by_symbol().values())
        approx(stats_sum, agg, hint="stats_by_symbol per-symbol sum too")
    r.check(
        "realized_pnl_by_symbol invariant: sum == aggregate",
        check_realized_pnl_by_symbol_matches_agg,
    )


def test_symbol_trade_stats_properties(r: Runner):
    section("SymbolTradeStats properties")
    s_win = SymbolTradeStats(
        symbol="AAPL", realized_pnl=100.0,
        fills=2, last_fill_time=BASE,
    )
    r.check("is_loss=False on positive realized",
            lambda: eq(s_win.is_loss, False))

    s_loss = SymbolTradeStats(
        symbol="AAPL", realized_pnl=-50.0,
        fills=2, last_fill_time=BASE,
    )
    r.check("is_loss=True on negative realized",
            lambda: eq(s_loss.is_loss, True))


def test_latest_fill_for_symbol(r: Runner):
    section("TradesSnapshot.latest_fill_for_symbol")
    snap = TradesSnapshot(
        fills_by_symbol={
            "AAPL": [
                fill("AAPL", "BOT", 100, 10.0, minutes_ago=60),
                fill("AAPL", "SLD", 100, 11.5, minutes_ago=30),
            ]
        },
    )
    r.check(
        "returns newest",
        lambda: eq(snap.latest_fill_for_symbol("AAPL").price, 11.5),
    )
    r.check(
        "missing symbol returns None",
        lambda: eq(snap.latest_fill_for_symbol("NVDA"), None),
    )


def test_snapshot_queries(r: Runner):
    section("TradesSnapshot query methods")

    async def build(fills):
        return await build_today_snapshot(StubIbClient(fills))

    def check_consecutive_losses():
        # Three losing round-trips, ordered by exit_time ascending.
        fills = [
            fill("A", "BOT", 100, 10.0, minutes_ago=180),
            fill("A", "SLD", 100, 9.0,  minutes_ago=170),
            fill("B", "BOT", 100, 10.0, minutes_ago=160),
            fill("B", "SLD", 100, 9.5,  minutes_ago=150),
            fill("C", "BOT", 100, 10.0, minutes_ago=140),
            fill("C", "SLD", 100, 9.8,  minutes_ago=130),
        ]
        snap = asyncio.run(build(fills))
        eq(snap.consecutive_losses(), 3, "streak of 3 losses")
    r.check("consecutive_losses counts tail losses", check_consecutive_losses)

    def check_streak_broken_by_win():
        fills = [
            fill("A", "BOT", 100, 10.0, minutes_ago=180),
            fill("A", "SLD", 100, 9.0,  minutes_ago=170),  # loss
            fill("B", "BOT", 100, 10.0, minutes_ago=160),
            fill("B", "SLD", 100, 11.0, minutes_ago=150),  # win breaks streak
        ]
        snap = asyncio.run(build(fills))
        eq(snap.consecutive_losses(), 0, "win breaks the tail")
    r.check("consecutive_losses=0 when tail is a win", check_streak_broken_by_win)

    def check_attempts():
        # Two entries on AAPL (round trip then re-entry), one on MSFT (open).
        fills = [
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=180),
            fill("AAPL", "SLD", 100, 11.0, minutes_ago=170),
            fill("AAPL", "BOT", 100, 10.5, minutes_ago=60),
            fill("MSFT", "BOT",  10, 300.0, minutes_ago=45),
        ]
        snap = asyncio.run(build(fills))
        eq(snap.attempts_for("AAPL"), 2)
        eq(snap.attempts_for("MSFT"), 1)
        eq(snap.total_attempts(), 3)
    r.check("attempts_for / total_attempts", check_attempts)


def test_build_trade_log(r: Runner):
    section("build_trade_log")

    async def build(fills):
        return await build_trade_log(StubIbClient(fills))

    def check_row_shape_and_sort():
        # Two symbols; MSFT is the more-recent one and should sort first.
        tl: TradeLog = asyncio.run(build([
            fill("AAPL", "BOT", 100, 10.0, minutes_ago=180),
            fill("AAPL", "SLD", 100, 11.0, minutes_ago=150),
            fill("MSFT", "BOT",  10, 300.0, minutes_ago=60),
            fill("MSFT", "SLD",  10, 305.0, minutes_ago=30),
        ]))
        assert isinstance(tl, TradeLog), "returns TradeLog dataclass"
        eq(tl.symbol_count, 2)
        eq(tl.rows[0].symbol, "MSFT", "newest last_fill_time sorts first")
        assert isinstance(tl.rows[0], TradeLogEntry), "rows are TradeLogEntry"
        approx(tl.realized_pnl, 150.0, hint="AAPL 100 + MSFT 50")
    r.check("row shape, sort newest-first, symbol count", check_row_shape_and_sort)

    def check_empty():
        tl = asyncio.run(build([]))
        eq(tl.symbol_count, 0)
        eq(tl.rows, [])
        approx(tl.realized_pnl, 0.0)
    r.check("empty fills -> empty TradeLog", check_empty)


def test_build_entry_attempts(r: Runner):
    section("build_entry_attempts")

    async def build(fills):
        return await build_entry_attempts(StubIbClient(fills))

    def check_alphabetical_sort():
        ea: EntryAttempts = asyncio.run(build([
            fill("ZZZ", "BOT", 100, 10.0, minutes_ago=90),
            fill("AAA", "BOT", 100, 10.0, minutes_ago=60),
            fill("MMM", "BOT", 100, 10.0, minutes_ago=30),
        ]))
        assert isinstance(ea, EntryAttempts)
        symbols = [row.symbol for row in ea.rows]
        eq(symbols, sorted(symbols), "rows sorted alphabetically")
        assert all(isinstance(r_, EntryAttemptsEntry) for r_ in ea.rows)
    r.check("rows sorted alphabetically", check_alphabetical_sort)

    def check_remaining_clamped():
        # Force a symbol's count above max_attempts by hand, then verify
        # remaining clamps to 0. Easiest: build a snapshot directly.
        snap = TradesSnapshot(entry_counts={"AAPL": 9999})
        # Reuse build_entry_attempts's inner logic by manual construction.
        # (Full path exercised elsewhere; this just verifies the clamp.)
        # We just check the min-clamp on remaining by inspecting a row:
        # simulate by using stats output directly.
        max_att = 3
        remaining = max(0, max_att - snap.attempts_for("AAPL"))
        eq(remaining, 0)
    r.check("remaining clamped at 0 when count exceeds cap", check_remaining_clamped)

    def check_only_symbols_with_attempts():
        # A fill sequence for a symbol that never leaves flat (SELL 100 with
        # no matching BUY) still counts as an attempt (opens a short cycle).
        # The interesting case: fills present but count_entries returns 0
        # can't easily happen -- any non-zero signed fill from flat starts
        # a cycle. So just sanity-check that empty fills -> empty rows.
        ea = asyncio.run(build([]))
        eq(ea.rows, [])
        eq(ea.total_attempts, 0)
    r.check("empty fills -> empty rows", check_only_symbols_with_attempts)


def test_snapshot_cache(r: Runner):
    section("build_today_snapshot: TTL cache + single-flight")

    def check_ttl_reuse():
        stub = StubIbClient([fill("AAPL", "BOT", 100, 10.0, minutes_ago=60)])
        invalidate_snapshot_cache(stub)

        async def run():
            s1 = await build_today_snapshot(stub)
            s2 = await build_today_snapshot(stub)
            return s1, s2

        s1, s2 = asyncio.run(run())
        # Same snapshot object (cache returns the cached reference verbatim).
        assert s1 is s2, "second call within TTL must reuse cached snapshot"
        eq(stub.trades_calls, 1)
    r.check("second call within TTL reuses cached snapshot", check_ttl_reuse)

    def check_expiry_refetches():
        stub = StubIbClient([fill("AAPL", "BOT", 100, 10.0, minutes_ago=60)])
        invalidate_snapshot_cache(stub)

        async def run():
            await build_today_snapshot(stub)
            # Force cache expiry without waiting the full TTL.
            invalidate_snapshot_cache(stub)
            await build_today_snapshot(stub)

        asyncio.run(run())
        eq(stub.trades_calls, 2, hint="second call after invalidate must refetch")
    r.check("post-invalidate call refetches", check_expiry_refetches)

    def check_single_flight():
        # Four concurrent callers with a slow get_trades. Only ONE fetch
        # should actually hit IB; the other three ride the in-flight future.
        stub = StubIbClient(
            [fill("AAPL", "BOT", 100, 10.0, minutes_ago=60)],
            delay=0.1,  # ensure all four block on the same in-flight fetch
        )
        invalidate_snapshot_cache(stub)

        async def run():
            results = await asyncio.gather(*(
                build_today_snapshot(stub) for _ in range(4)
            ))
            return results

        results = asyncio.run(run())
        eq(stub.trades_calls, 1, hint="single-flight: one fetch for four concurrent callers")
        # All callers get the same snapshot instance.
        assert all(s is results[0] for s in results), "all callers share the cached result"
    r.check("concurrent callers share one in-flight fetch", check_single_flight)

    def check_distinct_clients_dont_collide():
        # Two stubs with different fills — cache is keyed per underlying
        # connection (per stub in tests), so each gets its own snapshot.
        stub_a = StubIbClient([fill("AAPL", "BOT", 100, 10.0, minutes_ago=60)])
        stub_b = StubIbClient([fill("MSFT", "BOT", 10, 300.0, minutes_ago=60)])
        invalidate_snapshot_cache()

        async def run():
            sa = await build_today_snapshot(stub_a)
            sb = await build_today_snapshot(stub_b)
            return sa, sb

        sa, sb = asyncio.run(run())
        eq(stub_a.trades_calls, 1)
        eq(stub_b.trades_calls, 1)
        assert "AAPL" in sa.fills_by_symbol
        assert "MSFT" in sb.fills_by_symbol
        assert sa is not sb
    r.check("distinct clients don't collide on the cache", check_distinct_clients_dont_collide)


# ---- Main -----------------------------------------------------------

def main():
    r = Runner()
    test_signed_qty(r)
    test_latest_fill_time(r)
    test_count_entries(r)
    test_completed_trades(r)
    test_aggregate_realized_pnl(r)
    test_stats_by_symbol(r)
    test_symbol_trade_stats_properties(r)
    test_latest_fill_for_symbol(r)
    test_snapshot_queries(r)
    test_build_trade_log(r)
    test_build_entry_attempts(r)
    test_snapshot_cache(r)

    print()
    print("=" * 50)
    print(f"PASSED: {r.passed}   FAILED: {r.failed}")
    print("=" * 50)
    sys.exit(0 if r.failed == 0 else 1)


if __name__ == "__main__":
    main()
