// Shared entry-strategy constants. Mirrors the backend's
// ENTRY_STRATEGY_NAMES in backend/schemas/api_schemas.py — keep this file
// in sync so request validation and the UI's picker agree.
//
// To add a new entry strategy: add it here AND to ENTRY_STRATEGY_NAMES in
// backend/schemas/api_schemas.py AND implement it in
// 22_WatchlistStreamer/src/strategies.py.

export type EntryStrategyOption = {
  value: string;
  label: string;
};

export const ENTRY_STRATEGY_OPTIONS: EntryStrategyOption[] = [
  { value: "reversal_long", label: "reversal_long" },
  { value: "reversal_short", label: "reversal_short" },
  { value: "vwap_continuation_long", label: "vwap_continuation_long" },
  { value: "vwap_continuation_short", label: "vwap_continuation_short" },
  { value: "orb_breakout_long", label: "orb_breakout_long" },
];
