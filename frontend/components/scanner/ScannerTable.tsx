'use client';

import * as React from "react";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

type ScannerRow = {
  symbol: string;
  rank: number | null;
  change: number | null;
  rvol?: number | null;
};

// Raw API row (more fields exist, but we only pick 3)
type ApiScannerRow = {
  rank: number | null;
  symbol: string | null;
  change: number | null;
  rvol?: number | null;
};

type ScannerTableProps = {
  title?: string;
  endpoint: string;
  fetchTrigger?: boolean;
  onFetched?: () => void;
};

const ScannerTable: React.FC<ScannerTableProps> = ({
  title = "IB Scanner Results",
  endpoint,
  fetchTrigger = false,
  onFetched,
}) => {
  const [data, setData] = React.useState<ScannerRow[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const fetchData = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const url = endpoint.startsWith("http")
        ? endpoint
        : `http://127.0.0.1:8080${endpoint}`;

      const res = await fetch(url);
      if (!res.ok) throw new Error("Failed to fetch scanner data");

      const json = await res.json();

      const results = (json.results as ApiScannerRow[] | undefined) ?? [];

      const rows: ScannerRow[] = results.map((row) => ({
        rank: row.rank ?? null,
        symbol: row.symbol ?? "-",
        change: row.change ?? null,
        rvol: row.rvol ?? null,
      }))
      .filter((row) => row.rvol === null || row.rvol >= 2); // keep null or >=1

      setData(rows);
      onFetched?.();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [endpoint, onFetched]);

  React.useEffect(() => {
    if (fetchTrigger) fetchData();
  }, [fetchTrigger, fetchData]);

  const displayedColumns = ["rank", "symbol", "change", "rvol"];

// --- gradient scaling for positive & negative values ---
const maxAbsChange = Math.max(
  ...data.map((d) => Math.abs(d.change ?? 0)),
  0.001
);

const getRowColor = (value: number | null) => {
  if (value === null || value === 0) return "transparent";

  const intensity = Math.min(Math.abs(value) / maxAbsChange, 1); // 0–1
  const colorValue = Math.floor(100 + intensity * 155); // 100 → 255

  if (value > 0) {
    // green gradient
    return `rgb(0, ${colorValue}, 0)`;
  } else {
    // red gradient
    return `rgb(${colorValue}, 0, 0)`;
  }
};

  return (
    <div
      className={`border rounded-md p-2 bg-white shadow-sm w-full max-w-xs transition-colors duration-300 ${
        loading ? "bg-blue-50 animate-pulse" : ""
      }`}
    >
      <h3 className="text-sm font-semibold mb-1">{title}</h3>
      {error && <p className="text-red-500 text-xs mb-1">{error}</p>}

      <div className="overflow-y-auto max-h-96">
        <Table className="table-auto text-xs">
          <TableHeader>
            <TableRow>
              {displayedColumns.map((col) => (
                <TableHead key={col}>{col.toUpperCase()}</TableHead>
              ))}
            </TableRow>
          </TableHeader>

          <TableBody>
            {data.length === 0 && !loading && (
              <TableRow>
                <TableCell
                  colSpan={displayedColumns.length}
                  className="text-center text-xs"
                >
                  No data
                </TableCell>
              </TableRow>
            )}

            {data.map((row, idx) => (
              <TableRow
                key={idx}
                style={{ backgroundColor: getRowColor(row.change) }}
              >
                {displayedColumns.map((col) => (
                  <TableCell key={col} className="text-xs">
                    {row[col as keyof ScannerRow] ?? "-"}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
};

export default ScannerTable;
