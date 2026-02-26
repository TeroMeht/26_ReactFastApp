"use client";

import React, { useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { OpenPosition } from "@/lib/types";
import { API_PREFIX } from "@/lib/api_prefix";

const ManagePage = () => {
  const params = useSearchParams();
  const dataParam = params.get("data");

  const [totalRisk, setTotalRisk] = useState("");
  const [responseData, setResponseData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exitSwitchOn, setExitSwitchOn] = useState(false);
  const [updatingExit, setUpdatingExit] = useState(false);

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
        const data: { symbol: string; exitrequested: boolean; updated: string }[] =
          await res.json();
        const match = data.find((item) => item.symbol === position!.symbol);
        if (match) setExitSwitchOn(match.exitrequested);
      } catch (err: any) {
        console.error("Error fetching exit state:", err);
      }
    };
    fetchExitState();
  }, [position]);

  const handleAdd = async () => {
    if (!position) return;

    try {
      setLoading(true);
      setError(null);
      setResponseData(null);

      const payload = {
        symbol: position.symbol,
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
      const res = await fetch(`${API_PREFIX}/exits`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: position.symbol,
          requested: newValue,
        }),
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(text);
      }

      setExitSwitchOn(newValue);
    } catch (err: any) {
      setError(`Exit update failed: ${err.message || err}`);
    } finally {
      setUpdatingExit(false);
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
      <div className="mt-6 flex justify-center gap-4">
        <button
          onClick={handleAdd}
          disabled={loading || !totalRisk}
          className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700 disabled:opacity-50"
        >
          {loading ? "Adding..." : "Add"}
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
    </div>
  );
};

export default ManagePage;