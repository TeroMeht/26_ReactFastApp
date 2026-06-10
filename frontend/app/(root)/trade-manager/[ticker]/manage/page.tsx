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

// Custom (user-defined) price-target exit. Backed by a real IB LIMIT
// order — no DB row. State comes straight from IB's open-order list,
// filtered by the CUSTOM_EXIT orderRef tag set at placement time.
type CustomExitRow = {
  symbol: string;
  contract_type: string;
  order_id: number;
  perm_id: number | null;
  target_price: number | string;
  trim_percentage: number | string | null;
  action: string;
  quantity: number;
  status: string;
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

  // `mutatingStrategy` disables a row's Delete button while the DELETE is
  // in flight. Inline edit was removed — to change a row, delete + re-add.
  const [mutatingStrategy, setMutatingStrategy] = useState<string | null>(null);
  const [exitsError, setExitsError] = useState<string | null>(null);

  // Add-form state for the always-visible Exit-plan form. addDraft is
  // committed on Arm Exit; addingExit blocks double-submits.
  const [addDraft, setAddDraft] = useState<{
    strategy: string;
    trim: number;
  }>({ strategy: "", trim: 1 });
  const [addingExit, setAddingExit] = useState(false);

  // --- Custom (price-target) exits ---------------------------------------
  // Hydrated from GET /api/exits/custom/{symbol}. Each row is a real IB
  // LIMIT order; cancelling a row also cancels the IB order. The fill
  // listener on the backend handles STP resize on fill.
  const [customExits, setCustomExits] = useState<CustomExitRow[]>([]);
  const [customLoading, setCustomLoading] = useState(false);
  const [customError, setCustomError] = useState<string | null>(null);
  const [customAdding, setCustomAdding] = useState(false);
  const [customDraft, setCustomDraft] = useState<{
    target_price: string;
    trim: number;
  }>({ target_price: "", trim: 1 });
  // Keyed on permId — that's what the backend's DELETE handler expects.
  const [customMutatingId, setCustomMutatingId] = useState<number | null>(null);

  // Move-stop-to-breakeven state - kept separate from the Add flow so the two
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
  // produce a fresh object on every render - that re-firing useCallback /
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

  // Pull all custom (price-target) exits for this symbol from the backend.
  const fetchCustomExits = useCallback(async () => {
    if (!symbol) return;
    try {
      setCustomLoading(true);
      const res = await fetch(
        `${API_PREFIX}/exits/custom/${encodeURIComponent(symbol)}`,
      );
      if (!res.ok) throw new Error(await res.text());
      const data: CustomExitRow[] = await res.json();
      setCustomExits(data);
    } catch (err: any) {
      console.error("Error fetching custom exits:", err);
      setCustomError(`Failed to load custom exits: ${err.message || err}`);
    } finally {
      setCustomLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    fetchCustomExits();
  }, [fetchCustomExits]);

  const handleAddCustomExit = async () => {
    if (!symbol) return;
    const targetNum = Number(customDraft.target_price);
    if (!targetNum || targetNum <= 0) {
      setCustomError("Enter a positive target price.");
      return;
    }
    try {
      setCustomAdding(true);
      setCustomError(null);
      const res = await fetch(`${API_PREFIX}/exits/custom`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          target_price: targetNum,
          trim_percentage: customDraft.trim,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      setCustomDraft({ target_price: "", trim: 1 });
      await fetchCustomExits();
    } catch (err: any) {
      console.error("Error adding custom exit:", err);
      setCustomError(`Failed to arm custom exit: ${err.message || err}`);
    } finally {
      setCustomAdding(false);
    }
  };

  const handleCancelCustomExit = async (row: CustomExitRow) => {
    // Backend cancels by IB permId. Fall back to order_id when perm_id
    // isn't yet populated (very early lifecycle of a fresh placement).
    const cancelId = row.perm_id ?? row.order_id;
    if (!cancelId) {
      setCustomError("Cannot cancel: order has no IB id yet.");
      return;
    }
    try {
      setCustomMutatingId(cancelId);
      setCustomError(null);
      const res = await fetch(
        `${API_PREFIX}/exits/custom/${cancelId}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(await res.text());
      await fetchCustomExits();
    } catch (err: any) {
      console.error("Error cancelling custom exit:", err);
      setCustomError(`Failed to cancel custom exit: ${err.message || err}`);
    } finally {
      setCustomMutatingId(null);
    }
  };

  // POST a new exit_request row. Backend upserts by (symbol, strategy);
  // we still guard against arming the same strategy twice client-side
  // so the user gets a clearer message than a silent overwrite.
  const handleAddExit = async () => {
    if (!symbol) return;
    const { strategy, trim } = addDraft;
    if (!strategy) return;

    if (exitRows.some((r) => r.strategy === strategy)) {
      setExitsError(
        `${strategy} is already armed for ${symbol}. Delete it and re-add to change.`,
      );
      return;
    }

    try {
      setAddingExit(true);
      setExitsError(null);
      const res = await fetch(`${API_PREFIX}/exits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          strategy,
          trim_percentage: trim,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      setAddDraft({ strategy: "", trim: 1 });
      await fetchExitRows();
    } catch (err: any) {
      console.error("Error adding exit:", err);
      setExitsError(`Failed to add exit: ${err.message || err}`);
    } finally {
      setAddingExit(false);
    }
  };

  const handleDeleteExit = async (row: ExitRow) => {
    if (!symbol) return;

    try {
      setMutatingStrategy(row.strategy);
      setExitsError(null);
      const res = await fetch(
        `${API_PREFIX}/exits/${encodeURIComponent(symbol)}/${encodeURIComponent(row.strategy)}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(await res.text());
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
        Position Management - {position.symbol}
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

      {/* Strategy-based exit plans for this symbol. Layout mirrors the
          Custom Price Exits section below: always-visible add form on top,
          read-only table beneath. To change a row, delete + re-add. */}
      <div className="mt-6">
        <h3 className="font-semibold mb-2">Exit plans</h3>

        {exitsError && (
          <p className="text-sm text-red-600 mb-2">{exitsError}</p>
        )}

        {(() => {
          const used = new Set(exitRows.map((r) => r.strategy));
          const available = EXIT_STRATEGY_OPTIONS.filter(
            (o) => !used.has(o.value),
          );
          const selectedStrategy =
            addDraft.strategy && available.some((o) => o.value === addDraft.strategy)
              ? addDraft.strategy
              : (available[0]?.value ?? "");
          return (
            <div className="mb-3 p-2 border rounded bg-gray-50 flex flex-wrap items-end gap-2">
              <div>
                <label className="block text-xs text-gray-600">Strategy</label>
                <select
                  value={selectedStrategy}
                  onChange={(e) =>
                    setAddDraft((d) => ({ ...d, strategy: e.target.value }))
                  }
                  disabled={available.length === 0}
                  className="border rounded px-2 py-0.5 text-sm w-48"
                >
                  {available.length === 0 ? (
                    <option value="">All strategies armed</option>
                  ) : (
                    available.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))
                  )}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-600">Trim %</label>
                <select
                  value={addDraft.trim}
                  onChange={(e) =>
                    setAddDraft((d) => ({
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
              </div>
              <button
                onClick={() => {
                  // Commit the implicit default ("first available") to
                  // state if the user never touched the dropdown.
                  if (addDraft.strategy !== selectedStrategy) {
                    setAddDraft((d) => ({ ...d, strategy: selectedStrategy }));
                  }
                  handleAddExit();
                }}
                disabled={addingExit || !selectedStrategy}
                className="text-xs bg-green-600 text-white px-2 py-1 rounded hover:bg-green-700 disabled:opacity-50"
              >
                {addingExit ? "Adding..." : "Add Exit"}
              </button>
            </div>
          );
        })()}

        {exitsLoading ? (
          <p className="text-sm text-gray-500">Loading...</p>
        ) : exitRows.length === 0 ? null : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b">
                <th className="text-left py-1 pr-2">Strategy</th>
                <th className="text-left py-1 pr-2">Trim %</th>
                <th className="text-left py-1 pr-2">Updated</th>
                <th className="text-left py-1 pr-2 w-24">Actions</th>
              </tr>
            </thead>
            <tbody>
              {exitRows.map((row) => {
                const trimNum = Number(row.trim_percentage);
                const isMutating = mutatingStrategy === row.strategy;
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
                    <td className="py-1 pr-2">
                      <button
                        onClick={() => handleDeleteExit(row)}
                        disabled={isMutating}
                        className="text-xs bg-red-600 text-white px-2 py-0.5 rounded hover:bg-red-700 disabled:opacity-50"
                      >
                        {isMutating ? "..." : "Delete"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Custom price-target exits - real IB LIMIT orders. When IB fills
          one, the backend resizes the STP (or cancels it on a 100% trim)
          using the same flow as strategy-based exits. */}
      <div className="mt-6">
        <h3 className="font-semibold mb-2">Custom Price Exits</h3>

        {customError && (
          <p className="text-sm text-red-600 mb-2">{customError}</p>
        )}

        <div className="mb-3 p-2 border rounded bg-gray-50 flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-xs text-gray-600">Target Price</label>
            <input
              type="number"
              step="0.01"
              value={customDraft.target_price}
              onChange={(e) =>
                setCustomDraft((d) => ({ ...d, target_price: e.target.value }))
              }
              className="border rounded px-2 py-0.5 text-sm w-48"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-600">Trim %</label>
            <select
              value={customDraft.trim}
              onChange={(e) =>
                setCustomDraft((d) => ({ ...d, trim: Number(e.target.value) }))
              }
              className="border rounded px-2 py-0.5 text-sm"
            >
              {TRIM_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={handleAddCustomExit}
            disabled={customAdding || !customDraft.target_price}
            className="text-xs bg-green-600 text-white px-2 py-1 rounded hover:bg-green-700 disabled:opacity-50"
          >
            {customAdding ? "Adding..." : "Add Exit"}
          </button>
        </div>

        {customLoading ? (
          <p className="text-sm text-gray-500">Loading...</p>
        ) : customExits.length === 0 ? (
          <p className="text-sm text-gray-500"></p>
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b">
                <th className="text-left py-1 pr-2">Target</th>
                <th className="text-left py-1 pr-2">Trim %</th>
                <th className="text-left py-1 pr-2">Side</th>
                <th className="text-left py-1 pr-2">Qty</th>
                <th className="text-left py-1 pr-2">Status</th>
                <th className="text-left py-1 pr-2 w-24">Actions</th>
              </tr>
            </thead>
            <tbody>
              {customExits.map((row) => {
                const trimNum = Number(row.trim_percentage);
                const targetNum = Number(row.target_price);
                const rowKey = row.perm_id ?? row.order_id;
                const isMutating = customMutatingId === rowKey;
                // IB returns statuses like "Submitted", "PreSubmitted",
                // "PendingSubmit"; treat anything that isn't terminal as
                // cancellable. The fresh "armed" string from the POST
                // response is also valid.
                const terminal = new Set([
                  "filled",
                  "cancelled",
                  "apicancelled",
                  "inactive",
                ]);
                const canCancel = !terminal.has(
                  (row.status || "").toLowerCase(),
                );
                return (
                  <tr key={rowKey} className="border-b last:border-0">
                    <td className="py-1 pr-2">
                      {Number.isNaN(targetNum)
                        ? row.target_price
                        : targetNum.toFixed(2)}
                    </td>
                    <td className="py-1 pr-2">
                      {row.trim_percentage == null || Number.isNaN(trimNum)
                        ? "-"
                        : formatTrimLabel(trimNum)}
                    </td>
                    <td className="py-1 pr-2">{row.action}</td>
                    <td className="py-1 pr-2">{row.quantity}</td>
                    <td className="py-1 pr-2">{row.status}</td>
                    <td className="py-1 pr-2">
                      <button
                        onClick={() => handleCancelCustomExit(row)}
                        disabled={!canCancel || isMutating}
                        className="text-xs bg-red-600 text-white px-2 py-0.5 rounded hover:bg-red-700 disabled:opacity-50"
                      >
                        {isMutating ? "..." : "Cancel"}
                      </button>
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

        {/* Move stop to breakeven - live IB modify, no Total Risk needed. */}
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

      {/* Move-stop-to-BE result - green on success, red on error.  Kept in a
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
