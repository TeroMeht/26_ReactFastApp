'use client';

import { useEffect, useRef, useState } from 'react';

import TickerStarter from '@/components/auto-assist/TickerStarter';
import LiveAutoChart from '@/components/auto-assist/LiveAutoChart';
import SignalApproval, { SignalEvent } from '@/components/auto-assist/SignalApproval';
import { API_PREFIX } from '@/lib/api_prefix';

type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  ema9?: number | null;
  vwap?: number | null;
};

/**
 * Auto Assist panel shown on the Risk Levels page.
 *
 * Owns the lifecycle for the per-symbol live session:
 *  - POST /api/auto-assist/start    to begin a session (server seeds 12h history)
 *  - GET  /api/auto-assist/stream   SSE channel for state/tick/bar/levels/signal
 *  - POST /api/auto-assist/stop     to tear the session down
 *
 * When a signal arrives, the SignalApproval row is populated with an
 * auto-generated order that the user can submit via Send — which posts to
 * /api/portfolio/entry-request exactly the same way the manual Pending
 * Orders table does.
 */
export default function AutoAssistPanel() {
  const [activeSymbol, setActiveSymbol] = useState<string | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [currentBar, setCurrentBar] = useState<Bar | null>(null);
  const [last2High, setLast2High] = useState<number | null>(null);
  const [stopLevel, setStopLevel] = useState<number | null>(null);
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [signal, setSignal] = useState<SignalEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const resetState = () => {
    setBars([]);
    setCurrentBar(null);
    setLast2High(null);
    setStopLevel(null);
    setLastPrice(null);
    setSignal(null);
    setError(null);
  };

  const handleStart = async (symbol: string) => {
    try {
      const res = await fetch(`${API_PREFIX}/auto-assist/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail ?? `Failed to start (status ${res.status})`);
      }
      resetState();
      setActiveSymbol(symbol.toUpperCase());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleStop = async () => {
    if (!activeSymbol) return;
    try {
      await fetch(`${API_PREFIX}/auto-assist/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: activeSymbol }),
      });
    } catch (err) {
      console.error(err);
    }
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
    setActiveSymbol(null);
    resetState();
  };

  useEffect(() => {
    if (!activeSymbol) return;

    const es = new EventSource(
      `${API_PREFIX}/auto-assist/stream?symbol=${activeSymbol}`,
    );
    esRef.current = es;

    es.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        switch (msg.type) {
          case 'state':
            setBars(Array.isArray(msg.bars) ? msg.bars : []);
            setLast2High(msg.last2_high ?? null);
            setStopLevel(msg.stop_level ?? null);
            break;
          case 'tick':
            setLastPrice(msg.price);
            setCurrentBar({
              time: msg.bar_time,
              open: msg.bar_open,
              high: msg.bar_high,
              low: msg.bar_low,
              close: msg.bar_close,
              volume: msg.bar_volume ?? null,
              vwap: msg.bar_vwap ?? null,
            });
            break;
          case 'bar':
            setBars((prev) => [
              ...prev,
              {
                time: msg.time,
                open: msg.open,
                high: msg.high,
                low: msg.low,
                close: msg.close,
                volume: msg.volume ?? null,
                ema9: msg.ema9 ?? null,
                vwap: msg.vwap ?? null,
              },
            ]);
            setCurrentBar(null);
            break;
          case 'levels':
            setLast2High(msg.last2_high ?? null);
            setStopLevel(msg.stop_level ?? null);
            break;
          case 'signal':
            setSignal(msg as SignalEvent);
            break;
          case 'stopped':
            setActiveSymbol(null);
            break;
          default:
            break;
        }
      } catch {
        /* ignore malformed messages */
      }
    };

    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) {
        setError('Live stream closed unexpectedly.');
      }
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [activeSymbol]);

  return (
    <div className="p-4">
      <h2 className="text-xl font-bold mb-4">Auto Assist</h2>

      {/* Control row: streamer + pending-order signal panel */}
      <div className="flex flex-col md:flex-row gap-6 items-start">
        <div className="flex-1">
          <TickerStarter
            activeSymbol={activeSymbol}
            onStart={handleStart}
            onStop={handleStop}
            lastPrice={lastPrice}
            last2High={last2High}
            stopLevel={stopLevel}
          />
        </div>
        <div className="w-full md:flex-[2]">
          <SignalApproval signal={signal} onDismiss={() => setSignal(null)} />
        </div>
      </div>

      {error && (
        <div className="mt-4 p-2 rounded bg-red-100 text-red-700 text-xs">{error}</div>
      )}

      {/* Live chart */}
      <div className="mt-6">
        <LiveAutoChart
          symbol={activeSymbol}
          bars={bars}
          currentBar={currentBar}
          last2High={last2High}
          stopLevel={stopLevel}
          signal={
            signal
              ? {
                  bar_time: signal.bar_time ?? signal.ts,
                  price: signal.price,
                }
              : null
          }
        />
      </div>
    </div>
  );
}
