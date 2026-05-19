'use client';

import * as React from 'react';

type Props = {
  // SSE EventSource state.
  sseConnected: boolean;
  // IB-connection flag from server-side update payload.
  ibConnected: boolean;
  // Epoch seconds of last received update (for "last update" timer).
  lastTs: number | null;
};

const dotClass = (ok: boolean) =>
  `inline-block w-2.5 h-2.5 rounded-full ${ok ? 'bg-success-600' : 'bg-pink-600'}`;

const fmtAgo = (ts: number | null, now: number) => {
  if (ts === null) return '—';
  const sec = Math.max(0, Math.round(now / 1000 - ts));
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  return `${Math.floor(sec / 3600)}h ago`;
};

const ConnectionStatus: React.FC<Props> = ({ sseConnected, ibConnected, lastTs }) => {
  // Re-render every second so "last update" stays fresh.
  const [now, setNow] = React.useState<number>(() => Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex items-center gap-4 text-xs">
      <div className="flex items-center gap-2">
        <span className={dotClass(sseConnected)} />
        <span className="text-gray-700">
          Stream: <span className="font-medium">{sseConnected ? 'connected' : 'disconnected'}</span>
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span className={dotClass(ibConnected)} />
        <span className="text-gray-700">
          IB:{' '}
          <span className="font-medium">{ibConnected ? 'connected' : 'disconnected'}</span>
        </span>
      </div>
      <div className="text-gray-500">Last update: {fmtAgo(lastTs, now)}</div>
    </div>
  );
};

export default ConnectionStatus;
