"use client";

import React, { useState, useEffect, useCallback, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { OpenPosition } from "@/lib/types";
import { API_PREFIX } from "@/lib/api_prefix";
import { EXIT_STRATEGY_OPTIONS, TRIM_OPTIONS } from "@/constants/exits";

// Armed exits can be edited (strategy + trim %) or deleted here. Backend
// upserts by (symbol, strategy), so a strategy change is implemented as
// delete-old + upsert-new in one save action. IBKR sync of edits is a
// follow-up; for now we only mutate the stored exit_request record.

type ExitRow = {
  symbol: string;
  strategy: string;
  trim_percentage: number | string;
  updated: string;
};

const formatTrimLabel = (value: number) => `${(value * 100).toFixed(0)}%`;

const ManagePage = () => {
  const params = useSearchParams();
  const dataParam = params.get("data");

  const [totalRisk, setTotalRisk] = useState("");
  const [responseData, setResponseData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // List of currently-armed exit_requests rows for this symbol, hydrated
  // from GET /api/exits/{symbol}. Editable inline via the table below.
  const [exitRows, setExitRows] = useState<ExitRow[]>([]);
  const [exitsLoading, setExitsLoading] = useState(false);

  // Inline-edit state for the armed exits table. `editingStrategy` holds the
  // original strategy of the row currently being edited (or null if none),
  // and `editDraft` holds the in-progress new values. `mutatingStrategy`
  // disables the row's buttons while a network request is in flight.
  const [editingStrategy, setEditingStrategy] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<{
    strategy: string;
    trim: number;
  }>({ strategy: "", trim: 1 });
  const [mutatingStrategy, setMutatingStrategy] = useState<string | null>(null);
  const [exitsError, setExitsError] = useState<string | null>(null);

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

  // Enter edit mode for one row. Pre-fills the draft with the row's current
  // values so the user can change one field without losing the other.
  const beginEdit = (row: ExitRow) => {
    setEditingStrategy(row.strategy);
    setEditDraft({
      strategy: row.strategy,
      trim: Number(row.trim_percentage) || 1,
    });
    setExitsError(null);
  };

  const cancelEdit = () => {
    setEditingStrategy(null);
    setEditDraft({ strategy: "", trim: 1 });
  };

  // Save an edited row. Backend upserts by (symbol, strategy); if the user
  // changed the strategy name we have to delete the old row first, otherwise
  // we'd be left with two rows for the same logical exit.
  const handleSaveEdit = async (originalStrategy: string) => {
    if (!symbol) return;
    const { strategy: newStrategy, trim } = editDraft;
    if (!newStrategy) return;

    try {
      setMutatingStrategy(originalStrategy);
      setExitsError(null);

      if (newStrategy !== originalStrategy) {
        const delRes = await fetch(
          `${API_PREFIX}/exits/${encodeURIComponent(symbol)}/${encodeURIComponent(originalStrategy)}`,
          { method: "DELETE" },
        );
        // 404 on the delete is fine — the row may already be gone.
        if (!delRes.ok && delRes.status !== 404) {
          throw new Error(await delRes.text());
        }
      }

      const upsertRes = await fetch(`${API_PREFIX}/exits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          strategy: newStrategy,
          trim_percentage: trim,
        }),
      });
      if (!upsertRes.ok) {
        throw new Error(await upsertRes.text());
      }

      cancelEdit();
      await fetchExitRows();
    } catch (err: any) {
      console.error("Error saving exit edit:", err);
      setExitsError(`Failed to save exit: ${err.message || err}`);
    } finally {
      setMutatingStrategy(null);
    }
  };

  const handleDeleteExit = async (row: ExitRow) => {
    if (!symbol) return;
    if (
      !window.confirm(
        `Delete the ${row.strategy} exit for ${symbol}? This disarms it ` +
          `entirely — the streamer will no longer fire on this trigger.`,
      )
    ) {
      return;
    }

    try {
      setMutatingStrategy(row.strategy);
      setExitsError(null);
      const res = await fetch(
        `${API_PREFIX}/exits/${encodeURIComponent(symbol)}/${encodeURIComponent(row.strategy)}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(await res.text());
      if (editingStrategy === row.strategy) cancelEdit();
      await fetchExitRows();
    } catch (err: any) {
      console.error("Error deleting exit:", err);
      setExitsError(`Failed to delete exit: ${err.message || err}`);
    } finally {
      setMutatingStrategy(null);
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

      {/* Armed exit requests for this symbol — inline editable. IBKR sync
          of edits is intentionally NOT performed yet; this only mutates the
          stored exit_request record the streamer reads. */}
      <div className="mt-6">
        <h3 className="font-semibold mb-2">Armed Exits</h3>
        <p className="text-xs text-gray-500 mb-2">
          Edit strategy / trim % or delete a row. Changes only update the
          stored exit plan — IBKR orders are not modified here.
        </p>

        {exitsError && (
          <p className="text-sm text-red-600 mb-2">{exitsError}</p>
        )}

        {exitsLoading ? (
          <p className="text-sm text-gray-500">Loading...</p>
        ) : exitRows.length === 0 ? (
          <p className="text-sm text-red-600">
            No exit plan armed for {position.symbol}. This position is
            running without a documented exit — investigate immediately.
          </p>
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b">
                <th className="text-left py-1 pr-2">Strategy</th>
                <th className="text-left py-1 pr-2">Trim %</th>
                <th className="text-left py-1 pr-2">Updated</th>
                <th className="text-left py-1 pr-2 w-32">Actions</th>
              </tr>
            </thead>
            <tbody>
              {exitRows.map((row) => {
                const trimNum = Number(row.trim_percentage);
                const isEditing = editingStrategy === row.strategy;
                const isMutating = mutatingStrategy === row.strategy;

                // When editing, suppress this row's own strategy from the
                // duplicate-check so the user can keep the same strategy
                // (just changing trim). Other rows still block duplicates.
                const usedStrategies = new Set(
                  exitRows
                    .map((r) => r.strategy)
                    .filter((s) => s !== row.strategy),
                );

                return (
                  <tr key={row.strategy} className="border-b last:border-0">
                    <td className="py-1 pr-2">
                      {isEditing ? (
                        <select
                          value={editDraft.strategy}
                          onChange={(e) =>
                            setEditDraft((d) => ({
                              ...d,
                              strategy: e.target.value,
                            }))
                          }
                          className="border rounded px-2 py-0.5 text-sm"
                        >
                          {EXIT_STRATEGY_OPTIONS.filter(
                            (opt) =>
                              opt.value === row.strategy ||
                              !usedStrategies.has(opt.value),
                          ).map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      ) : (
                        row.strategy
                      )}
                    </td>
                    <td className="py-1 pr-2">
                      {isEditing ? (
                        <select
                          value={editDraft.trim}
                          onChange={(e) =>
                            setEditDraft((d) => ({
                              ...d,
                              trim: Number(e.target.value),
                            }))
                          }
                          className="border rounded px-2 py-0.5 text-sm"
                        >
                          {TRIM_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      ) : Number.isNaN(trimNum) ? (
                        row.trim_percentage
                      ) : (
                        formatTrimLabel(trimNum)
                      )}
                    </td>
                    <td className="py-1 pr-2">
                      {new Date(row.updated).toLocaleString()}
                    </td>
                    <td className="py-1 pr-2">
                      {isEditing ? (
                        <div className="flex gap-1">
                          <button
                            onClick={() => handleSaveEdit(row.strategy)}
                            disabled={isMutating || !editDraft.strategy}
                            className="text-xs bg-green-600 text-white px-2 py-0.5 rounded hover:bg-green-700 disabled:opacity-50"
                          >
                            {isMutating ? "Saving..." : "Save"}
                          </button>
                          <button
                            onClick={cancelEdit}
                            disabled={isMutating}
                            className="text-xs bg-gray-200 text-gray-700 px-2 py-0.5 rounded hover:bg-gray-300 disabled:opacity-50"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="flex gap-1">
                          <button
                            onClick={() => beginEdit(row)}
                            disabled={
                              isMutating || editingStrategy !== null
                            }
                            className="text-xs bg-blue-600 text-white px-2 py-0.5 rounded hover:bg-blue-700 disabled:opacity-50"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => handleDeleteExit(row)}
                            disabled={
                              isMutating || editingStrategy !== null
                            }
                            className="text-xs bg-red-600 text-white px-2 py-0.5 rounded hover:bg-red-700 disabled:opacity-50"
                          >
                            {isMutating ? "..." : "Delete"}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
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
