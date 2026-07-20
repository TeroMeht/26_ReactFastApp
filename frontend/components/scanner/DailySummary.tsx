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
//
// Catalyst Value Equation (CVE) fields mirror docs/CATALYST_EVALUATION.md.
// The constrained vocabularies (Magnitude/Speed/Grade/CatalystType) are kept
// as plain strings here — the backend already normalises the LLM output, and
// using string literal unions would make stray casing crash the table render.
type DailySummaryRow = {
  run_date: string;             // ISO date "YYYY-MM-DD" — present on every row
  side: "up" | "down" | string;
  rank: number;
  symbol: string;
  change: number | null;
  rvol: number | null;
  catalyst_type: string;        // confirmed | coverage | narrative | none
  magnitude: string;            // Absolute | Yes | Maybe | No
  speed: string;                // Absolute | Yes | Maybe | No
  grade: string;                // A+ | A | B | C | D
  sizing_pct: number;           // 0..80, derived risk cap from grade
  reason: string;
  notes: string;
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

// CVE colour helpers — keep grade visible at a glance. The palette deliberately
// pushes D toward a soft grey rather than red: D means "don't trade", not
// "this is a bad stock", so red would mis-signal direction.
const gradeStyle = (g: string): string => {
  switch (g) {
    case "A+": return "bg-emerald-600 text-white";
    case "A":  return "bg-emerald-500 text-white";
    case "B":  return "bg-amber-400 text-amber-950";
    case "C":  return "bg-orange-300 text-orange-950";
    case "D":
    default:   return "bg-gray-200 text-gray-600";
  }
};

// Magnitude / Speed cells share a 4-step scale. A muted background keeps the
// row scannable while preserving ordinality.
const scoreStyle = (s: string): string => {
  switch (s) {
    case "Absolute": return "bg-emerald-100 text-emerald-900 font-semibold";
    case "Yes":      return "bg-emerald-50 text-emerald-800";
    case "Maybe":    return "bg-amber-50 text-amber-800";
    case "No":
    default:         return "bg-gray-50 text-gray-500";
  }
};

// Grade ordering used by the "by grade" sort. A+ first, then A, etc., with
// unrecognised letters dropping to the bottom so they're easy to spot.
const _gradeRank = (g: string): number => {
  const o: Record<string, number> = { "A+": 5, A: 4, B: 3, C: 2, D: 1 };
  return o[g] ?? 0;
};

type SortMode = "grade" | "side";

const DailySummary: React.FC = () => {
  const [data, setData] = React.useState<DailySummaryResponse | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [running, setRunning] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [sortMode, setSortMode] = React.useState<SortMode>("grade");
  // Hide D-grade rows by default — the rubric says D = don't trade, so they
  // just add noise to pre-market prep. Toggle exposes them when the trader
  // wants to double-check that nothing tradeable was misgraded as D.
  const [hideD, setHideD] = React.useState<boolean>(true);

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
  //  - "grade": one combined list sorted by CVE grade desc (A+ first), so the
  //            tradeable names float to the top and the Ds collapse at the
  //            bottom.
  //  - "side":  up movers first then down movers, preserving original rank.
  const sortedRows: DailySummaryRow[] = React.useMemo(() => {
    const filtered = hideD ? allRows.filter((r) => r.grade !== "D") : allRows;
    if (sortMode === "grade") {
      return [...filtered].sort((a, b) => _gradeRank(b.grade) - _gradeRank(a.grade));
    }
    return [...filtered].sort((a, b) => {
      if (a.side !== b.side) return a.side === "up" ? -1 : 1;
      return a.rank - b.rank;
    });
  }, [allRows, sortMode, hideD]);

  const hiddenCount = hideD ? allRows.filter((r) => r.grade === "D").length : 0;

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
                  onClick={() => setSortMode("grade")}
                  className={
                    "px-2 py-0.5 rounded " +
                    (sortMode === "grade"
                      ? "bg-blue-500 text-white"
                      : "bg-gray-100 text-gray-600 hover:bg-gray-200")
                  }
                >
                  Grade
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
                <span className="text-gray-300 mx-1">|</span>
                <button
                  onClick={() => setHideD((v) => !v)}
                  className={
                    "px-2 py-0.5 rounded " +
                    (hideD
                      ? "bg-gray-100 text-gray-600 hover:bg-gray-200"
                      : "bg-blue-500 text-white")
                  }
                  title="Toggle D-grade rows (rubric says D = don't trade)"
                >
                  {hideD ? `Show D (${hiddenCount})` : "Hide D"}
                </button>
              </div>
            </div>
            <Table className="table-auto text-sm">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-12">Grade</TableHead>
                  <TableHead className="w-12">Size</TableHead>
                  <TableHead className="w-20">Symbol</TableHead>
                  <TableHead className="w-20">Change</TableHead>
                  <TableHead className="w-16">RVol</TableHead>
                  <TableHead className="w-20">Type</TableHead>
                  <TableHead className="w-20">Magnitude</TableHead>
                  <TableHead className="w-20">Speed</TableHead>
                  <TableHead className="font-bold text-black">Catalyst</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedRows.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={9} className="text-center text-sm">
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
                      title="CVE grade: Magnitude × Speed → A+ / A / B / C / D"
                    >
                      <span
                        className={
                          "inline-block px-1.5 py-0.5 rounded text-xs font-bold " +
                          gradeStyle(r.grade)
                        }
                      >
                        {r.grade || "D"}
                      </span>
                    </TableCell>
                    <TableCell
                      className="text-gray-700"
                      title="Suggested daily-risk cap derived from the grade (see docs/CATALYST_EVALUATION.md §4)"
                    >
                      {r.sizing_pct}%
                    </TableCell>
                    <TableCell className="font-medium">{r.symbol}</TableCell>
                    <TableCell className={changeColor(r.change)}>
                      {fmtPct(r.change)}
                    </TableCell>
                    <TableCell className="text-gray-700">{fmtRvol(r.rvol)}</TableCell>
                    <TableCell className="text-gray-700 capitalize">
                      {r.catalyst_type || "none"}
                    </TableCell>
                    <TableCell>
                      <span className={"inline-block px-1.5 py-0.5 rounded text-xs " + scoreStyle(r.magnitude)}>
                        {r.magnitude || "No"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className={"inline-block px-1.5 py-0.5 rounded text-xs " + scoreStyle(r.speed)}>
                        {r.speed || "No"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="text-sm text-black">{r.reason || "—"}</div>
                      {r.notes && (
                        <div
                          className="text-xs text-black mt-0.5"
                          title="LLM caveats: float, peer flow, already in price, etc."
                        >
                          {r.notes}
                        </div>
                      )}
                      {r.headline && (
                        <a
                          href={r.news_url || undefined}
                          target="_blank"
                          rel="noreferrer noopener"
                          className="text-xs text-blue-600 underline hover:text-blue-800 line-clamp-1 block mt-0.5"
                          title="Open source headline in a new tab"
                        >
                          {r.headline} ↗
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
