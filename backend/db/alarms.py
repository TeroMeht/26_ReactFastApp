from typing import List, Dict
import asyncpg



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