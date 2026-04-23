"use client";

import React, { useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { OpenPosition } from "@/lib/types";
import { API_PREFIX } from "@/lib/api_prefix";

const TRIM_OPTIONS = [
  { value: 0.25, label: "25% (trim)" },
  { value: 0.5, label: "50% (trim)" },
  { value: 0.75, label: "75% (trim)" },
  { value: 1, label: "100% (full exit)" },
];

const ManagePage = () => {
  const params = useSearchParams();
  const dataParam = params.get("data");

  const [totalRisk, setTotalRisk] = useState("");
  const [responseData, setResponseData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exitSwitchOn, setExitSwitchOn] = useState(false);
  const [trimPercentage, setTrimPercentage] = useState<number>(1);
  const [updatingExit, setUpdatingExit] = useState(false);

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

  let position: OpenPosition | null = null;

  if (dataParam) {
    try {
      position = JSON.parse(atob(decodeURIComponent(dataParam))) as OpenPosition;
    } catch (err) {
      console.error("Failed to parse position data:", err);
    }
  }

  const handleClose = () => window.history.back();

  // Fetch initial exit state
  useEffect(() => {
    const fetchExitState = async () => {
      if (!position) return;
      try {
        const res = await fetch(`${API_PREFIX}/exits`);
        if (!res.ok) throw new Error("Failed to fetch exit state");
        const data: {
          symbol: string;
          exitrequested: boolean;
          trim_percentage?: number | string;
          updated: string;
        }[] = await res.json();
        const match = data.find((item) => item.symbol === position!.symbol);
        if (match) {
          setExitSwitchOn(match.exitrequested);
          if (match.trim_percentage !== undefined && match.trim_percentage !== null) {
            const parsed = Number(match.trim_percentage);
            if (!Number.isNaN(parsed)) setTrimPercentage(parsed);
          }
        }
      } catch (err: any) {
        console.error("Error fetching exit state:", err);
      }
    };
    fetchExitState();
  }, [position]);

  const persistExitRequest = async (
    requested: boolean,
    trim: number,
  ) => {
    if (!position) return;
    const res = await fetch(`${API_PREFIX}/exits`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: position.symbol,
        requested,
        trim_percentage: trim,
      }),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(text);
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

  const toggleExitSwitch = async () => {
    if (!position) return;

    const newValue = !exitSwitchOn;
    try {
      setUpdatingExit(true);
      await persistExitRequest(newValue, trimPercentage);
      setExitSwitchOn(newValue);
    } catch (err: any) {
      setError(`Exit update failed: ${err.message || err}`);
    } finally {
      setUpdatingExit(false);
    }
  };

  const handleTrimChange = async (
    e: React.ChangeEvent<HTMLSelectElement>,
  ) => {
    const next = Number(e.target.value);
    setTrimPercentage(next);

    // If exit is already armed, update the row server-side right away so the
    // backend process_exit_request sees the latest trim when an alarm fires.
    if (exitSwitchOn) {
      try {
        setUpdatingExit(true);
        await persistExitRequest(exitSwitchOn, next);
      } catch (err: any) {
        setError(`Trim update failed: ${err.message || err}`);
      } finally {
        setUpdatingExit(false);
      }
    }
  };

  if (!position) return <div>Loading position...</div>;

  return (
    <div className="p-6 max-w-md mx-auto bg-white shadow-lg rounded-md mt-10">
      <h2 className="text-lg font-semibold mb-4">
        Position Management – {position.symbol}
      </h2>

      <div className="space-y-2 text-left">
        {/* Exit Switch */}
        <div className="flex items-center gap-2">
          <strong>Exit Request:</strong>
          <button
            onClick={toggleExitSwitch}
            disabled={updatingExit}
            className={`w-14 h-7 rounded-full relative transition-colors duration-200 focus:outline-none ${
              exitSwitchOn ? "bg-blue-600" : "bg-gray-400"
            }`}
          >
            <span
              className={`block w-6 h-6 bg-white rounded-full shadow-md transform transition-transform duration-200 ${
                exitSwitchOn ? "translate-x-7" : "translate-x-0"
              }`}
            />
          </button>
        </div>

        {/* Trim Percentage Dropdown */}
        <div className="flex items-center gap-2">
          <label htmlFor="trim-percentage" className="font-bold">
            Trim %:
          </label>
          <select
            id="trim-percentage"
            value={trimPercentage}
            onChange={handleTrimChange}
            disabled={updatingExit}
            className="border rounded px-2 py-1"
          >
            {TRIM_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        <p><strong>Contract:</strong> {position.contract_type}</p>
        <p><strong>Aux Price:</strong> {position.auxprice}</p>
        <p><strong>Avg Cost:</strong> {position.avgcost}</p>
        <p><strong>Position:</strong> {position.position}</p>
        <p><strong>Open Risk:</strong> {position.openrisk}</p>
        <p><strong>Allocation:</strong> {position.allocation}</p>
        <p><strong>Size:</strong> {position.size}</p>
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
