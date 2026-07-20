"""
Daily premarket summary persistence — per-ticker rows only.

The row carries a *structured* catalyst evaluation produced by the LLM under
the Catalyst Value Equation rubric (docs/CATALYST_EVALUATION.md). The legacy
1-10 `catalyst_strength` field is replaced by Magnitude × Speed → Grade,
plus the suggested daily-risk sizing the rubric maps each grade to.

One table:

    daily_summary_row
      id              SERIAL PK
      run_date        DATE                   -- date of the snapshot
      created_at      TIMESTAMPTZ DEFAULT NOW()
      side            TEXT                   -- "up" or "down"
      rank            INT                    -- 1..5 within the side
      symbol          TEXT
      change          REAL                   -- % change at scan time, signed
      rvol            REAL                   -- relative volume at scan time
      catalyst_type   TEXT                   -- confirmed | coverage | narrative | none
      magnitude       TEXT                   -- Absolute | Yes | Maybe | No
      speed           TEXT                   -- Absolute | Yes | Maybe | No
      grade           TEXT                   -- A+ | A | B | C | D
      sizing_pct      INT                    -- 0..80, derived risk cap from grade
      reason          TEXT                   -- short "what's the catalyst" sentence
      notes           TEXT                   -- caveats (float, peer flow, in-price, etc.)
      headline        TEXT                   -- headline the eval came from
      news_url        TEXT                   -- link to that headline
      UNIQUE (run_date, side, rank)

Rerunning for an existing run_date is destructive: all rows for that date are
deleted first so we never end up with stale rows mixed in with new ones.

Schema migrations performed at startup (idempotent):
  1. Old two-table layout (daily_summary_run + daily_summary_row by run_id) →
     drop both, recreate the single-table shape below.
  2. Single-table shape with `catalyst_strength` (1-10 LLM score) → drop the
     whole table and recreate with the CVE columns. Historical snapshots are
     not migrated — the daily snapshot is cheap to regenerate.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Optional

import asyncpg


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

async def create_daily_summary_tables(db_conn: asyncpg.Connection) -> None:
    """Idempotent table + index creation. Called from main.py's lifespan.

    Two one-shot migrations run before the CREATE:
      1. Legacy two-table layout (daily_summary_run + daily_summary_row joined
         by run_id) → drop both.
      2. Single-table layout with a `catalyst_strength` INT column (the old
         1-10 LLM score) → drop the table so the CVE-shaped table can be
         created fresh. Old snapshots are not migrated; the catalyst snapshot
         is regenerated daily anyway.
    Both migrations are safe to run repeatedly — they fire only when the
    "old" thing is actually still present.
    """
    legacy_present = await db_conn.fetchval(
        "SELECT to_regclass('public.daily_summary_run') IS NOT NULL;"
    )
    if legacy_present:
        # Old shape used FK run_id; drop both legacy tables. CASCADE handles
        # the FK so the row table goes away cleanly with the run table.
        await db_conn.execute("DROP TABLE IF EXISTS daily_summary_row CASCADE;")
        await db_conn.execute("DROP TABLE IF EXISTS daily_summary_run CASCADE;")

    # Migration 2: if the table exists but still has the old catalyst_strength
    # column, drop it so the CVE shape below is created clean. Checking column
    # presence via information_schema keeps this idempotent across restarts.
    old_col_present = await db_conn.fetchval(
        """
        SELECT EXISTS(
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'daily_summary_row'
              AND column_name = 'catalyst_strength'
        );
        """
    )
    if old_col_present:
        await db_conn.execute("DROP TABLE IF EXISTS daily_summary_row CASCADE;")

    await db_conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_summary_row (
            id              SERIAL PRIMARY KEY,
            run_date        DATE NOT NULL,
            created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            side            TEXT NOT NULL,
            rank            INTEGER NOT NULL,
            symbol          TEXT NOT NULL,
            change          REAL,
            rvol            REAL,
            -- Catalyst Value Equation (CVE) fields. See docs/CATALYST_EVALUATION.md.
            -- All four CVE text columns use a constrained vocabulary, but we keep
            -- them as TEXT (not ENUM) so the rubric can evolve without a DB migration.
            catalyst_type   TEXT NOT NULL DEFAULT 'none',      -- confirmed|coverage|narrative|none
            magnitude       TEXT NOT NULL DEFAULT 'No',        -- Absolute|Yes|Maybe|No
            speed           TEXT NOT NULL DEFAULT 'No',        -- Absolute|Yes|Maybe|No
            grade           TEXT NOT NULL DEFAULT 'D',         -- A+|A|B|C|D
            sizing_pct      INTEGER NOT NULL DEFAULT 0,        -- 0..80, derived risk cap
            reason          TEXT NOT NULL DEFAULT '',
            notes           TEXT NOT NULL DEFAULT '',
            headline        TEXT NOT NULL DEFAULT '',
            news_url        TEXT NOT NULL DEFAULT '',
            UNIQUE (run_date, side, rank)
        );
        """
    )
    # History lookups: by date (latest day's snapshot) and by symbol (replay a
    # ticker's catalyst history over time).
    await db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_summary_row_run_date
            ON daily_summary_row(run_date DESC);
        """
    )
    await db_conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_daily_summary_row_symbol_date
            ON daily_summary_row(symbol, run_date DESC);
        """
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def upsert_daily_summary(
    db_conn: asyncpg.Connection,
    run_date: date,
    rows: List[Dict],
) -> None:
    """
    Replace today's snapshot atomically.

    Each row dict must carry: side ("up"|"down"), rank (int), symbol (str),
    change (float|None), rvol (float|None), catalyst_type (str), magnitude (str),
    speed (str), grade (str), sizing_pct (int), reason (str), notes (str),
    headline (str), news_url (str). Defaults below match the rubric's
    "every trade starts at D" bias: missing CVE fields collapse to a D grade
    with 0% sizing so a partial LLM response is never silently treated as a
    tradeable signal.
    """
    async with db_conn.transaction():
        # Wipe any existing snapshot for this date so rerunning is idempotent.
        await db_conn.execute(
            "DELETE FROM daily_summary_row WHERE run_date = $1;", run_date
        )
        if rows:
            await db_conn.executemany(
                """
                INSERT INTO daily_summary_row
                    (run_date, side, rank, symbol, change, rvol,
                     catalyst_type, magnitude, speed, grade, sizing_pct,
                     reason, notes, headline, news_url)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                        $12, $13, $14, $15);
                """,
                [
                    (
                        run_date,
                        r["side"],
                        r["rank"],
                        r["symbol"],
                        r.get("change"),
                        r.get("rvol"),
                        r.get("catalyst_type") or "none",
                        r.get("magnitude") or "No",
                        r.get("speed") or "No",
                        r.get("grade") or "D",
                        int(r.get("sizing_pct") or 0),
                        r.get("reason") or "",
                        r.get("notes") or "",
                        r.get("headline") or "",
                        r.get("news_url") or "",
                    )
                    for r in rows
                ],
            )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _row_to_dict(r) -> Dict:
    return {
        "run_date": r["run_date"],
        "side": r["side"],
        "rank": r["rank"],
        "symbol": r["symbol"],
        "change": r["change"],
        "rvol": r["rvol"],
        "catalyst_type": r["catalyst_type"] or "none",
        "magnitude": r["magnitude"] or "No",
        "speed": r["speed"] or "No",
        "grade": r["grade"] or "D",
        "sizing_pct": int(r["sizing_pct"] or 0),
        "reason": r["reason"] or "",
        "notes": r["notes"] or "",
        "headline": r["headline"] or "",
        "news_url": r["news_url"] or "",
    }


async def get_latest_daily_summary(
    db_conn: asyncpg.Connection,
) -> Optional[Dict]:
    """
    Return the most recent snapshot (latest run_date), or None if the table is
    empty. Shape: {run_date, created_at, rows: [...]}.
    """
    latest = await db_conn.fetchrow(
        """
        SELECT run_date, MIN(created_at) AS created_at
        FROM daily_summary_row
        WHERE run_date = (SELECT MAX(run_date) FROM daily_summary_row)
        GROUP BY run_date;
        """
    )
    if not latest:
        return None
    rows = await db_conn.fetch(
        """
        SELECT run_date, side, rank, symbol, change, rvol,
               catalyst_type, magnitude, speed, grade, sizing_pct,
               reason, notes, headline, news_url
        FROM daily_summary_row
        WHERE run_date = $1
        ORDER BY
            CASE side WHEN 'up' THEN 0 WHEN 'down' THEN 1 ELSE 2 END,
            rank ASC;
        """,
        latest["run_date"],
    )
    return {
        "run_date": latest["run_date"],
        "created_at": latest["created_at"],
        "rows": [_row_to_dict(r) for r in rows],
    }


async def get_daily_summary_by_date(
    db_conn: asyncpg.Connection,
    run_date: date,
) -> Optional[Dict]:
    """Fetch a specific date's snapshot. Same shape as get_latest_daily_summary."""
    rows = await db_conn.fetch(
        """
        SELECT run_date, created_at, side, rank, symbol, change, rvol,
               catalyst_type, magnitude, speed, grade, sizing_pct,
               reason, notes, headline, news_url
        FROM daily_summary_row
        WHERE run_date = $1
        ORDER BY
            CASE side WHEN 'up' THEN 0 WHEN 'down' THEN 1 ELSE 2 END,
            rank ASC;
        """,
        run_date,
    )
    if not rows:
        return None
    return {
        "run_date": run_date,
        "created_at": rows[0]["created_at"],
        "rows": [_row_to_dict(r) for r in rows],
    }


async def get_symbol_history(
    db_conn: asyncpg.Connection,
    symbol: str,
    limit: int = 60,
) -> List[Dict]:
    """
    Every catalyst entry recorded for a given ticker, newest first. Lets you go
    back later and ask "why was AAPL gapping on 2026-04-12?" without having to
    re-run anything. Returns a list of row dicts (each with its own run_date).
    """
    sym = (symbol or "").strip().upper()
    rows = await db_conn.fetch(
        """
        SELECT run_date, side, rank, symbol, change, rvol,
               catalyst_type, magnitude, speed, grade, sizing_pct,
               reason, notes, headline, news_url
        FROM daily_summary_row
        WHERE symbol = $1
        ORDER BY run_date DESC, rank ASC
        LIMIT $2;
        """,
        sym,
        limit,
    )
    return [_row_to_dict(r) for r in rows]
