"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { API_PREFIX } from "@/lib/api_prefix";

import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";

type OrderLogEntry = {
  ts: number;
  perm_id: number;
  order_id: number;
  symbol: string | null;
  action: string | null;
  order_type: string | null;
  total_qty: number;
  lmt_price: number | null;
  aux_price: number | null;
  status: string | null;
  filled: number;
  remaining: number;
  avg_fill_price: number;
  last_error: string | null;
  last_error_code: number | null;
};

const STATUS_FILTERS = [
  "All",
  "Filled",
  "Cancelled",
  "Submitted",
  "PreSubmitted",
  "Inactive",
  "Errors",
] as const;

type StatusFilter = (typeof STATUS_FILTERS)[number];

const statusStyle = (status: string | null): string => {
  switch (status) {
    case "Filled":
      return "bg-green-100 text-green-800 border-green-300";
    case "Cancelled":
    case "ApiCancelled":
    case "Inactive":
      return "bg-gray-200 text-gray-700 border-gray-300";
    case "Submitted":
      return "bg-yellow-100 text-yellow-800 border-yellow-300";
    case "PreSubmitted":
    case "PendingSubmit":
    case "ApiPending":
      return "bg-blue-100 text-blue-800 border-blue-300";
    case "PendingCancel":
      return "bg-orange-100 text-orange-800 border-orange-300";
    default:
      return "bg-gray-100 text-gray-700 border-gray-200";
  }
};

const fmtTime = (ts: number): string => {
  if (!ts) return "—";
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleString();
  } catch {
    return String(ts);
  }
};

const OrderLogTable = () => {
  const [entries, setEntries] = useState<OrderLogEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<StatusFilter>("All");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchLog = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_PREFIX}/portfolio/order-log`);
      if (!res.ok) {
        console.error("Order log fetch failed:", res.statusText);
        setEntries([]);
        return;
      }
      const data = (await res.json()) as OrderLogEntry[];
      setEntries(data);
    } catch (err) {
      console.error("Order log fetch error:", err);
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLog();
  }, [fetchLog]);

  // Soft auto-refresh — pull every 5s so the page stays current without
  // needing SSE. Cheap because the log is in-memory on the backend.
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(fetchLog, 5000);
    return () => clearInterval(id);
  }, [autoRefresh, fetchLog]);

  const filtered = useMemo(() => {
    const sym = symbolFilter.trim().toUpperCase();
    return entries.filter((e) => {
      if (sym && (e.symbol ?? "").toUpperCase() !== sym) return false;

      if (filter === "All") return true;
      if (filter === "Errors") return !!e.last_error;
      if (filter === "Cancelled") {
        return e.status === "Cancelled" || e.status === "ApiCancelled";
      }
      return e.status === filter;
    });
  }, [entries, filter, symbolFilter]);

  return (
    <div className="py-4">
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <Button variant="outline" onClick={fetchLog} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </Button>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          Auto-refresh (5s)
        </label>

        <div className="flex items-center gap-2 text-sm">
          <span>Status:</span>
          {STATUS_FILTERS.map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`px-2 py-1 text-xs rounded-md border ${
                filter === s
                  ? "bg-gray-800 text-white border-gray-800"
                  : "bg-white text-gray-700 border-gray-300 hover:bg-gray-100"
              }`}
            >
              {s}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-2 text-sm">
          <span>Symbol:</span>
          <input
            type="text"
            value={symbolFilter}
            onChange={(e) => setSymbolFilter(e.target.value)}
            placeholder="e.g. AAPL"
            className="border border-gray-300 rounded-md px-2 py-1 text-sm w-32"
          />
        </div>

        <span className="text-xs text-gray-500 ml-auto">
          {filtered.length} of {entries.length} events
        </span>
      </div>

      <Table className="w-full table-auto">
        <TableHeader>
          <TableRow>
            <TableHead>Time</TableHead>
            <TableHead>Perm ID</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Side</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Qty</TableHead>
            <TableHead>Filled / Rem</TableHead>
            <TableHead>Price</TableHead>
            <TableHead>Avg Fill</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Error</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {filtered.length === 0 ? (
            <TableRow>
              <TableCell colSpan={11} className="text-gray-500">
                No order activity recorded yet.
              </TableCell>
            </TableRow>
          ) : (
            filtered.map((e, idx) => (
              <TableRow key={`${e.perm_id}-${e.ts}-${idx}`}>
                <TableCell className="font-mono text-xs whitespace-nowrap">
                  {fmtTime(e.ts)}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {e.perm_id || `(oid ${e.order_id})`}
                </TableCell>
                <TableCell>{e.symbol || "—"}</TableCell>
                <TableCell>{e.action || "—"}</TableCell>
                <TableCell>{e.order_type || "—"}</TableCell>
                <TableCell>{e.total_qty}</TableCell>
                <TableCell>
                  {e.filled} / {e.remaining}
                </TableCell>
                <TableCell>{e.lmt_price ?? e.aux_price ?? "—"}</TableCell>
                <TableCell>{e.avg_fill_price || "—"}</TableCell>
                <TableCell>
                  <span
                    className={`inline-block px-2 py-0.5 text-xs rounded-full border ${statusStyle(
                      e.status
                    )}`}
                  >
                    {e.status || "—"}
                  </span>
                </TableCell>
                <TableCell className="text-xs text-red-700">
                  {e.last_error
                    ? `${e.last_error_code ?? ""} ${e.last_error}`.trim()
                    : ""}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default OrderLogTable;
