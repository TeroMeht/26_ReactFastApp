"use client";

import React, { useState, useEffect, useCallback } from "react";
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

// Local types — kept in sync with backend `EntryAttemptsRow` /
// `EntryAttemptsResponse` in schemas/api_schemas.py. Declared inline so
// this component works before the openapi-typescript client is regenerated.
type EntryAttemptsRow = {
  symbol: string;
  attempts: number;
  max_attempts: number;
  remaining: number;
};

type EntryAttemptsResponse = {
  rows: EntryAttemptsRow[];
  total_attempts: number;
  max_total: number;
  total_remaining: number;
};

type Props = {
  /**
   * Bumping this number triggers a refetch. Wire it to the parent's
   * Refresh action so a single click updates both the pending-orders
   * table and this stats table.
   */
  refreshTrigger?: number;
};

const EntryAttemptsTable: React.FC<Props> = ({ refreshTrigger = 0 }) => {
  const [rows, setRows] = useState<EntryAttemptsRow[]>([]);
  const [totalAttempts, setTotalAttempts] = useState(0);
  const [maxTotal, setMaxTotal] = useState(0);
  const [totalRemaining, setTotalRemaining] = useState(0);
  const [loading, setLoading] = useState(false);

  const fetchAttempts = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_PREFIX}/portfolio/entry-attempts`);
      if (!res.ok) throw new Error(`Status ${res.status}`);
      const json = (await res.json()) as EntryAttemptsResponse;
      setRows(json.rows ?? []);
      setTotalAttempts(json.total_attempts ?? 0);
      setMaxTotal(json.max_total ?? 0);
      setTotalRemaining(json.total_remaining ?? 0);
    } catch (err) {
      console.error("Fetch entry-attempts error:", err);
      setRows([]);
      setTotalAttempts(0);
      setMaxTotal(0);
      setTotalRemaining(0);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAttempts();
  }, [fetchAttempts, refreshTrigger]);

  return (
    <div>
      <h2 className="text-base font-bold mb-2">Entry Attempts</h2>

      {/*
        Compact viewport: ~7 data rows + header + footer at text-xs / h-6
        (~1.5rem per row). Vertical-only scrolling.

        Two key tricks so the scrollbar appearing doesn't push "Remaining"
        out of view:
          1. scrollbar-gutter: stable always reserves space for the
             scrollbar so the available width is the same whether or not
             the bar is visible.
          2. The <table> uses table-fixed with explicit column widths,
             so columns can't reflow as more rows are added.
        The inner div from shadcn's Table still needs overflow-x-hidden
        so it doesn't render its own horizontal bar.
      */}
      <div
        className="
          max-h-[14rem] overflow-y-auto overflow-x-hidden
          [scrollbar-gutter:stable]
          [&>div]:overflow-x-hidden [&>div]:overflow-y-visible
        "
      >
        <Table className="table-fixed w-full text-xs">
          <TableHeader className="sticky top-0 bg-background z-10">
            <TableRow className="h-7">
              <TableHead className="w-[34%] px-2 py-1 h-7 whitespace-nowrap">Symbol</TableHead>
              <TableHead className="w-[22%] px-2 py-1 h-7 text-right whitespace-nowrap">Attempts</TableHead>
              <TableHead className="w-[22%] px-2 py-1 h-7 text-right whitespace-nowrap">Max</TableHead>
              <TableHead className="w-[22%] px-2 py-1 h-7 text-right whitespace-nowrap">Remaining</TableHead>
            </TableRow>
          </TableHeader>

          <TableBody>
            {rows.length === 0 ? (
              <TableRow className="h-6">
                <TableCell colSpan={4} className="text-gray-500 px-2 py-1">
                  {loading ? "Loading..." : "No entries today."}
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row) => {
                const atLimit = row.remaining === 0;
                const oneLeft = row.remaining === 1;
                return (
                  <TableRow key={row.symbol} className="h-6">
                    <TableCell className="font-medium px-2 py-1 truncate">{row.symbol}</TableCell>
                    <TableCell className="px-2 py-1 text-right">{row.attempts}</TableCell>
                    <TableCell className="px-2 py-1 text-right">{row.max_attempts}</TableCell>
                    <TableCell
                      className={`px-2 py-1 text-right font-semibold ${
                        atLimit
                          ? "text-red-600"
                          : oneLeft
                          ? "text-amber-600"
                          : "text-green-700"
                      }`}
                    >
                      {row.remaining}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>

          {/*
            Daily total across all tickers. Mirrors the per-symbol coloring
            so the user sees at a glance how close they are to the
            MAX_TOTAL_ENTRIES_PER_DAY hard cap defined in backend config.
          */}
          <TableFooter className="sticky bottom-0 bg-background">
            <TableRow className="h-7">
              <TableCell className="font-semibold px-2 py-1">Total</TableCell>
              <TableCell className="px-2 py-1 text-right font-semibold">
                {totalAttempts}
              </TableCell>
              <TableCell className="px-2 py-1 text-right font-semibold">
                {maxTotal}
              </TableCell>
              <TableCell
                className={`px-2 py-1 text-right font-semibold ${
                  totalRemaining === 0
                    ? "text-red-600"
                    : totalRemaining === 1
                    ? "text-amber-600"
                    : "text-green-700"
                }`}
              >
                {totalRemaining}
              </TableCell>
            </TableRow>
          </TableFooter>
        </Table>
      </div>
    </div>
  );
};

export default EntryAttemptsTable;
