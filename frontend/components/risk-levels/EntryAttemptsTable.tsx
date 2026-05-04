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
} from "@/components/ui/table";

// Local type — kept in sync with backend `EntryAttemptsRow` in
// schemas/api_schemas.py. Declared inline so this component works
// before the openapi-typescript client is regenerated.
type EntryAttemptsRow = {
  symbol: string;
  attempts: number;
  max_attempts: number;
  remaining: number;
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
  const [loading, setLoading] = useState(false);

  const fetchAttempts = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_PREFIX}/portfolio/entry-attempts`);
      if (!res.ok) throw new Error(`Status ${res.status}`);
      const json = await res.json();
      setRows(json as EntryAttemptsRow[]);
    } catch (err) {
      console.error("Fetch entry-attempts error:", err);
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAttempts();
  }, [fetchAttempts, refreshTrigger]);

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Entry Attempts</h2>

      {/*
        Viewport: header + 3 data rows (~13.5rem). Vertical-only scrolling.

        Two key tricks so the 4th row's scrollbar doesn't push "Remaining"
        out of view:
          1. scrollbar-gutter: stable always reserves space for the
             scrollbar so the available width is the same whether or not
             the bar is visible.
          2. The <table> uses table-fixed with explicit column widths,
             so columns can't reflow as more rows are added — they stay
             at the percentages we set.
        The inner div from shadcn's Table still needs overflow-x-hidden
        so it doesn't render its own horizontal bar.
      */}
      <div
        className="
          max-h-[13.5rem] overflow-y-auto overflow-x-hidden
          [scrollbar-gutter:stable]
          [&>div]:overflow-x-hidden [&>div]:overflow-y-visible
        "
      >
        <Table className="table-fixed w-full">
          <TableHeader className="sticky top-0 bg-background z-10">
            <TableRow>
              <TableHead className="w-[34%] px-2">Symbol</TableHead>
              <TableHead className="w-[22%] px-2 text-right">Attempts</TableHead>
              <TableHead className="w-[22%] px-2 text-right">Max</TableHead>
              <TableHead className="w-[22%] px-2 text-right">Remaining</TableHead>
            </TableRow>
          </TableHeader>

          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={4} className="text-gray-500 px-2">
                  {loading ? "Loading..." : "No entries today."}
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row) => {
                const atLimit = row.remaining === 0;
                const oneLeft = row.remaining === 1;
                return (
                  <TableRow key={row.symbol}>
                    <TableCell className="font-medium px-2 truncate">{row.symbol}</TableCell>
                    <TableCell className="px-2 text-right">{row.attempts}</TableCell>
                    <TableCell className="px-2 text-right">{row.max_attempts}</TableCell>
                    <TableCell
                      className={`px-2 text-right font-semibold ${
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
        </Table>
      </div>
    </div>
  );
};

export default EntryAttemptsTable;
