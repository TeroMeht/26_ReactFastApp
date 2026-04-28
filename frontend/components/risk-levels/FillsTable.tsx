"use client";

import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
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

/**
 * One row per IB order seen today. Matches the shape produced by
 * backend/services/fills.py::_row_from_trade. Fetched fresh every 30s
 * from GET /api/portfolio/fills — no streaming.
 */
type FillRow = {
  orderId?: number | null;
  permId?: number | null;
  parentId?: number | null;
  symbol?: string | null;
  secType?: string | null;
  action?: string | null;
  orderType?: string | null;
  totalQty?: number | null;
  lmtPrice?: number | null;
  auxPrice?: number | null;
  status?: string | null;
  filled?: number | null;
  remaining?: number | null;
  avgFillPrice?: number | null;
  commission?: number | null;
  lastFillTime?: string | null;
  createdTime?: string | null;
};

const STATUS_CLASSES: Record<string, string> = {
  Filled: "bg-green-100 text-green-800",
  PartiallyFilled: "bg-amber-100 text-amber-800",
  Submitted: "bg-blue-100 text-blue-800",
  PreSubmitted: "bg-blue-100 text-blue-800",
  PendingSubmit: "bg-blue-100 text-blue-800",
  ApiPending: "bg-blue-100 text-blue-800",
  Cancelled: "bg-red-100 text-red-800",
  ApiCancelled: "bg-red-100 text-red-800",
  Inactive: "bg-gray-200 text-gray-700",
};

const statusClass = (status?: string | null) =>
  (status && STATUS_CLASSES[status]) || "bg-gray-100 text-gray-800";

const rowKey = (row: FillRow) =>
  String(row.permId ?? row.orderId ?? `${row.symbol}-${row.createdTime ?? ""}`);

// Always render times in Helsinki / Finnish time and 24-hour format so the
// fills column is consistent regardless of the user's browser locale or
// machine timezone (e.g. avoids "08:23:59 AM" / "11:23:58 AM" mixed style).
const HELSINKI_TIME_FORMAT: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
  timeZone: "Europe/Helsinki",
};

const formatTime = (iso?: string | null) => {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("en-GB", HELSINKI_TIME_FORMAT);
};

const formatPrice = (v?: number | null) =>
  v === null || v === undefined ? "-" : Number(v).toFixed(4);

// Poll the snapshot endpoint every 30s. Single source of truth.
const POLL_INTERVAL_MS = 30_000;

// Status options shown in the dropdown even before a matching row arrives.
const KNOWN_STATUSES = [
  "PendingSubmit",
  "ApiPending",
  "PreSubmitted",
  "Submitted",
  "PartiallyFilled",
  "Filled",
  "Cancelled",
  "ApiCancelled",
  "Inactive",
] as const;

type FilterKey = "status" | "action" | "type";

const FillsTable = () => {
  const [rows, setRows] = useState<FillRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // ---- Filter state ---------------------------------------------------
  const [statusFilter, setStatusFilter] = useState<Set<string>>(new Set());
  const [symbolFilter, setSymbolFilter] = useState<string>("");
  const [actionFilter, setActionFilter] = useState<string>("");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [openFilter, setOpenFilter] = useState<FilterKey | null>(null);
  const filterBarRef = useRef<HTMLDivElement | null>(null);

  // Close any open filter popover when the user clicks outside the filter bar.
  useEffect(() => {
    if (!openFilter) return;
    const onDocClick = (e: MouseEvent) => {
      if (
        filterBarRef.current &&
        !filterBarRef.current.contains(e.target as Node)
      ) {
        setOpenFilter(null);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [openFilter]);

  // ---- Data fetching --------------------------------------------------
  const fetchFills = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_PREFIX}/portfolio/fills`);
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const json = (await res.json()) as FillRow[];
      setRows(json);
      setError(null);
      setLastUpdated(new Date());
    } catch (err: unknown) {
      console.error("Failed to fetch fills:", err);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load + 30s polling.
  useEffect(() => {
    fetchFills();
    const id = setInterval(fetchFills, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [fetchFills]);

  // ---- Derived: options + filtered rows -------------------------------
  const availableStatuses = useMemo(() => {
    const set = new Set<string>(KNOWN_STATUSES);
    for (const r of rows) if (r.status) set.add(r.status);
    return Array.from(set);
  }, [rows]);

  const availableActions = useMemo(() => {
    const set = new Set<string>();
    for (const r of rows) if (r.action) set.add(r.action);
    return Array.from(set).sort();
  }, [rows]);

  const availableTypes = useMemo(() => {
    const set = new Set<string>();
    for (const r of rows) if (r.orderType) set.add(r.orderType);
    return Array.from(set).sort();
  }, [rows]);

  const filteredRows = useMemo(() => {
    const symbolQuery = symbolFilter.trim().toUpperCase();
    return rows.filter((r) => {
      if (statusFilter.size > 0 && !statusFilter.has(r.status ?? "")) return false;
      if (actionFilter && r.action !== actionFilter) return false;
      if (typeFilter && r.orderType !== typeFilter) return false;
      if (symbolQuery && !(r.symbol ?? "").toUpperCase().includes(symbolQuery)) {
        return false;
      }
      return true;
    });
  }, [rows, statusFilter, actionFilter, typeFilter, symbolFilter]);

  const anyFilterActive =
    statusFilter.size > 0 ||
    symbolFilter.trim().length > 0 ||
    actionFilter !== "" ||
    typeFilter !== "";

  const clearFilters = () => {
    setStatusFilter(new Set());
    setSymbolFilter("");
    setActionFilter("");
    setTypeFilter("");
    setOpenFilter(null);
  };

  const toggleStatus = (status: string) => {
    setStatusFilter((prev) => {
      const next = new Set(prev);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      return next;
    });
  };

  const statusButtonLabel = (() => {
    if (statusFilter.size === 0) return "All statuses";
    if (statusFilter.size === 1) return Array.from(statusFilter)[0];
    return `${statusFilter.size} selected`;
  })();

  const lastUpdatedLabel = lastUpdated
    ? `Updated ${lastUpdated.toLocaleTimeString("en-GB", HELSINKI_TIME_FORMAT)}`
    : "Never updated";

  // ---- Render ---------------------------------------------------------
  return (
    <div className="py-4">
      <div className="flex items-center gap-3 mb-4">
        <h2 className="text-xl font-bold">Fills</h2>
        <span className="text-xs px-2 py-0.5 rounded-md bg-gray-100 text-gray-700">
          Auto-refresh every 30s
        </span>
        <span className="text-xs text-gray-500">{lastUpdatedLabel}</span>

        <Button
          variant="outline"
          onClick={fetchFills}
          disabled={loading}
          className="ml-1"
        >
          {loading ? "Refreshing..." : "Refresh"}
        </Button>
      </div>

      {/* Filter bar */}
      <div
        ref={filterBarRef}
        className="flex flex-wrap items-center gap-2 mb-3"
      >
        {/* Status filter (multi-select) */}
        <div className="relative">
          <button
            className="px-3 py-1 text-sm rounded-md border border-input bg-gray-200 hover:bg-gray-400 transition-colors"
            onClick={() =>
              setOpenFilter(openFilter === "status" ? null : "status")
            }
          >
            Status: {statusButtonLabel}
          </button>
          {openFilter === "status" && (
            <div className="absolute z-50 mt-1 w-56 rounded-md border border-input bg-white shadow-md max-h-72 overflow-auto">
              <div className="flex items-center justify-between px-3 py-2 border-b border-input">
                <span className="text-xs text-gray-600">Select statuses</span>
                {statusFilter.size > 0 && (
                  <button
                    className="text-xs text-blue-600 hover:underline"
                    onClick={() => setStatusFilter(new Set())}
                  >
                    Clear
                  </button>
                )}
              </div>
              {availableStatuses.map((status) => {
                const checked = statusFilter.has(status);
                return (
                  <label
                    key={status}
                    className={`flex items-center gap-2 px-3 py-2 text-sm cursor-pointer ${
                      checked ? "bg-gray-100" : "hover:bg-gray-100"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleStatus(status)}
                    />
                    <span
                      className={`inline-block px-2 py-0.5 rounded-md text-xs ${statusClass(
                        status
                      )}`}
                    >
                      {status}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </div>

        {/* Action filter */}
        <div className="relative">
          <button
            className="px-3 py-1 text-sm rounded-md border border-input bg-gray-200 hover:bg-gray-400 transition-colors"
            onClick={() =>
              setOpenFilter(openFilter === "action" ? null : "action")
            }
          >
            Action: {actionFilter || "All"}
          </button>
          {openFilter === "action" && (
            <div className="absolute z-50 mt-1 w-36 rounded-md border border-input bg-white shadow-md">
              {["", ...availableActions].map((opt) => (
                <button
                  key={opt || "all"}
                  className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                    actionFilter === opt
                      ? "bg-gray-200 text-primary font-medium"
                      : "text-foreground hover:bg-gray-200"
                  }`}
                  onClick={() => {
                    setActionFilter(opt);
                    setOpenFilter(null);
                  }}
                >
                  {opt || "All"}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Type filter */}
        <div className="relative">
          <button
            className="px-3 py-1 text-sm rounded-md border border-input bg-gray-200 hover:bg-gray-400 transition-colors"
            onClick={() =>
              setOpenFilter(openFilter === "type" ? null : "type")
            }
          >
            Type: {typeFilter || "All"}
          </button>
          {openFilter === "type" && (
            <div className="absolute z-50 mt-1 w-36 rounded-md border border-input bg-white shadow-md">
              {["", ...availableTypes].map((opt) => (
                <button
                  key={opt || "all"}
                  className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                    typeFilter === opt
                      ? "bg-gray-200 text-primary font-medium"
                      : "text-foreground hover:bg-gray-200"
                  }`}
                  onClick={() => {
                    setTypeFilter(opt);
                    setOpenFilter(null);
                  }}
                >
                  {opt || "All"}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Symbol filter (free-text) */}
        <input
          type="text"
          value={symbolFilter}
          onChange={(e) => setSymbolFilter(e.target.value)}
          placeholder="Symbol"
          className="px-3 py-1 text-sm rounded-md border border-input bg-white w-32 focus:outline-none focus:ring-2 focus:ring-blue-400"
        />

        {anyFilterActive && (
          <Button variant="ghost" onClick={clearFilters}>
            Clear filters
          </Button>
        )}

        <span className="ml-auto text-xs text-gray-500">
          {filteredRows.length} / {rows.length} rows
        </span>
      </div>

      {error && (
        <div className="mt-2 p-2 bg-red-100 text-red-800 rounded-md text-sm">
          {error}
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Time</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Action</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Qty</TableHead>
            <TableHead>Filled</TableHead>
            <TableHead>Remaining</TableHead>
            <TableHead>Avg Fill</TableHead>
            <TableHead>Lmt / Aux</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {filteredRows.length === 0 ? (
            <TableRow>
              <TableCell colSpan={10} className="text-gray-500">
                {rows.length === 0
                  ? "No orders sent today."
                  : "No orders match the current filters."}
              </TableCell>
            </TableRow>
          ) : (
            filteredRows.map((row) => (
              <TableRow key={rowKey(row)}>
                <TableCell>
                  {formatTime(row.lastFillTime || row.createdTime)}
                </TableCell>
                <TableCell>{row.symbol ?? "-"}</TableCell>
                <TableCell>{row.action ?? "-"}</TableCell>
                <TableCell>{row.orderType ?? "-"}</TableCell>
                <TableCell>{row.totalQty ?? "-"}</TableCell>
                <TableCell>{row.filled ?? 0}</TableCell>
                <TableCell>{row.remaining ?? 0}</TableCell>
                <TableCell>{formatPrice(row.avgFillPrice)}</TableCell>
                <TableCell>
                  {row.lmtPrice
                    ? formatPrice(row.lmtPrice)
                    : row.auxPrice
                    ? formatPrice(row.auxPrice)
                    : "-"}
                </TableCell>
                <TableCell>
                  <span
                    className={`inline-block px-2 py-0.5 rounded-md text-xs ${statusClass(
                      row.status
                    )}`}
                  >
                    {row.status ?? "-"}
                  </span>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default FillsTable;
