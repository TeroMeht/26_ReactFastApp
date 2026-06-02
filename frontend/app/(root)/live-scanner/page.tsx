'use client';

import * as React from 'react';
import HeaderBox from '@/components/HeaderBox';
import LiveScannerTable, {
  type LiveScannerRow,
  setExtensionStatusListener,
} from '@/components/live-scanner/LiveScannerTable';
import ConnectionStatus from '@/components/live-scanner/ConnectionStatus';
import { API_PREFIX } from '@/lib/api_prefix';

// Wire shape pushed by /api/live-scanner/stream. Mirrors
// backend.schemas.api_schemas.LiveScannerUpdate.
type LiveScannerUpdate = {
  side: 'up' | 'down';
  rows: LiveScannerRow[];
  connected: boolean;
  ts: number;
};

const DEFAULT_MAX_ROWS = 25;
const ROW_LIMIT_OPTIONS = [10, 25, 50, 100];
// Default minimum absolute gap percent. The user's spec is to only see
// movers of at least +/- 5%, so the toggle ships on.
const DEFAULT_MIN_ABS_CHANGE_PCT = 5;

const LiveScannerPage = () => {
  const [upRows, setUpRows] = React.useState<LiveScannerRow[]>([]);
  const [downRows, setDownRows] = React.useState<LiveScannerRow[]>([]);
  const [sseConnected, setSseConnected] = React.useState<boolean>(false);
  const [ibConnected, setIbConnected] = React.useState<boolean>(false);
  const [lastTs, setLastTs] = React.useState<number | null>(null);
  const [maxRows, setMaxRows] = React.useState<number>(DEFAULT_MAX_ROWS);
  // ON by default per spec: only show rows whose absolute gap % is >= 5.
  const [gapFilterEnabled, setGapFilterEnabled] = React.useState<boolean>(true);
  // Chart Router extension presence (see browser-extension/ at repo root).
  const [extensionReady, setExtensionReady] = React.useState<boolean>(false);
  const [error, setError] = React.useState<string | null>(null);

  const minAbsChangePct = gapFilterEnabled ? DEFAULT_MIN_ABS_CHANGE_PCT : 0;

  // Subscribe to extension status updates from the row-click module so
  // we can render a small indicator next to the connection dots.
  React.useEffect(() => {
    setExtensionStatusListener(setExtensionReady);
    return () => setExtensionStatusListener(null);
  }, []);

  // Open the SSE stream on mount; close on unmount. Browser auto-reconnects
  // on transient drops, we only flag disconnected after explicit CLOSED state.
  React.useEffect(() => {
    const url = `${API_PREFIX}/live-scanner/stream`;
    const es = new EventSource(url);

    const handleUpdate = (event: MessageEvent) => {
      try {
        const payload: LiveScannerUpdate = JSON.parse(event.data);
        if (payload.side === 'up') setUpRows(payload.rows);
        else if (payload.side === 'down') setDownRows(payload.rows);
        setIbConnected(Boolean(payload.connected));
        setLastTs(payload.ts);
        setSseConnected(true);
        setError(null);
      } catch (err) {
        console.error('Failed to parse live-scanner update', err);
      }
    };

    // Server emits explicit `event: update` frames.
    es.addEventListener('update', handleUpdate as EventListener);
    // sse-starlette pings as `event: ping` (no state change needed; just
    // confirms stream is alive — handled implicitly by EventSource state).
    es.addEventListener('ping', () => setSseConnected(true));

    es.onopen = () => {
      setSseConnected(true);
      setError(null);
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) {
        setSseConnected(false);
        setError('Stream closed. Refresh the page to reconnect.');
      } else {
        // CONNECTING — browser is retrying. Reflect that visually.
        setSseConnected(false);
      }
    };

    return () => {
      es.close();
    };
  }, []);

  return (
    <section className="home">
      <div className="home-content">
        <header className="home-header flex items-start justify-between">
          <HeaderBox
            type="title"
            title="Live Scanner"
            subtext="Real-time +5% gap ups and -5% gap downs from Interactive Brokers."
          />
        </header>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 px-1">
          <div className="flex items-center gap-4 flex-wrap">
            <ConnectionStatus
              sseConnected={sseConnected}
              ibConnected={ibConnected}
              lastTs={lastTs}
            />
            <div
              className="flex items-center gap-2 text-xs"
              title={
                extensionReady
                  ? 'Chart Router extension active — row clicks reuse the same TradingView tab.'
                  : 'Chart Router extension not detected. Install the extension in browser-extension/ for reliable tab reuse.'
              }
            >
              <span
                className={`inline-block w-2.5 h-2.5 rounded-full ${
                  extensionReady ? 'bg-success-600' : 'bg-gray-300'
                }`}
              />
              <span className="text-gray-700">
                Chart Router:{' '}
                <span className="font-medium">
                  {extensionReady ? 'connected' : 'off'}
                </span>
              </span>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-xs text-gray-700">
              <input
                type="checkbox"
                checked={gapFilterEnabled}
                onChange={(e) => setGapFilterEnabled(e.target.checked)}
                className="size-3.5 cursor-pointer"
              />
              ≥ ±{DEFAULT_MIN_ABS_CHANGE_PCT}% only
            </label>
            <label className="flex items-center gap-2 text-xs text-gray-700">
              Max rows per table:
              <select
                value={maxRows}
                onChange={(e) => setMaxRows(Number(e.target.value))}
                className="border border-gray-300 rounded px-2 py-1 text-xs"
              >
                {ROW_LIMIT_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </div>

        {error && (
          <div className="mt-2 p-2 text-xs text-pink-700 bg-pink-25 border border-pink-100 rounded">
            {error}
          </div>
        )}

        <main className="home-main mt-4">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <LiveScannerTable
              title="Gap Ups (+5%)"
              side="up"
              rows={upRows}
              maxRows={maxRows}
              minAbsChangePct={minAbsChangePct}
            />
            <LiveScannerTable
              title="Gap Downs (-5%)"
              side="down"
              rows={downRows}
              maxRows={maxRows}
              minAbsChangePct={minAbsChangePct}
            />
          </div>
        </main>
      </div>
    </section>
  );
};

export default LiveScannerPage;
