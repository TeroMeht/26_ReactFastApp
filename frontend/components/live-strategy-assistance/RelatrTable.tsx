'use client';

import * as React from "react";
import { API_PREFIX } from '@/lib/api_prefix';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  TableCaption,
} from "@/components/ui/table";

type LastRow = Record<string, string | number>;

// How often to re-fetch the full latest snapshot from the backend.
// SSE was flaky in practice, so we replaced push updates with a plain
// polling loop: cheap, predictable, and easy to reason about.
const POLL_INTERVAL_MS = 10_000;

export const LastRowsTable: React.FC = () => {
  // Symbol -> row map. Rebuilt on every poll from the /latest snapshot
  // (which returns the full current state), so per-row merging is no
  // longer needed.
  const [bySymbol, setBySymbol] = React.useState<Map<string, LastRow>>(
    () => new Map(),
  );
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;

    const fetchLatest = async () => {
      try {
        const res = await fetch(`${API_PREFIX}/livestream/latest`);
        if (!res.ok) throw new Error("Failed to fetch table data");
        const json = (await res.json()) as LastRow[];
        if (cancelled) return;
        const next = new Map<string, LastRow>();
        for (const row of json.filter(Boolean)) {
          const s = String(row.Symbol ?? "");
          if (s) next.set(s, row);
        }
        setBySymbol(next);
        setError(null);
      } catch (err: unknown) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    };

    // Immediate first fetch so the table paints on load, then poll.
    fetchLatest();
    const intervalId = setInterval(fetchLatest, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, []);

  const displayedColumns = ["Symbol", "Time", "Relatr", "Rvol"];

  const rows = React.useMemo(() => {
    const list = Array.from(bySymbol.values());
    list.sort((a, b) => Number(b.Rvol ?? 0) - Number(a.Rvol ?? 0));
    return list;
  }, [bySymbol]);

  return (
    <>
      {error && <p className="text-red-500">{error}</p>}
      <Table className="mt-4">
        <TableCaption>Last rows of all tables (sorted by Rvol ↓)</TableCaption>
        <TableHeader>
          <TableRow>
            {displayedColumns.map((col) => (
              <TableHead key={col}>{col}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, idx) => {
            const rowClass = Number(row.Rvol) > 1.5 ? "bg-blue-100" : "";
            return (
              <TableRow key={String(row.Symbol ?? idx)} className={rowClass}>
                {displayedColumns.map((col) => {
                  let cellClass = "";
                  if (col === "Relatr") {
                    const val = Number(row[col]);
                    if (val < -0.45) cellClass = "font-bold text-green-600";
                    if (val > 0.45) cellClass = "font-bold text-red-600";
                  }
                  if (col === "Rvol" && Number(row[col]) > 1.5) {
                    cellClass = "font-bold text-grey-900";
                  }
                  return (
                    <TableCell key={col} className={cellClass}>
                      {row[col] !== undefined ? row[col] : "-"}
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </>
  );
};

export default LastRowsTable;
