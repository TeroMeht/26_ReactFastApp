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

export const LastRowsTable: React.FC = () => {
  // We keep a Symbol -> row map so SSE events can update individual rows
  // in O(1). The rendered list is derived from it (sorted by Rvol desc).
  const [bySymbol, setBySymbol] = React.useState<Map<string, LastRow>>(
    () => new Map(),
  );
  const [error, setError] = React.useState<string | null>(null);

  const mergeRow = React.useCallback((row: LastRow) => {
    const symbol = String(row.Symbol ?? "");
    if (!symbol) return;
    setBySymbol((prev) => {
      const next = new Map(prev);
      next.set(symbol, row);
      return next;
    });
  }, []);

  React.useEffect(() => {
    let cancelled = false;

    // 1) Seed once with the current snapshot so the table paints on load.
    (async () => {
      try {
        const res = await fetch(`${API_PREFIX}/livestream/latest`);
        if (!res.ok) throw new Error("Failed to fetch table data");
        const json = (await res.json()) as LastRow[];
        if (cancelled) return;
        const seeded = new Map<string, LastRow>();
        for (const row of json.filter(Boolean)) {
          const s = String(row.Symbol ?? "");
          if (s) seeded.set(s, row);
        }
        setBySymbol(seeded);
        setError(null);
      } catch (err: unknown) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      }
    })();

    // 2) Subscribe to incremental updates pushed by the streamer via the
    //    backend's webhook -> SSE pipeline. Each event is a single row.
    const es = new EventSource(`${API_PREFIX}/livestream/stream`);
    es.onmessage = (e) => {
      try {
        const row = JSON.parse(e.data) as LastRow;
        mergeRow(row);
      } catch {
        // Ignore malformed payloads -- next event will refresh state.
      }
    };
    es.onerror = () => {
      // EventSource auto-reconnects; surface a soft warning so the user
      // sees that updates may be paused.
      if (!cancelled) setError("Live updates paused (reconnecting...)");
    };

    return () => {
      cancelled = true;
      es.close();
    };
  }, [mergeRow]);

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
