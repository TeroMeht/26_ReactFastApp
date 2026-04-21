'use client';

import { useState } from 'react';

type Props = {
  activeSymbol: string | null;
  onStart: (sym: string) => Promise<void> | void;
  onStop: () => Promise<void> | void;
  lastPrice: number | null;
  last2High: number | null;
  stopLevel: number | null;
};

export default function TickerStarter({
  activeSymbol,
  onStart,
  onStop,
  lastPrice,
  last2High,
  stopLevel,
}: Props) {
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);

  const handleClick = async () => {
    setBusy(true);
    try {
      if (activeSymbol) {
        await onStop();
        setInput('');
      } else if (input.trim().length > 0) {
        await onStart(input.trim().toUpperCase());
      }
    } finally {
      setBusy(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'Enter') return;
    if (activeSymbol || busy || input.trim().length === 0) return;
    e.preventDefault();
    handleClick();
  };

  const fmt = (v: number | null) => (v === null ? '—' : v.toFixed(2));

  return (
    <div className="w-full max-w-md p-3 bg-white rounded-lg shadow-sm border border-gray-200 text-sm space-y-2">
      <div className="font-medium text-gray-700">Auto Assist Streamer</div>

      <div className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ticker (e.g. AAPL) — press Enter"
          disabled={!!activeSymbol || busy}
          className="flex-1 border rounded px-2 py-1 uppercase disabled:bg-gray-100"
        />
        <button
          onClick={handleClick}
          disabled={busy || (!activeSymbol && input.trim().length === 0)}
          className={`px-3 py-1 rounded text-white disabled:opacity-60 ${
            activeSymbol ? 'bg-red-600 hover:bg-red-700' : 'bg-blue-600 hover:bg-blue-700'
          }`}
        >
          {busy ? '…' : activeSymbol ? 'Stop' : 'Start'}
        </button>
      </div>

      {activeSymbol && (
        <div className="grid grid-cols-2 gap-y-1 gap-x-2 text-xs text-gray-700 pt-2 border-t">
          <div className="text-gray-500">Symbol</div>
          <div className="font-mono">{activeSymbol}</div>
          <div className="text-gray-500">Last price</div>
          <div className="font-mono">{fmt(lastPrice)}</div>
          <div className="text-gray-500">Last-2 high (entry)</div>
          <div className="font-mono text-blue-700">{fmt(last2High)}</div>
          <div className="text-gray-500">Stop (last-5 low − 0.06)</div>
          <div className="font-mono text-red-700">{fmt(stopLevel)}</div>
        </div>
      )}
    </div>
  );
}
