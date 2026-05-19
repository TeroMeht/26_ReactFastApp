'use client';

import * as React from 'react';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';

// Wire shape for a single live-scanner row. Kept in sync manually with
// backend schemas.api_schemas.LiveScannerRow — promote to generated types
// once the OpenAPI re-gen step is run.
export type LiveScannerRow = {
  symbol: string;
  rank: number;
  price: number | null;
  change: number | null;
  change_percent: number | null;
  volume: number | null;
  time_added: string;
};

type Side = 'up' | 'down';

type Props = {
  title: string;
  side: Side;
  rows: LiveScannerRow[];
  maxRows: number;
  // Minimum *absolute* gap percent to display. Rows whose
  // |change_percent| < this are hidden. 0 = no filter.
  // Default 5 enforces the "show only +/-5% movers" rule.
  minAbsChangePct?: number;
};

// TradingView chart URL pattern. Layout id fixed, only the symbol swaps.
//
// Goal: one TradingView tab total — every row click navigates that single
// tab to the new symbol instead of opening a fresh window.
//
// Implementation: a synthetic <a target="tv_chart"> click. Browsers honor
// named-target reuse for *anchor-driven* navigation more reliably than
// for the window.open() API; in particular, an anchor click goes through
// the normal navigation pipeline (popup blocker treats it as a user-
// gesture link, named-window lookup is consulted) and survives some
// COOP situations that defeat window.open().
//
// We also keep a module-scope Window reference as a belt-and-suspenders
// fallback: if writing to that reference succeeds we navigate directly
// without spawning anything.
//
// LIMITATION — read me: TradingView serves its chart pages with strict
// Cross-Origin-Opener-Policy. After the popup navigates to tradingview.com
// the browser severs the opener/named-target relationship for some
// browser+policy combinations, at which point NO pure web-page code can
// re-target that tab — only the user can manually close it. The two
// mechanisms below are the best a same-origin web app can do today.
const TRADINGVIEW_LAYOUT_ID = '4KgtaCO3';
const TRADINGVIEW_WINDOW_NAME = 'tradingview_chart';

let chartWindow: Window | null = null;

// Extension presence flag. The "Mehtanen Chart Router" extension
// (browser-extension/ at repo root) posts a HELLO message on every page
// load. While this flag is true, openTradingView() routes navigation
// through the extension's chrome.tabs API — which is *not* subject to
// COOP and can therefore reliably reuse the existing chart tab.
let extensionReady = false;
// Optional callback so the page UI can render a "Extension: connected"
// indicator. Wired from the page on mount.
let onExtensionStatusChange: ((ready: boolean) => void) | null = null;

if (typeof window !== 'undefined') {
  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const data = event.data as
      | { source?: string; type?: string; ok?: boolean }
      | undefined;
    if (!data || data.source !== 'mehtanen-scanner-ext') return;
    if (data.type === 'HELLO' || data.type === 'PING_RESULT') {
      if (!extensionReady) {
        extensionReady = true;
        onExtensionStatusChange?.(true);
      }
    }
    // Note: OPEN_TRADINGVIEW_RESULT is informational only; we don't
    // currently surface the success/failure to the UI.
  });

  // Ping once shortly after load in case we mounted before the content
  // script announced itself. (Race-condition belt-and-suspenders.)
  setTimeout(() => {
    try {
      window.postMessage(
        { source: 'mehtanen-scanner', type: 'PING' },
        window.location.origin,
      );
    } catch { /* ignore */ }
  }, 300);
}

export const setExtensionStatusListener = (
  cb: ((ready: boolean) => void) | null,
) => {
  onExtensionStatusChange = cb;
  // Fire immediately so the listener gets the current state.
  if (cb) cb(extensionReady);
};

const buildChartUrl = (symbol: string) =>
  `https://www.tradingview.com/chart/${TRADINGVIEW_LAYOUT_ID}/?symbol=${encodeURIComponent(
    symbol,
  )}`;

const openTradingView = (symbol: string) => {
  const url = buildChartUrl(symbol);

  // Path 0 (preferred): extension is installed — let it route via
  // chrome.tabs. The background worker finds the existing chart tab
  // (or any open tradingview.com/chart/* tab) and navigates it.
  if (extensionReady) {
    try {
      window.postMessage(
        { source: 'mehtanen-scanner', type: 'OPEN_TRADINGVIEW', url },
        window.location.origin,
      );
      return;
    } catch {
      // Fall through to the web-only path if postMessage somehow fails.
    }
  }

  // Path 1: live Window reference from a previous click. Try to navigate
  // it directly; cross-origin write to `location.href` is one of the
  // few operations the browser still allows on an opened popup.
  if (chartWindow && !chartWindow.closed) {
    try {
      chartWindow.location.href = url;
      try {
        chartWindow.focus();
      } catch {
        /* focus is best-effort */
      }
      return;
    } catch {
      // COOP severed the reference — fall through to anchor click.
      chartWindow = null;
    }
  }

  // Path 2: synthetic anchor click into the named target. If a tab with
  // this name still exists in the browser's window registry the click
  // navigates it; otherwise a new tab is created and given that name.
  const a = document.createElement('a');
  a.href = url;
  a.target = TRADINGVIEW_WINDOW_NAME;
  // Intentionally NO `rel="noopener"`: noopener forces a fresh top-level
  // browsing context that cannot be addressed by name on subsequent
  // clicks, which would defeat the whole point.
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  // Try to also grab a Window reference for the *next* click. This may
  // return null if the popup blocker or COOP refuses; that's fine, the
  // anchor-click path remains the primary mechanism.
  try {
    const handle = window.open('', TRADINGVIEW_WINDOW_NAME);
    if (handle) chartWindow = handle;
  } catch {
    /* ignore */
  }
};

const fmtNum = (n: number | null, digits = 2) =>
  n === null || Number.isNaN(n) ? '-' : n.toFixed(digits);

const fmtVol = (n: number | null) => {
  if (n === null || Number.isNaN(n)) return '-';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
};

const fmtTime = (iso: string) => {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return iso;
  }
};

const LiveScannerTable: React.FC<Props> = ({
  title,
  side,
  rows,
  maxRows,
  minAbsChangePct = 0,
}) => {
  // Apply the gap-% filter first, then trim to maxRows. Rows with no
  // computed change_percent yet (e.g. brand-new symbol awaiting first
  // mkt-data tick) fall back to 0 so they get filtered while data
  // catches up — they reappear automatically on next snapshot.
  const filtered = React.useMemo(
    () =>
      minAbsChangePct > 0
        ? rows.filter(
            (r) => Math.abs(r.change_percent ?? 0) >= minAbsChangePct,
          )
        : rows,
    [rows, minAbsChangePct],
  );
  const display = filtered.slice(0, maxRows);

  const headerAccent =
    side === 'up'
      ? 'bg-success-50 border-success-100 text-success-900'
      : 'bg-pink-25 border-pink-100 text-pink-900';

  return (
    <div className="flex-1 min-w-0 border rounded-md bg-white shadow-sm">
      <div className={`px-3 py-2 border-b ${headerAccent} flex items-center justify-between`}>
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="text-xs opacity-70">
          {filtered.length} of {rows.length}{' '}
          {rows.length === 1 ? 'symbol' : 'symbols'}
          {filtered.length > maxRows ? ` (showing ${maxRows})` : ''}
          {minAbsChangePct > 0 ? ` · ≥${minAbsChangePct}%` : ''}
        </span>
      </div>

      <div className="overflow-y-auto max-h-[70vh]">
        <Table className="table-auto text-xs">
          <TableHeader>
            <TableRow>
              <TableHead>#</TableHead>
              <TableHead>Symbol</TableHead>
              <TableHead className="text-right">Price</TableHead>
              <TableHead className="text-right">Gap %</TableHead>
              <TableHead className="text-right">Change $</TableHead>
              <TableHead className="text-right">Volume</TableHead>
              <TableHead>Time</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {display.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="text-center text-xs py-6 text-gray-400">
                  Waiting for first {side === 'up' ? 'gap up' : 'gap down'} update…
                </TableCell>
              </TableRow>
            )}
            {display.map((row) => {
              const changePct = row.change_percent ?? 0;
              const pctColor =
                changePct > 0
                  ? 'text-success-700'
                  : changePct < 0
                    ? 'text-pink-700'
                    : 'text-gray-500';
              return (
                <TableRow
                  key={row.symbol}
                  className="cursor-pointer hover:bg-blue-25"
                  onClick={() => openTradingView(row.symbol)}
                  title={`Open ${row.symbol} on TradingView`}
                >
                  <TableCell className="text-gray-400">{row.rank}</TableCell>
                  <TableCell className="font-semibold">{row.symbol}</TableCell>
                  <TableCell className="text-right">{fmtNum(row.price)}</TableCell>
                  <TableCell className={`text-right font-semibold ${pctColor}`}>
                    {fmtNum(row.change_percent)}%
                  </TableCell>
                  <TableCell className={`text-right ${pctColor}`}>
                    {fmtNum(row.change)}
                  </TableCell>
                  <TableCell className="text-right">{fmtVol(row.volume)}</TableCell>
                  <TableCell className="text-gray-500">{fmtTime(row.time_added)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
};

export default LiveScannerTable;
