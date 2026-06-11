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

// Hand-typed mirror of backend/schemas/api_schemas.py::DailySummaryResponse.
// The user can regenerate generated/api.ts to swap this for the auto type:
//
//   type DailySummaryResponse =
//     paths["/api/scanner/daily-summary"]["get"]["responses"]["200"]["content"]["application/json"];
//
// Kept manual for now so the page compiles before `npm run gen:types` is run.
type DailySummaryRow = {
  run_date: string;             // ISO date "YYYY-MM-DD" — present on every row
  side: "up" | "down" | string;
  rank: number;
  symbol: string;
  change: number | null;
  rvol: number | null;
  catalyst_strength: number | null;   // 1-10 blended LLM rating
  reason: string;
  headline: string;
  news_url: string;
};

type DailySummaryResponse = {
  run_date: string;             // ISO date "YYYY-MM-DD"
  created_at: string;           // ISO datetime
  rows: DailySummaryRow[];
};

const fmtPct = (v: number | null | undefined): string =>
  v === null || v === undefined ? "-" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

const changeColor = (v: number | null | undefined): string => {
  if (v === null || v === undefined) return "text-gray-500";
  if (v > 0) return "text-green-700";
  if (v < 0) return "text-red-700";
  return "text-gray-700";
};

const fmtRvol = (v: number | null | undefined): string =>
  v === null || v === undefined ? "-" : `${v.toFixed(2)}x`;

type SortMode = "catalyst" | "side";

const DailySummary: React.FC = () => {
  const [data, setData] = React.useState<DailySummaryResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [sortMode, setSortMode] = React.useState<SortMode>("catalyst");

  const loadLatest = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_PREFIX}/scanner/daily-summary`);
      if (res.status === 404) {
        // No snapshot stored yet — that's fine, the user just hasn't run it.
        setData(null);
        return;
      }
      if (!res.ok) throw new Error(`Failed to load summary (${res.status})`);
      setData(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const runNow = React.useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await fetch(`${API_PREFIX}/scanner/daily-summary`, {
        method: "POST",
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        throw new Error(`Run failed (${res.status})${detail ? ": " + detail : ""}`);
      }
      setData(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }, []);

  React.useEffect(() => {
    loadLatest();
  }, [loadLatest]);

  const allRows = data?.rows ?? [];

  // Two view modes:
  //  - "catalyst": one combined list sorted by strength desc, biggest catalysts first
  //  - "side":     up movers first then down movers, preserving the original rank order
  const sortedRows: DailySummaryRow[] = React.useMemo(() => {
    if (sortMode === "catalyst") {
      // Nulls go to the bottom regardless of direction.
      return [...allRows].sort((a, b) => {
        const av = a.catalyst_strength ?? -1;
        const bv = b.catalyst_strength ?? -1;
        return bv - av;
      });
    }
    return [...allRows].sort((a, b) => {
      if (a.side !== b.side) return a.side === "up" ? -1 : 1;
      return a.rank - b.rank;
    });
  }, [allRows, sortMode]);

  return (
    <section className="mt-4 mb-6">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-lg font-semibold">Daily Premarket Summary</h2>
        <div className="flex items-center gap-3">
          {data && (
            <span className="text-xs text-gray-500">
              {data.run_date}
              {data.created_at && (
                <>
                  {" · "}
                  {new Date(data.created_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </>
              )}
            </span>
          )}
          <button
            onClick={runNow}
            disabled={running}
            className="px-3 py-1.5 text-sm bg-blue-500 text-white rounded hover:bg-blue-600 disabled:bg-gray-300"
          >
            {running ? "Running…" : "Run now"}
          </button>
        </div>
      </div>

      {error && (
        <p className="text-red-500 text-xs mb-2">{error}</p>
      )}

      {loading && !data && (
        <p className="text-xs text-gray-500">Loading latest summary…</p>
      )}

      {!loading && !data && !error && (
        <p className="text-xs text-gray-500">
          No daily summary yet — click <span className="font-medium">Run now</span> to generate one.
        </p>
      )}

      {data && (
        <div className="flex flex-col gap-4">
          {/* Movers table */}
          <div className="border rounded-md p-2 bg-white shadow-sm">
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-sm font-semibold">Top premarket movers</h3>
              <div className="flex items-center gap-1 text-xs">
                <span className="text-gray-500 mr-1">Sort:</span>
                <button
                  onClick={() => setSortMode("catalyst")}
                  className={
                    "px-2 py-0.5 rounded " +
                    (sortMode === "catalyst"
                      ? "bg-blue-500 text-white"
                      : "bg-gray-100 text-gray-600 hover:bg-gray-200")
                  }
                >
                  Catalyst
                </button>
                <button
                  onClick={() => setSortMode("side")}
                  className={
                    "px-2 py-0.5 rounded " +
                    (sortMode === "side"
                      ? "bg-blue-500 text-white"
                      : "bg-gray-100 text-gray-600 hover:bg-gray-200")
                  }
                >
                  Up/Down
                </button>
              </div>
            </div>
            <Table className="table-auto text-xs">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14">Rank</TableHead>
                  <TableHead className="w-12">Side</TableHead>
                  <TableHead className="w-20">Symbol</TableHead>
                  <TableHead className="w-20">Change</TableHead>
                  <TableHead className="w-16">RVol</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedRows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="text-center text-xs">
                      No movers in this snapshot
                    </TableCell>
                  </TableRow>
                )}
                {sortedRows.map((r) => (
                  <TableRow
                    key={`${r.side}-${r.rank}-${r.symbol}`}
                    style={{
                      backgroundColor:
                        r.side === "up"
                          ? "rgba(34,197,94,0.06)"
                          : "rgba(239,68,68,0.06)",
                    }}
                  >
                    <TableCell
                      className="font-bold"
                      title="LLM-rated catalyst rank (1-10): blends news impact, gap size, and rvol"
                    >
                      {r.catalyst_strength ?? "—"}
                    </TableCell>
                    <TableCell className="uppercase text-[10px] tracking-wider text-gray-600">
                      {r.side}
                    </TableCell>
                    <TableCell className="font-medium">{r.symbol}</TableCell>
                    <TableCell className={changeColor(r.change)}>
                      {fmtPct(r.change)}
                    </TableCell>
                    <TableCell className="text-gray-700">{fmtRvol(r.rvol)}</TableCell>
                    <TableCell>
                      <div className="text-gray-800">{r.reason || "—"}</div>
                      {r.headline && (
                        <a
                          href={r.news_url || undefined}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-[10px] text-gray-400 hover:text-blue-600 line-clamp-1 block mt-0.5"
                        >
                          {r.headline}
                        </a>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </section>
  );
};

export default DailySummary;
