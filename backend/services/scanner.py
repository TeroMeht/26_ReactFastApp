from helpers.scanner_presets import SCANNER_PRESETS
from schemas.api_schemas import ScannerResponse
from ib_async import IB,ScannerSubscription,ScanData,Contract,Stock
from typing import List,Dict,DefaultDict
import asyncio
import pandas as pd
from datetime import date
import logging
import numpy as np
logger = logging.getLogger(__name__)


from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

@dataclass
class IncomingBar:
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    average: Optional[float] = None
    barCount: Optional[int] = None

def handle_incoming_bars_intraday(bars: List[IncomingBar], symbol: str, time_zone: str = "Europe/Helsinki") -> List[dict]:

    tz = ZoneInfo(time_zone)
    
    return [
        {
            "symbol": symbol,
            "date": bar.date.astimezone(tz).date().isoformat(),
            "time": bar.date.astimezone(tz).time().isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume
        }
        for bar in bars
    ]

# Tällä haetaan ankkurihinta changen laskemiselle kun markkina ei vielä ole auki
def get_yesterday_closingprices(past5days) -> List[Dict[str, str]]:
    result = []

    # Iterate through each symbol in past5days
    for symbol, bars in past5days.items():
        # Filter out bars where Time is '23:00:00'
        relevant_bars = [bar for bar in bars if bar.get('time') == '22:58:00']
        
        if relevant_bars:
            # Find the most recent date by comparing the Date values
            most_recent_bar = max(relevant_bars, key=lambda b: datetime.strptime(b['date'], '%Y-%m-%d').date())

            result.append({
                'symbol': most_recent_bar.get('symbol'),
                'date': most_recent_bar.get('date'),
                'time': most_recent_bar.get('time'),
                'close': most_recent_bar.get('close')
            })

    return result

def get_todays_closingprices(today_bars1) -> List[Dict[str, str]]:
    result = []
    today = datetime.today().date()

    # Iterate through each symbol in today's bars
    for symbol, bars in today_bars1.items():
        # Filter bars where Time is '16:30:00' and Date is today
        relevant_bars = [bar for bar in bars if bar.get('time') == '16:30:00' and datetime.strptime(bar.get('date'), '%Y-%m-%d').date() == today]
        
        if relevant_bars:
            # Since we are filtering by '16:30:00', we can just take the first (or only) bar
            most_recent_bar = relevant_bars[0]

            result.append({
                'symbol': most_recent_bar.get('symbol'),
                'date': most_recent_bar.get('date'),
                'time': most_recent_bar.get('time'),
                'close': most_recent_bar.get('close')
            })

    return result


# Calculations

def calculate_percentage_change(rvol_df: pd.DataFrame, close_prices: List[Dict[str, str]]) -> pd.DataFrame:
    # Convert the close_prices list to a DataFrame
    close_prices_df = pd.DataFrame(close_prices)

    # Merge the rvol_df with the close_prices_df based on 'Symbol'
    merged_df = pd.merge(rvol_df, close_prices_df, on='symbol', how='left')
    # Rename columns to remove the '_x' suffix and use the correct names
    merged_df.rename(columns={
        'date_x': 'date',    # Rename 'Date_x' to 'Date'
        'time_x': 'time',    # Rename 'Time_x' to 'Time'
        'close_x': 'close'   # Rename 'Close_x' to 'Close'
    }, inplace=True)

    # Calculate the percentage change in close prices
    merged_df['change'] = ((merged_df['close'] - merged_df['close_y']) / merged_df['close_y']) * 100

    # Round the percentage change to 2 decimal places
    merged_df['change'] = merged_df['change'].round(2)


    return merged_df

def calculate_avg_volume_model(grouped_dataset: Dict[str, List[dict]]) -> pd.DataFrame:

    all_data = []

    # Flatten the grouped dataset into a single list of dicts
    for symbol, bars in grouped_dataset.items():
        all_data.extend(bars)

    df = pd.DataFrame(all_data)

    # Compute average volume per Symbol per Time
    avg_volume_df = (
        df.groupby(['symbol', 'time'], as_index=False)['volume']
        .mean()
        .rename(columns={'volume': 'avgvolume'})
    )
        # Round Avg_volume to 2 decimal places
    avg_volume_df['avgvolume'] = avg_volume_df['avgvolume'].round(2)
    
    return avg_volume_df

def calculate_rvol(today_df: pd.DataFrame, avg_volume_df: pd.DataFrame) -> pd.DataFrame:

    # Merge today's bars with historical average volume
    df = today_df.merge(avg_volume_df, on=['symbol', 'time'], how='left')

    # Prepare empty list for processed data
    processed_list = []

    # Calculate cumulative sums and Rvol per symbol
    for symbol, group in df.groupby('symbol', sort=False):
        group = group.sort_values('time').copy()  # ensure proper time order
        group['cumvolume'] = group['volume'].cumsum()
        group['cumavgvolume'] = group['avgvolume'].cumsum()
        group['rvol'] = np.where(
            (group['cumavgvolume'] == 0) | group['cumavgvolume'].isna(),
            0.0,
            group['cumvolume'] / group['cumavgvolume']
        )
        group['rvol'] = group['rvol'].round(2)
        processed_list.append(group)

    # Combine all symbols back
    result_df = pd.concat(processed_list, ignore_index=True)
    return result_df


async def fetch_intraday_data(ib: IB, symbol: str):

    logger.info(f"Requesting 5days intraday data for {symbol}")

    # Create contract inline
    contract = Stock(symbol, "SMART", "USD")
    
    # Qualify the contract (blocking is usually fine once)
    await ib.qualifyContractsAsync(contract)

    # Async historical request
    bars = await ib.reqHistoricalDataAsync(
        contract,
        endDateTime="",
        durationStr="5 D",
        barSizeSetting="2 mins",
        whatToShow="TRADES",
        useRTH=False
    )
    await asyncio.sleep(2)

    if not bars:
        logger.warning(f"No 5-day historical data returned for {symbol}")
        return None
    
    processed_bars = handle_incoming_bars_intraday(bars,symbol)

    return processed_bars



async def scan_datapipeline(scan_data: List[ScanData], ib: IB) -> List[dict]:

    # Step 1 & 2: Build dataset and fetch intraday bars concurrently
    dataset = [
        {
            "rank": item.rank,
            "symbol": item.contractDetails.contract.symbol,
            "contract": str(item.contractDetails.contract.conId)
        }
        for item in scan_data
    ]

    # Fetch all 5day intrabars concurrently
    intraday_results = await asyncio.gather(
        *(fetch_intraday_data(ib, row["symbol"]) for row in dataset),
        return_exceptions=True
    )


    # Step 3: Merge intraday bars and log
    for row, bars in zip(dataset, intraday_results):
        symbol = row["symbol"]
        if isinstance(bars, Exception):
            logger.error(f"Error fetching intraday bars for {symbol}: {bars}")
            row["intraday_bars"] = None
        else:
            row["intraday_bars"] = bars

    return dataset

async def compute_datapipeline(dataset: List[dict]) -> List[ScannerResponse]:
    
    today_str = date.today().strftime("%Y-%m-%d")
    # Step 1: Group rows by symbol
    symbol_groups = DefaultDict(list)
    for row in dataset:
        symbol = row.get("symbol")
        intraday_bars = row.get("intraday_bars") or []
        symbol_groups[symbol].extend(intraday_bars)

    today_bars1 = DefaultDict(list)         # today's bars grouped by symbol
    past5day_bars = DefaultDict(list)   # historical bars grouped by symbol

    # Step 2: Process each symbol individually
    for symbol, bars in symbol_groups.items():
        # Sort bars by Date + Time
        bars_sorted = sorted(bars, key=lambda b: (b.get("date"), b.get("time")))

        # Separate today's bars and historical bars
        today_bars = [b for b in bars_sorted if b.get("date") == today_str]
        hist_bars = [b for b in bars_sorted if b.get("date") != today_str]

        today_bars1[symbol].extend(today_bars)
        past5day_bars[symbol].extend(hist_bars)

        # Step 2a: Flatten today's bars into a DataFrame
        today_data_list = []
        for symbol, bars in today_bars1.items():
            for bar in bars:
                today_data_list.append({
                    "symbol": bar.get("symbol"),
                    "date": bar.get("date"),
                    "time": bar.get("time"),
                    "open": bar.get("open"),
                    "high": bar.get("high"),
                    "low": bar.get("low"),
                    "close": bar.get("close"),
                    "volume": bar.get("volume")
                })

        today_df = pd.DataFrame(today_data_list)

# Filter out timestamps before 11:00
        today_df['time'] = pd.to_datetime(today_df['time'], format='%H:%M:%S').dt.time
        today_df = today_df[today_df['time'] >= pd.to_datetime('11:00', format='%H:%M').time()]

        # Step 3: Calculate average volume using historical data
        avg_volume_df = calculate_avg_volume_model(grouped_dataset=past5day_bars)

# Filter out timestamps before 11:00
        avg_volume_df['time'] = pd.to_datetime(avg_volume_df['time'], format='%H:%M:%S').dt.time
        avg_volume_df = avg_volume_df[avg_volume_df['time'] >= pd.to_datetime('11:00', format='%H:%M').time()]

        # Step 4: Calculate RVol for today's bars
        today_with_rvol_df = calculate_rvol(today_df, avg_volume_df)

        #logger.info(today_with_rvol_df)

        # Riippuen siitä mihin aikaan päivästä skannaus ajetaan
        current_time = datetime.now()

        # Check if it's before or after 16:30 today
        if current_time.hour < 16 or (current_time.hour == 16 and current_time.minute < 30):
            # Before 16:30, use yesterday's closing prices
            close_prices = get_yesterday_closingprices(past5day_bars)
        else:
            # After 16:30, use today's closing prices at 16:30
            close_prices = get_todays_closingprices(today_bars1)

        # Print the results using logger
        for res in close_prices:
 
            logger.info(f"Symbol: {res['symbol']}, Date: {res['date']}, Time: {res['time']}, Close: {res['close']}")

        change_df = calculate_percentage_change(today_with_rvol_df,close_prices)
        # Group by 'Symbol' and take the last row for each group
        last_rows = change_df.groupby('symbol').last().reset_index()

        # Convert the last rows into a list of dictionaries
        last_rows_dict = last_rows.to_dict(orient='records')

        # Palauttaa viimeisimmän rivin jokaiselle symbolille 

    return last_rows_dict





async def run_scanner_logic(preset_name: str, ib: IB) -> List[dict]:
    """
    Fetch scanner data from IB asynchronously, then push it to the data pipeline.
    """
    preset = SCANNER_PRESETS.get(preset_name)
    if not preset:
        raise ValueError(
            f"Invalid preset_name. Available presets: {list(SCANNER_PRESETS.keys())}"
        )

    sub = ScannerSubscription(**preset)

    # Fully async IB request
    scan_data: List[ScanData] = await ib.reqScannerDataAsync(sub)  # Scan the market
    if not scan_data:
        return []
    
    # Push scan data to pipeline and await results
    data_from_scanning = await scan_datapipeline(scan_data,ib)
    final_data = await compute_datapipeline(data_from_scanning)


    return final_data