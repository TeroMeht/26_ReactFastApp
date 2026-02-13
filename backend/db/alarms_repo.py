from typing import List, Dict
import asyncpg

class AlarmRepository:
    def __init__(self, conn: asyncpg.Connection):
        self.conn = conn

    async def fetch_alarms(self) -> List[Dict]:
        rows = await self.conn.fetch(
            """
            SELECT "Id", "Symbol", "Time", "Alarm", "Date"
            FROM alarms
            ORDER BY "Date" DESC, "Time" DESC
            LIMIT 50;
            """
        )

        # Convert asyncpg Record to dict
        return [dict(row) for row in rows]


    async def insert_alarm(self, alarm: Dict):
        """
        Insert a new alarm into the database and return the new row.
        """
        row = await self.conn.fetchrow(
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