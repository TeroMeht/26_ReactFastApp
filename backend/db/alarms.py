from typing import List, Dict
import asyncpg



async def fetch_alarms(db_conn:asyncpg.Connection) -> List[Dict]:
    rows = await db_conn.fetch(
        """
        SELECT "Id", "Symbol", "Time", "Alarm", "Date"
        FROM alarms
        ORDER BY "Date" DESC, "Time" DESC
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
        INSERT INTO alarms ("Symbol", "Time", "Alarm", "Date")
        VALUES ($1, $2, $3, $4)
        RETURNING "Id", "Symbol", "Time", "Alarm", "Date";
        """,
        alarm["Symbol"],
        alarm["Time"],
        alarm["Alarm"],
        alarm["Date"]
    )
    return dict(row)