# scanner_presets.py
"""
Scanner preset configurations for Interactive Brokers (IB) API.
Each preset is stored as a dictionary of parameters for ScannerSubscription.
"""

SCANNER_PRESETS = {
    "high_activity_scan": {
        "numberOfRows": 10,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "HOT_BY_VOLUME",
        "marketCapAbove": 1000,
        "abovePrice": 5,
        "aboveVolume": 100000,
        "stockTypeFilter": "CORP",
    },
    "high_activity_smallcaps_scan": {
        "numberOfRows": 10,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "HOT_BY_VOLUME",
        "marketCapAbove": 1,
        "abovePrice": 1,
        "aboveVolume": 1000,
        "stockTypeFilter": "CORP",
    },
    "gap_up_scan": {
        "numberOfRows": 10,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "TOP_PERC_GAIN",
        "abovePrice": 5,
        "aboveVolume": 100000,
        "marketCapAbove": 1000,
        "stockTypeFilter": "CORP",
    },
    "gap_down_scan": {
        "numberOfRows": 10,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "TOP_PERC_LOSE",
        "abovePrice": 5,
        "aboveVolume": 100000,
        "marketCapAbove": 1000,
        "stockTypeFilter": "CORP",
    },
    # ------------------------------------------------------------------
    # LIVE STREAMING SCANNER PRESETS
    # ------------------------------------------------------------------
    # Used by services.live_scanner.LiveScannerManager. These feed
    # ib.reqScannerSubscription(...) which streams updates whenever the
    # ranking changes. The +/- 5% threshold is enforced IB-side via
    # changePercAbove / changePercBelow so we only get qualifying symbols.
    "live_gap_up_scan": {
        "numberOfRows": 50,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "TOP_PERC_GAIN",
        "abovePrice": 5,
        "aboveVolume": 100000,
        "marketCapAbove": 1000,
        "stockTypeFilter": "CORP",
    },
    "live_gap_down_scan": {
        "numberOfRows": 50,
        "instrument": "STK",
        "locationCode": "STK.US.MAJOR",
        "scanCode": "TOP_PERC_LOSE",
        "abovePrice": 5,
        "aboveVolume": 100000,
        "marketCapAbove": 1000,
        "stockTypeFilter": "CORP",
    },
}
