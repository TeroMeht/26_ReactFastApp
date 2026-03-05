from helpers.scanner_presets import SCANNER_PRESETS
from schemas.api_schemas import ScannerResponse
from ib_async import IB,ScannerSubscription,ScanData,Contract,Stock
from typing import List,Dict,DefaultDict,Tuple
from collections import defaultdict
import asyncio
import pandas as pd

import logging
import numpy as np
logger = logging.getLogger(__name__)


from dataclasses import dataclass
from datetime import datetime,time
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
def get_yesterday_anchorprice(past_bars) -> List[Dict[str, str]]:
    result = []

    # Iterate through each symbol in past5days
    for symbol, bars in past_bars.items():
        # Filter out bars where Time is '23:00:00'
        relevant_bars = [bar for bar in bars if bar.get('time') == '22:58:00']
        
        if relevant_bars:
            # Find the most recent date by comparing the Date values
            most_recent_bar = max(relevant_bars, key=lambda b: datetime.strptime(b['date'], '%Y-%m-%d').date())

            result.append({
                'symbol': most_recent_bar.get('symbol'),
                'date': most_recent_bar.get('date'),
                'time': most_recent_bar.get('time'),
                'anchorprice': most_recent_bar.get('close')
            })

    return result

def get_today_anchorprice(today_bars) -> List[Dict[str, str]]:
    result = []

    # Iterate through each symbol in today's bars
    for symbol, bars in today_bars.items():
        # Filter bars where Time is '16:30:00' and Date is today
        relevant_bars = [bar for bar in bars if bar.get('time') == '11:00:00']
        
        if relevant_bars:
            # Since we are filtering by '16:30:00', we can just take the first (or only) bar
            most_recent_bar = relevant_bars[0]

            result.append({
                'symbol': most_recent_bar.get('symbol'),
                'date': most_recent_bar.get('date'),
                'time': most_recent_bar.get('time'),
                'anchorprice': most_recent_bar.get('open')
            })

    return result


# Calculations

def calculate_percentage_change(rvol_df: pd.DataFrame, close_prices_df: pd.DataFrame) -> pd.DataFrame:

    # Merge the rvol_df with the close_prices_df based on 'Symbol'
    merged_df = pd.merge(rvol_df, close_prices_df, on='symbol', how='left')

    # Rename columns to remove the '_x' suffix and use the correct names
    merged_df.rename(columns={
        'date_x': 'date',    # Rename 'Date_x' to 'Date'
        'time_x': 'time',    # Rename 'Time_x' to 'Time'
        'close_x': 'close'   # Rename 'Close_x' to 'Close'
    }, inplace=True)

    # Calculate the percentage change in close prices
    merged_df['change'] = ((merged_df['close'] - merged_df['anchorprice']) / merged_df['anchorprice']) * 100

    # Round the percentage change to 2 decimal places
    merged_df['change'] = merged_df['change'].round(2)

    return merged_df

def calculate_avg_volume_model(past_bars: Dict[str, List[dict]]) -> List[dict]:

    # Flatten grouped bars
    all_data = []
    for bars in past_bars.values():
        all_data.extend(bars)

    df = pd.DataFrame(all_data)

    # Compute average volume per symbol per time
    avg_volume_df = (
        df.groupby(['symbol', 'time'], as_index=False)['volume']
        .mean()
        .rename(columns={'volume': 'avgvolume'})
    )

    avg_volume_df['avgvolume'] = avg_volume_df['avgvolume'].round(2)

    # Convert back to bars
    avg_volume_bars = avg_volume_df.to_dict(orient="records")

    return avg_volume_bars

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

# IB data fetch

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


# Data pipeline
def group_dataset_by_symbol(dataset: List[dict]) -> DefaultDict[str, list]:

    symbol_groups: DefaultDict[str, list] = defaultdict(list)

    for row in dataset:
        symbol_groups[row["symbol"]].extend(row.get("intraday_bars", []))

    return symbol_groups

def split_symbol_groups(symbol_groups: DefaultDict[str, list]) -> Tuple[DefaultDict[str, list], DefaultDict[str, list]]:
    
    today_str = datetime.today().strftime("%Y-%m-%d")

    today_bars: DefaultDict[str, list] = defaultdict(list)
    past_bars: DefaultDict[str, list] = defaultdict(list)

    for symbol, bars in symbol_groups.items():
        # Sort bars by date and time
        bars_sorted = sorted(bars, key=lambda b: (b.get("date"), b.get("time")))

        # Split today's vs historical
        for bar in bars_sorted:
            if bar.get("date") == today_str:
                today_bars[symbol].append(bar)
            else:
                past_bars[symbol].append(bar)

    return today_bars, past_bars

def filter_bars_by_time(today_bars: DefaultDict[str, list]) -> DefaultDict[str, list]:
    
    cutoff_time: str = "11:00:00"
    
    filtered_bars: DefaultDict[str, list] = defaultdict(list)

    for symbol, bars in today_bars.items():
        for bar in bars:
            if bar.get("time") >= cutoff_time:
                filtered_bars[symbol].append(bar)

    return filtered_bars

def filter_avgvolume_list(bars: List[Dict]) -> List[Dict]:
    
    cutoff_time = "11:00:00"

    return [
        bar
        for bar in bars
        if bar.get("time") >= cutoff_time
    ]

def bars_to_dataframe(today_bars: DefaultDict[str, list]) -> pd.DataFrame:
    """Flatten symbol->bars dictionary into a pandas DataFrame."""
    
    all_bars = [
        bar
        for bars in today_bars.values()
        for bar in bars
    ]

    return pd.DataFrame(all_bars)

def return_last_row_per_symbol(df: pd.DataFrame) -> List[ScannerResponse]:

    if df.empty:
        return []

    # Group by 'symbol' and take the last row for each group
    last_rows = df.groupby('symbol', as_index=False).last()

    # Convert each row to a ScannerResponse instance
    responses = [
        ScannerResponse(
            symbol=row['symbol'],
            date=row['date'],
            time=row['time'],
            open=row['open'],
            high=row['high'],
            low=row['low'],
            close=row['close'],
            volume=int(row['volume']),
            rvol=float(row['rvol']),
            change=float(row['change'])
        )
        for _, row in last_rows.iterrows()
    ]

    return responses

# end of datapipeline

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


    # Step 3: Merge intraday bars
    for row, bars in zip(dataset, intraday_results):
        symbol = row["symbol"]
        if isinstance(bars, Exception):
            logger.error(f"Error fetching intraday bars for {symbol}: {bars}")
            row["intraday_bars"] = None
        else:
            row["intraday_bars"] = bars

    return dataset

async def compute_datapipeline(dataset: List[dict]) -> List[ScannerResponse]:
    
    
    # Step 1: Group rows by symbol
    symbol_groups = group_dataset_by_symbol(dataset = dataset)

    # Step 2: Split into today and past bars
    today_bars, past_bars = split_symbol_groups(symbol_groups)

    # Step 3: Filter today bars to start from 11:00
    today_bars = filter_bars_by_time(today_bars)

    # Step 4: Calculate average volume using historical data
    avg_volume_bars = calculate_avg_volume_model(past_bars)

    # Step 5: Filter avg volume bars starting from 11:00
    avg_volume_bars = filter_avgvolume_list(avg_volume_bars)

    if not today_bars or not avg_volume_bars:
        logger.warning("No data bars data coming in")
        return [] 


    current_time = datetime.now().time()
    market_open = time(hour=16, minute=30)

    if current_time < market_open:
        # before 16:30 → use yesterday's close
        close_prices = get_yesterday_anchorprice(past_bars)
    else:
        # 16:30 or later → use today's close
        close_prices = get_today_anchorprice(today_bars)

    # Step 6 — Convert bars → pandas
    today_df = bars_to_dataframe(today_bars)
    avg_volume_df = pd.DataFrame(avg_volume_bars)
    close_prices_df = pd.DataFrame(close_prices)

    # Step 4: Calculate RVol for today's bars
    today_rvol_df = calculate_rvol(today_df, avg_volume_df)


    change_df = calculate_percentage_change(today_rvol_df,close_prices_df)
    logger.info(change_df)
    # ScannerResponse made here
    last_rows_responses = return_last_row_per_symbol(change_df)


    return last_rows_responses





async def run_scanner_logic(preset_name: str, ib: IB) -> List[dict]:
    """
    Fetch scanner data from IB asynchronously, then push it to the data pipeline.
    """
    preset = SCANNER_PRESETS.get(preset_name)
    if not preset:
        raise ValueError(
            f"Invalid preset_name. Available presets: {list(SCANNER_PRESETS.keys())}"
        )
    logger.info(f"Scanning the market with {preset}")
    sub = ScannerSubscription(**preset)

    # Fully async IB request
    scan_data: List[ScanData] = await ib.reqScannerDataAsync(sub)  # Scan the market
    if not scan_data:
        logger.warning("No scan data coming back from IB")
        return []
    
    # Push scan data to pipeline and await results
    data_from_scanning = await scan_datapipeline(scan_data,ib)
    final_data = await compute_datapipeline(data_from_scanning)


    return final_data