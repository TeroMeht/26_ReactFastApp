'use client';

/**
 * WatchlistManager — replaces TickBoxAll.
 *
 * Lets the user pick a ticker + one or more entry strategies, then writes
 * that pair into the new `watchlist` / `watchlist_strategies` DB tables via
 * /api/watchlist. The 22_WatchlistStreamer reads those tables at startup; the
 * user restarts the streamer (Start button) to pick up changes.
 *
 * Endpoints used:
 *   GET    /api/strategies          available entry strategy names
 *   GET    /api/watchlist           list current rows
 *   POST   /api/watchlist           add a new symbol with strategies (409 on dup)
 *   PUT    /api/watchlist/{symbol}  replace strategies for an existing symbol
 *   DELETE /api/watchlist/{symbol}  remove a symbol
 */

import * as React from 'react';
import { API_PREFIX } from '@/lib/api_prefix';

type WatchlistRow = {
  id: number;
  symbol: string;
  strategies: string[];
  created_at: string;
};

type StrategiesResponse = {
  strategies: string[];
};

// Strategies we never want users to bind per-ticker, regardless of what the
// backend might return. Belt-and-braces filter: the API in
// schemas/api_schemas.py also omits these from GET /api/strategies, but this
// guarantees they don't appear in the UI even if a stale backend is running.
const HIDDEN_STRATEGIES = new Set<string>([
  'upside_extension',
  'downside_extension',
]);

const WatchlistManager: React.FC = () => {
  const [rows, setRows] = React.useState<WatchlistRow[]>([]);
  const [strategiesAvailable, setStrategiesAvailable] = React.useState<string[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  // Add-form state
  const [newSymbol, setNewSymbol] = React.useState('');
  const [newStrategies, setNewStrategies] = React.useState<Set<string>>(new Set());
  const [saving, setSaving] = React.useState(false);

  // Per-row "editing strategies" state (keyed by symbol)
  const [editingSymbol, setEditingSymbol] = React.useState<string | null>(null);
  const [editStrategies, setEditStrategies] = React.useState<Set<string>>(new Set());

  // Start-streamer state — colocated with the watchlist so the user can add a
  // ticker and start the streamer from the same panel.
  const [startMessage, setStartMessage] = React.useState<string | null>(null);
  const [startError, setStartError] = React.useState(false);
  const [starting, setStarting] = React.useState(false);

  // -------------------------------------------------------------------------
  // Data loading
  // -------------------------------------------------------------------------

  const fetchAll = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [stratRes, listRes] = await Promise.all([
        fetch(`${API_PREFIX}/strategies`),
        fetch(`${API_PREFIX}/watchlist`),
      ]);
      if (!stratRes.ok) throw new Error('Failed to fetch strategies');
      if (!listRes.ok) throw new Error('Failed to fetch watchlist');
      const strat: StrategiesResponse = await stratRes.json();
      const list: WatchlistRow[] = await listRes.json();
      // Filter out HIDDEN_STRATEGIES defensively (see comment on the constant).
      setStrategiesAvailable(
        (strat.strategies || []).filter((s) => !HIDDEN_STRATEGIES.has(s)),
      );
      setRows(list || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  const toggleInSet = (set: Set<string>, name: string): Set<string> => {
    const next = new Set(set);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    return next;
  };

  // -------------------------------------------------------------------------
  // Add a new ticker
  // -------------------------------------------------------------------------

  const handleAdd = async () => {
    const symbol = newSymbol.trim().toUpperCase();
    if (!symbol) {
      setError('Enter a ticker symbol first.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API_PREFIX}/watchlist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          strategies: Array.from(newStrategies),
        }),
      });
      if (res.status === 409) {
        setError(
          `${symbol} is already on the watchlist. Edit its strategies below.`,
        );
        return;
      }
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Add failed (${res.status}): ${body}`);
      }
      setNewSymbol('');
      setNewStrategies(new Set());
      await fetchAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  // -------------------------------------------------------------------------
  // Edit / delete an existing ticker
  // -------------------------------------------------------------------------

  const startEdit = (row: WatchlistRow) => {
    setEditingSymbol(row.symbol);
    setEditStrategies(new Set(row.strategies));
  };

  const cancelEdit = () => {
    setEditingSymbol(null);
    setEditStrategies(new Set());
  };

  const saveEdit = async (symbol: string) => {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API_PREFIX}/watchlist/${encodeURIComponent(symbol)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategies: Array.from(editStrategies) }),
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Update failed (${res.status}): ${body}`);
      }
      cancelEdit();
      await fetchAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  // -------------------------------------------------------------------------
  // Start the streamer (replaces the old standalone RunScript panel)
  // -------------------------------------------------------------------------

  const handleStart = async () => {
    setStarting(true);
    setStartMessage(null);
    setStartError(false);
    try {
      const res = await fetch(`${API_PREFIX}/run-script`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setStartError(true);
        setStartMessage(data.output || `Start failed (${res.status})`);
        return;
      }
      setStartMessage(data.output || 'Streamer started successfully.');
    } catch (err) {
      setStartError(true);
      setStartMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  };

  const handleDelete = async (symbol: string) => {
    // No confirm prompt — remove immediately on click.
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${API_PREFIX}/watchlist/${encodeURIComponent(symbol)}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const body = await res.text();
        throw new Error(`Delete failed (${res.status}): ${body}`);
      }
      await fetchAll();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="w-full max-w-md p-3 bg-white rounded-lg shadow-sm border border-gray-200 text-sm space-y-3">
      <div className="font-medium text-gray-700">Watchlist</div>

      {error && (
        <div className="p-2 rounded text-xs bg-red-100 text-red-700">{error}</div>
      )}

      {/* --- Add form + Start --- */}
      <div className="border rounded-md p-2 space-y-2 bg-gray-50">
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Ticker"
            value={newSymbol}
            onChange={(e) => setNewSymbol(e.target.value)}
            className="flex-1 px-2 py-1 border rounded text-xs uppercase"
          />
          <button
            onClick={handleAdd}
            disabled={saving || !newSymbol.trim()}
            className="px-3 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-700 disabled:opacity-60"
          >
            {saving ? '...' : 'Add'}
          </button>
          <button
            onClick={handleStart}
            disabled={starting}
            className="px-3 py-1 bg-green-600 text-white text-xs rounded hover:bg-green-700 disabled:opacity-60"
            title="Start the 22_WatchlistStreamer with the current watchlist"
          >
            {starting ? 'Starting…' : 'Start'}
          </button>
        </div>

        {/* Start-streamer status message */}
        {startMessage && (
          <div
            className={`p-2 rounded text-xs whitespace-pre-wrap max-h-32 overflow-auto ${
              startError
                ? 'bg-red-100 text-red-700'
                : 'bg-green-100 text-green-700'
            }`}
          >
            {startMessage}
          </div>
        )}

        <div className="grid grid-cols-1 gap-1">
          {strategiesAvailable.length === 0 && (
            <p className="text-xs text-gray-500">Loading strategies…</p>
          )}
          {strategiesAvailable.map((s) => (
            <label key={s} className="flex items-center gap-2 text-xs cursor-pointer">
              <input
                type="checkbox"
                checked={newStrategies.has(s)}
                onChange={() => setNewStrategies((prev) => toggleInSet(prev, s))}
                className="accent-blue-500"
              />
              <span className="font-mono">{s}</span>
            </label>
          ))}
        </div>
      </div>

      {/* --- Current watchlist --- */}
      <div className="space-y-2">
        {loading && <p className="text-xs text-gray-500">Loading watchlist…</p>}
        {!loading && rows.length === 0 && (
          <p className="text-xs text-gray-500">Watchlist is empty.</p>
        )}
        {rows.map((row) => {
          const isEditing = editingSymbol === row.symbol;
          return (
            <div
              key={row.symbol}
              className="border rounded-md p-2 space-y-1 bg-white"
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-xs">{row.symbol}</span>
                <div className="flex gap-2">
                  {!isEditing && (
                    <>
                      <button
                        onClick={() => startEdit(row)}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(row.symbol)}
                        disabled={saving}
                        className="text-xs text-red-600 hover:underline disabled:opacity-60"
                      >
                        Remove
                      </button>
                    </>
                  )}
                  {isEditing && (
                    <>
                      <button
                        onClick={() => saveEdit(row.symbol)}
                        disabled={saving}
                        className="text-xs text-green-600 hover:underline disabled:opacity-60"
                      >
                        {saving ? '...' : 'Save'}
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="text-xs text-gray-600 hover:underline"
                      >
                        Cancel
                      </button>
                    </>
                  )}
                </div>
              </div>

              {!isEditing && (
                <div className="flex flex-wrap gap-1">
                  {row.strategies.length === 0 ? (
                    <span className="text-xs text-gray-400 italic">
                      no strategies — won&apos;t trigger alarms
                    </span>
                  ) : (
                    row.strategies.map((s) => (
                      <span
                        key={s}
                        className="px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded text-[10px] font-mono"
                      >
                        {s}
                      </span>
                    ))
                  )}
                </div>
              )}

              {isEditing && (
                <div className="grid grid-cols-1 gap-1 pt-1">
                  {strategiesAvailable.map((s) => (
                    <label
                      key={s}
                      className="flex items-center gap-2 text-xs cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={editStrategies.has(s)}
                        onChange={() =>
                          setEditStrategies((prev) => toggleInSet(prev, s))
                        }
                        className="accent-blue-500"
                      />
                      <span className="font-mono">{s}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default WatchlistManager;
