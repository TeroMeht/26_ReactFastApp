// Shared exit-strategy + trim constants. Mirrors the backend's
// settings.EXIT_TRIGGERS and ALLOWED_TRIM_PERCENTAGES — keep this file
// in sync with backend/core/config.py and backend/schemas/api_schemas.py.
//
// swing_trade is a passive marker (no streamer trigger); the rest map to
// real alarms the streamer emits.

export type ExitStrategyOption = {
  value: string;
  label: string;
};

export const EXIT_STRATEGY_OPTIONS: ExitStrategyOption[] = [
  { value: "momentum_long_exit", label: "momentum_long_exit" },
  { value: "momentum_short_exit", label: "momentum_short_exit" },
  { value: "endofday_exit", label: "endofday_exit" },
  { value: "vwap_exit", label: "vwap_exit" },
  { value: "swing_trade", label: "swing_trade" },
];

export type TrimOption = {
  value: number;
  label: string;
};

export const TRIM_OPTIONS: TrimOption[] = [
  { value: 0.25, label: "25%" },
  { value: 0.5, label: "50%" },
  { value: 0.75, label: "75%" },
  { value: 1, label: "100%" },
];

// The payload shape sent to /api/portfolio/entry-request (per-exit) and
// equivalently /api/exits (one-at-a-time). Centralized here so callers
// don't redeclare it.
export type ExitSpec = {
  strategy: string;
  trim_percentage: number;
};

// Small tolerance for floating-point sum comparisons (e.g. 0.25 + 0.5 + 0.25
// can drift by ~1e-17). All trim values are 0.25 multiples so any miss > 1e-9
// is a real discrepancy.
export const TRIM_SUM_EPS = 1e-9;
