from typing import List, Dict
from db.livestream import *
import logging
from schemas.api_schemas import CandleRow

logger = logging.getLogger(__name__)




# ---------------- DB Fetch ----------------
async def fetch_latest_from_db(db_conn) -> List[CandleRow]:

    tables = await fetch_tables(db_conn, prefix= "livestream")

    latest_rows = []
    for table in tables:
        row = await fetch_last_row(db_conn,table)
        latest_rows.append(row)

    return latest_rows


async def fetch_pricedata_from_db(db_conn, symbol:str) -> List[Dict]:

    table_name = f"{symbol.lower()}_livestream" 
    pricedata = await fetch_pricedata_by_symbol(db_conn, table_name, symbol)

        # Ensure we always return a list, even if empty
    if not pricedata:
        return []  # No data found for this symbol
    return pricedata