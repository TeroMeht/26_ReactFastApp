from typing import List, Dict
import asyncpg


async def create_alarms_table(db_conn: asyncpg.Connection) -> None:
    """Create the 'alarms' table used to store price/time alarms.

    Idempotent: safe to run on every boot. Columns match the SELECT/INSERT
    in fetch_alarms/insert_alarm and the AlarmResponse schema
    (id, symbol, time, alarm, date).
    """
    await db_conn.execute("""
        CREATE TABLE IF NOT EXISTS alarms (
            id      BIGSERIAL PRIMARY KEY,
            symbol  TEXT NOT NULL,
            time    TIME NOT NULL,
            alarm   TEXT NOT NULL,
            date    DATE NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_alarms_date_time
            ON alarms (date DESC, time DESC);
    """)


async def fetch_alarms(db_conn:asyncpg.Connection) -> List[Dict]:
    rows = await db_conn.fetch(
        """
        SELECT "id", "symbol", "time", "alarm", "date"
        FROM alarms
        ORDER BY "date" DESC, "time" DESC
        LIMIT 50;
        """
    )

    # Convert asyncpg Record to dict
    return [dict(row) for row in rows]


async def insert_alarm(db_conn:asyncpg.Connection, alarm: Dict):
    """
    Insert a new alarm into the database and return the new row.
    """
    row = await db_conn.fetchrow(
        """
        INSERT INTO alarms ("symbol", "time", "alarm", "date")
        VALUES ($1, $2, $3, $4)
        RETURNING "id", "symbol", "time", "alarm", "date";
        """,
        alarm["symbol"],
        alarm["time"],
        alarm["alarm"],
        alarm["date"]
    )
    return dict(row)