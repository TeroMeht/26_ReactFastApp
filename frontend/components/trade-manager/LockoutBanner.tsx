"use client";

import * as React from "react";
import { API_PREFIX } from "@/lib/api_prefix";

type LockoutStatus = {
  locked: boolean;
  reason: string | null;
  message: string;
  cooldown_until: string | null;
  streak: number;
};

const POLL_MS = 60_000;

function formatRemaining(ms: number): string {
  if (ms <= 0) return "0s";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

/**
 * Global loss-cooldown banner.
 *
 * Polls /portfolio/lockout-status on a 15s cadence so the UI reflects the
 * lockout state proactively — the user sees the freeze the moment the 2nd
 * consecutive loss closes, without having to attempt a 3rd entry first.
 *
 * Auto-clears when cooldown_until passes; refetches on tab focus so the
 * state is fresh after the user comes back to the window.
 */
export default function LockoutBanner() {
  const [status, setStatus] = React.useState<LockoutStatus | null>(null);
  const [now, setNow] = React.useState<number>(Date.now());

  const fetchStatus = React.useCallback(async () => {
    try {
      const res = await fetch(`${API_PREFIX}/portfolio/lockout-status`, {
        cache: "no-store",
      });
      if (!res.ok) return;
      const data: LockoutStatus = await res.json();
      setStatus(data);
    } catch {
      // Network blips are fine — the next poll will catch up. We don't
      // want a transient fetch failure to drop the banner.
    }
  }, []);

  // Poll + refetch on focus.
  React.useEffect(() => {
    fetchStatus();
    const id = window.setInterval(fetchStatus, POLL_MS);
    const onFocus = () => fetchStatus();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [fetchStatus]);

  // 1Hz tick while locked so the countdown re-renders.
  React.useEffect(() => {
    if (!status?.locked) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [status?.locked]);

  if (!status?.locked) return null;

  const untilMs = status.cooldown_until
    ? new Date(status.cooldown_until).getTime()
    : null;

  // The cooldown window has elapsed but the next poll hasn't landed yet.
  // Hide the banner immediately — the next poll will confirm.
  if (untilMs !== null && untilMs <= now) return null;

  const remaining = untilMs !== null ? untilMs - now : null;
  // Tier 2 (rest of day) shows up as multiple hours; tier 1 is <= 1h.
  const isTier2 = remaining !== null && remaining > 90 * 60 * 1000;

  const headline = isTier2
    ? "TRADING LOCKED FOR THE REST OF THE DAY"
    : "TRADING LOCKED — STEP AWAY";

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="m-1 mb-2 rounded-md border-2 border-red-700 bg-gradient-to-r from-red-700 via-red-600 to-red-700 text-white shadow-lg shadow-red-900/50 ring-2 ring-red-500/40 ring-offset-2 ring-offset-red-900 overflow-hidden"
    >
      <div className="flex items-stretch">
        {/* Pulsing left rail — drives the eye to the banner */}
        <div className="w-2 bg-red-300 animate-pulse" aria-hidden="true" />

        <div className="flex-1 px-4 py-3 break-words">
          <div className="flex items-center gap-3">
            <span
              className="inline-flex items-center justify-center w-9 h-9 rounded-full bg-white text-red-700 font-black text-xl shadow-inner animate-pulse"
              aria-hidden="true"
            >
              !
            </span>
            <div className="text-xl font-black tracking-wide uppercase drop-shadow">
              {headline}
            </div>
          </div>

          <div className="mt-2 text-base font-semibold text-red-50">
            {status.streak} consecutive losses. {status.message}
          </div>

          {remaining !== null && untilMs !== null && (
            <div className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="text-sm uppercase tracking-wider text-red-100">
                Entries unlock in
              </span>
              <span className="font-mono text-3xl font-black tabular-nums text-white drop-shadow">
                {formatRemaining(remaining)}
              </span>
              <span className="text-sm text-red-100">
                (at {new Date(untilMs).toLocaleTimeString()})
              </span>
            </div>
          )}
        </div>

        {/* Pulsing right rail — symmetric, completes the alarm look */}
        <div className="w-2 bg-red-300 animate-pulse" aria-hidden="true" />
      </div>
    </div>
  );
}
