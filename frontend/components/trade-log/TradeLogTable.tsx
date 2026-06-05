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
  TableFooter,
} from "@/components/ui/table";

// Mirrors backend schemas TradeLogRow / TradeLogResponse. Each row is one
// symbol with realized PnL today, aggregated from IB's per-fill realizedPNL.
type TradeLogRow = {
  symbol: string;
  realized_pnl: number;
  commission: number;
  net_pnl: number;
  fills: number;
  last_fill_time: string | null;
  is_loss: boolean;
};

type TradeLogResponse = {
  rows: TradeLogRow[];
  realized_pnl: number;
  total_commission: number;
  net_pnl: number;
  symbol_count: number;
};

const PNL_FILTERS = ["All", "Winners", "Losers"] as const;
type PnlFilter = (typeof PNL_FILTERS)[number];

const fmtTime = (iso: string | null): string => {
  if (!iso) return "-";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
  } catch {
    return iso;
  }
};

const fmtMoney = (n: number, digits = 2): string => {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
};

const pnlClass = (n: number): string => {
  if (n > 0) return "text-green-700 font-semibold";
  if (n < 0) return "text-red-600 font-semibold";
  return "";
};

type Props = {
  refreshSignal?: number;
  onLoadingChange?: (loading: boolean) => void;
};

const TradeLogTable = ({ refreshSignal = 0, onLoadingChange }: Props) => {
  const [data, setData] = useState<TradeLogResponse | null>(null);
  const [filter, setFilter] = useState<PnlFilter>("All");
  const [symbolFilter, setSymbolFilter] = useState("");

  const fetchTrades = useCallback(async () => {
    try {
      onLoadingChange?.(true);
      const res = await fetch(`${API_PREFIX}/portfolio/trade-log`);
      if (!res.ok) {
        console.error("Trade log fetch failed:", res.statusText);
        setData(null);
        return;
      }
      const json = (await res.json()) as TradeLogResponse;
      setData(json);
    } catch (err) {
      console.error("Trade log fetch error:", err);
      setData(null);
    } finally {
      onLoadingChange?.(false);
    }
  }, [onLoadingChange]);

  useEffect(() => {
    fetchTrades();
  }, [fetchTrades, refreshSignal]);

  const rows = data?.rows ?? [];

  const filtered = useMemo(() => {
    const sym = symbolFilter.trim().toUpperCase();
    return rows.filter((r) => {
      if (sym && (r.symbol ?? "").toUpperCase() !== sym) return false;
      if (filter === "All") return true;
      if (filter === "Winners") return r.net_pnl > 0;
      if (filter === "Losers") return r.net_pnl < 0;
      return true;
    });
  }, [rows, filter, symbolFilter]);

  const realizedSum = filtered.reduce((acc, r) => acc + r.realized_pnl, 0);
  const commSum = filtered.reduce((acc, r) => acc + r.commission, 0);
  const netSum = filtered.reduce((acc, r) => acc + r.net_pnl, 0);

  return (
    <div className="py-4">
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex items-center gap-2 text-sm">
          <span>PnL:</span>
          {PNL_FILTERS.map((s) => (
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
          {filtered.length} of {rows.length} symbols
        </span>
      </div>

      <Table className="w-full table-auto">
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead className="text-right">Realized PnL</TableHead>
            <TableHead className="text-right">Commission</TableHead>
            <TableHead className="text-right">Net PnL</TableHead>
            <TableHead className="text-right">Fills</TableHead>
            <TableHead>Last fill</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {filtered.length === 0 ? (
            <TableRow>
              <TableCell colSpan={6} className="text-gray-500">
                No realized PnL today.
              </TableCell>
            </TableRow>
          ) : (
            filtered.map((r) => (
              <TableRow key={r.symbol}>
                <TableCell className="font-medium">{r.symbol}</TableCell>
                <TableCell className={`text-right ${pnlClass(r.realized_pnl)}`}>
                  {fmtMoney(r.realized_pnl)}
                </TableCell>
                <TableCell className="text-right">
                  {fmtMoney(r.commission)}
                </TableCell>
                <TableCell className={`text-right ${pnlClass(r.net_pnl)}`}>
                  {fmtMoney(r.net_pnl)}
                </TableCell>
                <TableCell className="text-right">{r.fills}</TableCell>
                <TableCell className="font-mono text-xs whitespace-nowrap">
                  {fmtTime(r.last_fill_time)}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>

        {filtered.length > 0 && (
          <TableFooter>
            <TableRow>
              <TableCell className="font-semibold">Totals</TableCell>
              <TableCell className={`text-right ${pnlClass(realizedSum)}`}>
                {fmtMoney(realizedSum)}
              </TableCell>
              <TableCell className="text-right font-semibold">
                {fmtMoney(commSum)}
              </TableCell>
              <TableCell className={`text-right ${pnlClass(netSum)}`}>
                {fmtMoney(netSum)}
              </TableCell>
              <TableCell colSpan={2} />
            </TableRow>
          </TableFooter>
        )}
      </Table>
    </div>
  );
};

export default TradeLogTable;
