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
import { API_PREFIX } from "@/lib/api_prefix";
import { paths } from "@/generated/api";

type ScannerResponse =
  paths["/api/scanner"]["get"]["responses"]["200"]["content"]["application/json"][number];

type ScannerTableProps = {
  title?: string;
  scan: string;
  fetchTrigger?: boolean;
  onFetched?: () => void;
};

const ScannerTable: React.FC<ScannerTableProps> = ({
  title = "IB Scanner Results",
  scan,
  fetchTrigger = false,
  onFetched,
}) => {
  const [data, setData] = React.useState<ScannerResponse[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const fetchData = React.useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_PREFIX}/scanner?preset_name=${scan}`);
      if (!res.ok) throw new Error("Failed to fetch scanner data");

      // API returns ScannerResponse[] directly
      const json: ScannerResponse[] = await res.json();

      // Optionally filter rows based on rvol
      const rows = json.filter((row) => row.rvol === null || row.rvol >= 2);

      setData(rows);
      onFetched?.();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [scan, onFetched]);

  React.useEffect(() => {
    if (fetchTrigger) fetchData();
  }, [fetchTrigger, fetchData]);

  const displayedColumns: (keyof ScannerResponse)[] = [
    "symbol",
    "change",
    "rvol",
    "time",
  ];

  // --- gradient scaling for positive & negative values ---
  const maxAbsChange = Math.max(...data.map((d) => Math.abs(d.change ?? 0)), 0.001);

  const getRowColor = (value: number | null) => {
    if (value === null || value === 0) return "transparent";
    const intensity = Math.min(Math.abs(value) / maxAbsChange, 1); // 0–1
    const colorValue = Math.floor(100 + intensity * 155); // 100 → 255
    return value > 0 ? `rgb(0, ${colorValue}, 0)` : `rgb(${colorValue}, 0, 0)`;
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
                {displayedColumns.map((col) => {
                  const val = row[col];
                  <TableCell key={col}>{val ?? "-"}</TableCell>
                  if (typeof val === "number") return <TableCell key={col}>{val.toFixed(2)}</TableCell>;
                  return <TableCell key={col}>{val ?? "-"}</TableCell>;
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
};

export default ScannerTable;