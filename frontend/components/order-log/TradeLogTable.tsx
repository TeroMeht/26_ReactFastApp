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

// One closed round-trip (BOT + matched SLD) for today, as returned by
// GET /api/portfolio/pnl (IbClient.get_trades_with_pnl).
type TradePnl = {
  symbol: string;
  entry_time: string; // ISO, Europe/Helsinki
  exit_time: string; // ISO, Europe/Helsinki
  entry_price: number;
  exit_price: number;
  quantity: number;
  gross_pnl: number;
  commission: number;
  net_pnl: number;
  is_loss: boolean;
};

const fmtTime = (iso: string): string => {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
};

const fmtMoney = (n: number): string => {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}`;
};

const pnlStyle = (n: number): string =>
  n > 0
    ? "text-green-700 font-medium"
    : n < 0
    ? "text-red-700 font-medium"
    : "text-gray-700";

type TradeLogTableProps = {
  refreshSignal?: number;
  onLoadingChange?: (loading: boolean) => void;
};

const TradeLogTable = ({
  refreshSignal = 0,
  onLoadingChange,
}: TradeLogTableProps) => {
  const [trades, setTrades] = useState<TradePnl[]>([]);

  const fetchTrades = useCallback(async () => {
    try {
      onLoadingChange?.(true);
      const res = await fetch(`${API_PREFIX}/portfolio/pnl`);
      if (!res.ok) {
        console.error("Trade log fetch failed:", res.statusText);
        setTrades([]);
        return;
      }
      const data = (await res.json()) as TradePnl[];
      setTrades(data);
    } catch (err) {
      console.error("Trade log fetch error:", err);
      setTrades([]);
    } finally {
      onLoadingChange?.(false);
    }
  }, [onLoadingChange]);

  // Fetch on mount and whenever the shared page-level Refresh is clicked.
  useEffect(() => {
    fetchTrades();
  }, [fetchTrades, refreshSignal]);

  // Newest closed trade first (backend sorts ascending by exit_time).
  const ordered = useMemo(
    () => [...trades].sort((a, b) => b.exit_time.localeCompare(a.exit_time)),
    [trades]
  );

  const totals = useMemo(() => {
    return ordered.reduce(
      (acc, t) => {
        acc.gross += t.gross_pnl;
        acc.commission += t.commission;
        acc.net += t.net_pnl;
        if (t.net_pnl > 0) acc.wins += 1;
        else if (t.net_pnl < 0) acc.losses += 1;
        return acc;
      },
      { gross: 0, commission: 0, net: 0, wins: 0, losses: 0 }
    );
  }, [ordered]);

  return (
    <div className="py-4">
      <div className="flex flex-wrap items-center gap-3 mb-4">
        {ordered.length > 0 && (
          <div className="flex items-center gap-4 text-sm ml-auto">
            <span className="text-gray-600">
              {ordered.length} closed · {totals.wins}W / {totals.losses}L
            </span>
            <span className={pnlStyle(totals.net)}>
              Net PnL: {fmtMoney(totals.net)}
            </span>
          </div>
        )}
      </div>

      <Table className="w-full table-auto">
        <TableHeader>
          <TableRow>
            <TableHead>Exit Time</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Qty</TableHead>
            <TableHead>Entry</TableHead>
            <TableHead>Exit</TableHead>
            <TableHead>Gross PnL</TableHead>
            <TableHead>Commission</TableHead>
            <TableHead>Net PnL</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {ordered.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="text-gray-500">
                No closed trades today yet.
              </TableCell>
            </TableRow>
          ) : (
            ordered.map((t, idx) => (
              <TableRow key={`${t.symbol}-${t.exit_time}-${idx}`}>
                <TableCell className="font-mono text-xs whitespace-nowrap">
                  {fmtTime(t.exit_time)}
                </TableCell>
                <TableCell className="font-medium">{t.symbol}</TableCell>
                <TableCell>{t.quantity}</TableCell>
                <TableCell className="font-mono text-xs">
                  {t.entry_price}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {t.exit_price}
                </TableCell>
                <TableCell className={`font-mono ${pnlStyle(t.gross_pnl)}`}>
                  {fmtMoney(t.gross_pnl)}
                </TableCell>
                <TableCell className="font-mono text-xs text-gray-600">
                  -{t.commission.toFixed(2)}
                </TableCell>
                <TableCell className={`font-mono ${pnlStyle(t.net_pnl)}`}>
                  {fmtMoney(t.net_pnl)}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default TradeLogTable;
