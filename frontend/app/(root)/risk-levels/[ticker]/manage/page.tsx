"use client";

import React, { useState, useEffect, useCallback, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { OpenPosition } from "@/lib/types";
import { API_PREFIX } from "@/lib/api_prefix";

const TRIM_OPTIONS = [
  { value: 0.25, label: "25% (trim)" },
  { value: 0.5, label: "50% (trim)" },
  { value: 0.75, label: "75% (trim)" },
  { value: 1, label: "100% (full exit)" },
];

// Mirrors backend settings.EXIT_TRIGGERS. Keep this list in sync with the
// backend env (EXIT_TRIGGERS) so the dropdown only offers alarms the
// process_exit_request endpoint will actually accept.
const STRATEGY_OPTIONS = [
  { value: "relatr_up_exit", label: "relatr_up_exit" },
  { value: "relatr_down_exit", label: "relatr_down_exit" },
  { value: "endofday_exit", label: "endofday_exit" },
  { value: "vwap_exit", label: "vwap_exit" },
];

type ExitRow = {
  symbol: string;
  strategy: string;
  trim_percentage: number | string;
  updated: string;
};

const formatTrimLabel = (value: number) => {
  const opt = TRIM_OPTIONS.find((o) => o.value === value);
  return opt ? opt.label : `${(value * 100).toFixed(0)}%`;
};

const ManagePage = () => {
  const params = useSearchParams();
  const dataParam = params.get("data");

  const [totalRisk, setTotalRisk] = useState("");
  const [responseData, setResponseData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // List of currently-armed exit_requests rows for this symbol, hydrated
  // from GET /api/exits/{symbol}.
  const [exitRows, setExitRows] = useState<ExitRow[]>([]);
  const [exitsLoading, setExitsLoading] = useState(false);

  // Add-new-row form state. Strategy starts empty so the user must pick.
  const [newStrategy, setNewStrategy] = useState<string>("");
  const [newTrim, setNewTrim] = useState<number>(1);
  const [addingRow, setAddingRow] = useState(false);

  // Per-row deleting flag, keyed by strategy.
  const [deleting, setDeleting] = useState<Record<string, boolean>>({});

  // Move-stop-to-breakeven state — kept separate from the Add flow so the two
  // operations' results don't collide when the user runs both on one visit.
  const [moveBeLoading, setMoveBeLoading] = useState(false);
  const [moveBeResult, setMoveBeResult] = useState<{
    status: string;
    message: string;
    symbol?: string;
    order_id?: number;
    new_stop_price?: number;
  } | null>(null);

  // Memoize the parsed position on dataParam (a stable string) so we don't
  // produce a fresh object on every render — that re-firing useCallback /
  // useEffect deps caused an infinite GET /api/exits/{symbol} loop.
  const position = useMemo<OpenPosition | null>(() => {
    if (!dataParam) return null;
    try {
      return JSON.parse(atob(decodeURIComponent(dataParam))) as OpenPosition;
    } catch (err) {
      console.error("Failed to parse position data:", err);
      return null;
    }
  }, [dataParam]);

  const symbol = position?.symbol ?? null;

  const handleClose = () => window.history.back();

  // Pull all exit_request rows for this symbol from the backend. Depend on
  // the primitive `symbol` string, not the position object reference.
  const fetchExitRows = useCallback(async () => {
    if (!symbol) return;
    try {
      setExitsLoading(true);
      const res = await fetch(
        `${API_PREFIX}/exits/${encodeURIComponent(symbol)}`,
      );
      if (!res.ok) throw new Error(await res.text());
      const data: ExitRow[] = await res.json();
      setExitRows(data);
    } catch (err: any) {
      console.error("Error fetching exit rows:", err);
      setError(`Failed to load exit requests: ${err.message || err}`);
    } finally {
      setExitsLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    fetchExitRows();
  }, [fetchExitRows]);

  // Strategies already used for this symbol — memoized so identity is stable
  // across renders. The add form hides these to prevent duplicate
  // (symbol, strategy) submissions.
  const usedStrategies = useMemo(
    () => new Set(exitRows.map((r) => r.strategy)),
    [exitRows],
  );
  const availableStrategyOptions = useMemo(
    () => STRATEGY_OPTIONS.filter((opt) => !usedStrategies.has(opt.value)),
    [usedStrategies],
  );

  // Reset newStrategy if it became unavailable (e.g., user just added it).
  useEffect(() => {
    if (newStrategy && usedStrategies.has(newStrategy)) {
      setNewStrategy("");
    }
  }, [newStrategy, usedStrategies]);

  const addExitRow = async () => {
    if (!position) return;
    if (!newStrategy) {
      setError("Pick a strategy before adding the row.");
      return;
    }

    try {
      setAddingRow(true);
      setError(null);

      const res = await fetch(`${API_PREFIX}/exits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: position.symbol,
          strategy: newStrategy,
          trim_percentage: newTrim,
        }),
      });

      if (!res.ok) throw new Error(await res.text());

      // Reset the form and refresh the list.
      setNewStrategy("");
      setNewTrim(1);
      await fetchExitRows();
    } catch (err: any) {
      setError(`Add failed: ${err.message || err}`);
    } finally {
      setAddingRow(false);
    }
  };

  const deleteExitRow = async (strategy: string) => {
    if (!position) return;
    try {
      setDeleting((d) => ({ ...d, [strategy]: true }));
      setError(null);

      const res = await fetch(
        `${API_PREFIX}/exits/${encodeURIComponent(
          position.symbol,
        )}/${encodeURIComponent(strategy)}`,
        { method: "DELETE" },
      );

      if (!res.ok) throw new Error(await res.text());
      await fetchExitRows();
    } catch (err: any) {
      setError(`Delete failed: ${err.message || err}`);
    } finally {
      setDeleting((d) => {
        const next = { ...d };
        delete next[strategy];
        return next;
      });
    }
  };

  // Move the open STP order for this symbol to breakeven (avg cost).  Backend:
  // POST /api/portfolio/move-stop-be?symbol=<symbol>.  The router declares
  // `symbol` as a plain str parameter, so FastAPI reads it from the query
  // string rather than the JSON body.
  const handleMoveBreakeven = async () => {
    if (!position) return;

    try {
      setMoveBeLoading(true);
      setMoveBeResult(null);

      const url = `${API_PREFIX}/portfolio/move-stop-be?symbol=${encodeURIComponent(
        position.symbol,
      )}`;
      const res = await fetch(url, { method: "POST" });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text);
      }

      const data = await res.json();
      setMoveBeResult(data);
    } catch (err: any) {
      setMoveBeResult({
        status: "error",
        message: err.message || String(err),
      });
    } finally {
      setMoveBeLoading(false);
    }
  };

  const handleAdd = async () => {
    if (!position) return;

    try {
      setLoading(true);
      setError(null);
      setResponseData(null);

      const payload = {
        symbol: position.symbol,
        contract_type: position.contract_type,
        total_risk: Number(totalRisk),
      };

      const res = await fetch(`${API_PREFIX}/portfolio/add-request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text);
      }

      const data = await res.json();
      setResponseData(data);
    } catch (err: any) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  };

  if (!position) return <div>Loading position...</div>;

  return (
    <div className="p-6 max-w-2xl mx-auto bg-white shadow-lg rounded-md mt-10">
      <h2 className="text-lg font-semibold mb-4">
        Position Management – {position.symbol}
      </h2>

      <div className="space-y-2 text-left">
        <p><strong>Contract:</strong> {position.contract_type}</p>
        <p><strong>Aux Price:</strong> {position.auxprice}</p>
        <p><strong>Avg Cost:</strong> {position.avgcost}</p>
        <p><strong>Position:</strong> {position.position}</p>
        <p><strong>Open Risk:</strong> {position.openrisk}</p>
        <p><strong>Allocation:</strong> {position.allocation}</p>
        <p><strong>Size:</strong> {position.size}</p>
      </div>

      {/* Existing exit requests for this symbol */}
      <div className="mt-6">
        <h3 className="font-semibold mb-2">Exit Requests</h3>

        {exitsLoading ? (
          <p className="text-sm text-gray-500">Loading...</p>
        ) : exitRows.length === 0 ? (
          <p className="text-sm text-gray-500">
            No exit requests armed for {position.symbol}.
          </p>
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b">
                <th className="text-left py-1 pr-2">Strategy</th>
                <th className="text-left py-1 pr-2">Trim %</th>
                <th className="text-left py-1 pr-2">Updated</th>
                <th className="text-right py-1">Action</th>
              </tr>
            </thead>
            <tbody>
              {exitRows.map((row) => {
                const trimNum = Number(row.trim_percentage);
                return (
                  <tr key={row.strategy} className="border-b last:border-0">
                    <td className="py-1 pr-2">{row.strategy}</td>
                    <td className="py-1 pr-2">
                      {Number.isNaN(trimNum)
                        ? row.trim_percentage
                        : formatTrimLabel(trimNum)}
                    </td>
                    <td className="py-1 pr-2">
                      {new Date(row.updated).toLocaleString()}
                    </td>
                    <td className="py-1 text-right">
                      <button
                        onClick={() => deleteExitRow(row.strategy)}
                        disabled={!!deleting[row.strategy]}
                        className="text-red-600 hover:text-red-800 disabled:opacity-50"
                      >
                        {deleting[row.strategy] ? "Removing..." : "Remove"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        {/* Add-new-row form. Hidden when every strategy is already armed. */}
        <div className="mt-4 p-3 border rounded bg-gray-50">
          <p className="text-sm font-semibold mb-2">Add exit request</p>
          {availableStrategyOptions.length === 0 ? (
            <p className="text-xs text-gray-600">
              All strategies are already armed for this symbol.
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <label htmlFor="new-strategy" className="font-medium">
                Strategy:
              </label>
              <select
                id="new-strategy"
                value={newStrategy}
                onChange={(e) => setNewStrategy(e.target.value)}
                disabled={addingRow}
                className="border rounded px-2 py-1"
              >
                <option value="" disabled>
                  Select strategy...
                </option>
                {availableStrategyOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>

              <label htmlFor="new-trim" className="font-medium">
                Trim %:
              </label>
              <select
                id="new-trim"
                value={newTrim}
                onChange={(e) => setNewTrim(Number(e.target.value))}
                disabled={addingRow}
                className="border rounded px-2 py-1"
              >
                {TRIM_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>

              <button
                onClick={addExitRow}
                disabled={addingRow || !newStrategy}
                className="bg-blue-600 text-white px-3 py-1 rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {addingRow ? "Adding..." : "Add"}
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Total Risk Input */}
      <div className="mt-6">
        <label className="block text-sm font-medium mb-1">Total Risk</label>
        <input
          type="number"
          value={totalRisk}
          onChange={(e) => setTotalRisk(e.target.value)}
          className="w-full border rounded px-3 py-2"
          placeholder="Enter total risk"
        />
      </div>

      {/* Buttons */}
      <div className="mt-6 flex justify-center gap-4 flex-wrap">
        <button
          onClick={handleAdd}
          disabled={loading || !totalRisk}
          className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700 disabled:opacity-50"
        >
          {loading ? "Adding..." : "Add"}
        </button>

        {/* Move stop to breakeven — live IB modify, no Total Risk needed. */}
        <button
          onClick={handleMoveBreakeven}
          disabled={moveBeLoading}
          className="bg-amber-600 text-white px-4 py-2 rounded hover:bg-amber-700 disabled:opacity-50"
          title="Move the open STP order for this symbol to the position's average cost"
        >
          {moveBeLoading ? "Moving..." : "Move Stop to BE"}
        </button>

        <button
          onClick={handleClose}
          className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
        >
          Close
        </button>
      </div>

      {/* API Response */}
      {error && <div className="mt-4 text-red-600 text-sm">{error}</div>}

      {responseData && (
        <div className="mt-4 p-3 border rounded bg-gray-50 text-sm space-y-2">
          <p><strong>Allowed:</strong> {responseData.allowed ? "Yes" : "No"}</p>
          <p><strong>Message:</strong> {responseData.message}</p>
          <p><strong>Symbol:</strong> {responseData.symbol}</p>


          {responseData.place_result && (
            <div className="pl-2 border-l border-gray-300 space-y-1">
              <p><strong>Placed Order:</strong></p>
              <p>Order ID: {responseData.place_result.orderId}</p>
              <p>Action: {responseData.place_result.action}</p>
              <p>Total Quantity: {responseData.place_result.totalQuantity}</p>
              <p>Limit Price: {responseData.place_result.lmtPrice}</p>
              <p>Stop Price: {responseData.place_result.stopPrice}</p>
            </div>
          )}
        </div>
      )}

      {/* Move-stop-to-BE result — green on success, red on error.  Kept in a
          separate panel so it doesn't overwrite the Add response. */}
      {moveBeResult && (
        <div
          className={`mt-4 p-3 border rounded text-sm space-y-1 ${
            moveBeResult.status === "success"
              ? "bg-green-50 border-green-200 text-green-800"
              : "bg-red-50 border-red-200 text-red-800"
          }`}
        >
          <p><strong>Move Stop to BE:</strong> {moveBeResult.status}</p>
          <p><strong>Message:</strong> {moveBeResult.message}</p>
          {moveBeResult.order_id !== undefined && (
            <p><strong>Order ID:</strong> {moveBeResult.order_id}</p>
          )}
          {moveBeResult.new_stop_price !== undefined && (
            <p><strong>New Stop Price:</strong> {moveBeResult.new_stop_price}</p>
          )}
        </div>
      )}
    </div>
  );
};

export default ManagePage;
