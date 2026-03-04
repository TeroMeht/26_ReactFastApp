# scanner_presets.py
"""
Scanner preset configurations for Interactive Brokers (IB) API.
Each preset is stored as a dictionary of parameters for ScannerSubscription.
"""

SCANNER_PRESETS = {
    "high_activity_scan": {
        "numberOfRows": 20,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "HOT_BY_VOLUME",
        "marketCapAbove": 1000,
        "abovePrice": 5,
        "aboveVolume": 100000,
        "stockTypeFilter": "CORP"
    },
    
    "high_activity_smallcaps_scan": {
        "numberOfRows": 20,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "HOT_BY_VOLUME",
        "marketCapAbove": 1,
        "abovePrice": 1,
        "aboveVolume": 1,
        "stockTypeFilter": "CORP"
    },
    "gap_up_scan": {
        "numberOfRows": 20,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "TOP_PERC_GAIN",  # top % gainers
        "abovePrice": 5,              # avoid tiny stocks
        "aboveVolume": 100000,        # minimum liquidity
        "marketCapAbove": 1000,        # minimum market cap in millions
        "stockTypeFilter": "CORP"
    },
    "gap_down_scan": {
        "numberOfRows": 20,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "TOP_PERC_LOSE",  # top % losers
        "abovePrice": 5,              # avoid tiny stocks
        "aboveVolume": 100000,        # minimum liquidity
        "marketCapAbove": 1000,        # minimum market cap in millions
        "stockTypeFilter": "CORP"
    }
}
