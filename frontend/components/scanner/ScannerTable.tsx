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

type NewsItem = {
  title: string;
  summary: string;
  url: string;
  source: string;
  published_at: string;
  thumbnail: string;
};

type NewsPanel = {
  symbol: string;
  data: NewsItem[] | null;
  loading: boolean;
  error: string | null;
};

type ContextMenu = {
  symbol: string;
  x: number;
  y: number;
};

type ScannerTableProps = {
  title?: string;
  scan: string;
  fetchTrigger?: boolean;
  onFetched?: () => void;
  sortOrder?: "asc" | "desc";
  onAddToWatchlist?: (symbol: string) => void;
};

const WATCHLIST_FILENAME = "watchlist.txt";

const ScannerTable: React.FC<ScannerTableProps> = ({
  title = "IB Scanner Results",
  scan,
  fetchTrigger = false,
  onFetched,
  sortOrder = "desc",
  onAddToWatchlist,
}) => {
  const [data, setData] = React.useState<ScannerResponse[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [newsPanel, setNewsPanel] = React.useState<NewsPanel | null>(null);
  const [selectedSymbol, setSelectedSymbol] = React.useState<string | null>(null);
  const [contextMenu, setContextMenu] = React.useState<ContextMenu | null>(null);
  const [watchlist, setWatchlist] = React.useState<Set<string>>(new Set());

  const contextMenuRef = React.useRef<HTMLDivElement>(null);

  const fetchData = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_PREFIX}/scanner?preset_name=${scan}`);
      if (!res.ok) throw new Error("Failed to fetch scanner data");
      const json: ScannerResponse[] = await res.json();
      const rows = json
        .filter((row) => row.rvol === null || row.rvol >= 1)
        .sort((a, b) => {
          const aVal = a.change ?? 0;
          const bVal = b.change ?? 0;
          return sortOrder === "desc" ? bVal - aVal : aVal - bVal;
        });
      setData(rows);
      onFetched?.();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [scan, onFetched, sortOrder]);


  React.useEffect(() => {
    if (fetchTrigger) fetchData();
  }, [fetchTrigger, fetchData]);

  // Close context menu on outside click or scroll
  React.useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target as Node)) {
        setContextMenu(null);
      }
    };
    const handleScroll = () => setContextMenu(null);
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("scroll", handleScroll, true);
    };
  }, []);

  const handleRowClick = async (symbol: string) => {
    if (selectedSymbol === symbol) {
      setSelectedSymbol(null);
      setNewsPanel(null);
      return;
    }
    setSelectedSymbol(symbol);
    setNewsPanel({ symbol, data: null, loading: true, error: null });
    try {
      const res = await fetch(`${API_PREFIX}/scanner/news/${symbol}`);
      if (!res.ok) throw new Error("Failed to fetch news");
      const json: NewsItem[] = await res.json();
      setNewsPanel({ symbol, data: json, loading: false, error: null });
    } catch {
      setNewsPanel({ symbol, data: null, loading: false, error: "Could not load news" });
    }
  };

  const handleRightClick = (e: React.MouseEvent, symbol: string) => {
    e.preventDefault();
    setContextMenu({ symbol, x: e.clientX, y: e.clientY });
  };

  const handleAddToWatchlist = async (symbol: string) => {
    setContextMenu(null);
    try {
      const res = await fetch(`${API_PREFIX}/add-tickers-watchlist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: WATCHLIST_FILENAME, content: symbol }),
      });
      if (!res.ok) throw new Error("Failed to save watchlist");
      setWatchlist((prev) => new Set(prev).add(symbol));
      onAddToWatchlist?.(symbol);
    } catch (err) {
      console.error("Watchlist save failed:", err);
    }
  };

  const displayedColumns: (keyof ScannerResponse)[] = ["symbol", "change", "rvol"];
  const maxAbsChange = Math.max(...data.map((d) => Math.abs(d.change ?? 0)), 0.001);

  const getRowColor = (value: number | null, isSelected: boolean) => {
    if (isSelected) return "rgb(219 234 254)";
    if (value === null || value === 0) return "transparent";
    const intensity = Math.min(Math.abs(value) / maxAbsChange, 1);
    const colorValue = Math.floor(100 + intensity * 155);
    return value > 0 ? `rgb(0, ${colorValue}, 0)` : `rgb(${colorValue}, 0, 0)`;
  };

  return (
    <>
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
                  <TableCell colSpan={displayedColumns.length} className="text-center text-xs">
                    No data
                  </TableCell>
                </TableRow>
              )}
              {data.map((row, idx) => (
                <TableRow
                  key={idx}
                  style={{ backgroundColor: getRowColor(row.change, selectedSymbol === row.symbol) }}
                  className="cursor-pointer hover:opacity-80 transition-opacity"
                  onClick={() => handleRowClick(row.symbol)}
                  onContextMenu={(e) => handleRightClick(e, row.symbol)}
                >
                  {displayedColumns.map((col) => {
                    const val = row[col];
                    if (col === "symbol") {
                      return (
                        <TableCell key={col} className="flex items-center gap-1">
                          {val ?? "-"}
                          {watchlist.has(row.symbol) && (
                            <span className="text-yellow-400 text-xs">★</span>
                          )}
                        </TableCell>
                      );
                    }
                    if (typeof val === "number")
                      return <TableCell key={col}>{val.toFixed(2)}</TableCell>;
                    return <TableCell key={col}>{val ?? "-"}</TableCell>;
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>

      {/* Context menu */}
      {contextMenu && (
        <div
          ref={contextMenuRef}
          style={{ position: "fixed", top: contextMenu.y, left: contextMenu.x, zIndex: 100 }}
          className="bg-white border border-gray-200 rounded-md shadow-lg py-1 min-w-36"
        >
          <div className="px-3 py-1 text-xs text-gray-400 border-b border-gray-100 mb-1">
            {contextMenu.symbol}
          </div>
          <button
            onClick={() => handleAddToWatchlist(contextMenu.symbol)}
            className="w-full text-left px-3 py-1.5 text-xs hover:bg-gray-50 transition-colors flex items-center gap-2"
          >
            {watchlist.has(contextMenu.symbol) ? (
              <><span className="text-yellow-400">★</span> In watchlist</>
            ) : (
              <><span className="text-gray-400">☆</span> Add to watchlist</>
            )}
          </button>
        </div>
      )}

      {/* News panel */}
      {newsPanel && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 border rounded-md p-3 bg-white shadow-xl w-96">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-semibold">{newsPanel.symbol}</span>
            <div className="flex items-center gap-2">
              {newsPanel.data && (
                <span className="text-xs text-gray-400">
                  {newsPanel.data.length} article{newsPanel.data.length !== 1 ? "s" : ""} · last 24h
                </span>
              )}
              <button
                onClick={() => { setNewsPanel(null); setSelectedSymbol(null); }}
                className="text-xs text-gray-400 hover:text-gray-600 leading-none"
              >
                ✕
              </button>
            </div>
          </div>
          {newsPanel.loading && (
            <div className="flex items-center gap-2 py-6 justify-center">
              <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
              <span className="text-xs text-gray-400">Fetching news...</span>
            </div>
          )}
          {newsPanel.error && (
            <p className="text-xs text-red-400 py-2">{newsPanel.error}</p>
          )}
          {newsPanel.data && !newsPanel.loading && (
            <div className="max-h-96 overflow-y-auto flex flex-col gap-2">
              {newsPanel.data.length === 0 ? (
                <p className="text-xs text-gray-400 py-2">No news in the last 24h</p>
              ) : (
                newsPanel.data.map((item, i) => (
                  <div
                    key={i}
                    onClick={() => window.open(item.url, "_blank")}
                    className="block hover:bg-gray-50 rounded p-1.5 transition-colors cursor-pointer"
                  >
                    <p className="text-xs font-medium text-gray-800 leading-snug">{item.title}</p>
                    {item.summary && (
                      <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">{item.summary}</p>
                    )}
                    <p className="text-xs text-gray-400 mt-1">
                      {item.source} ·{" "}
                      {new Date(item.published_at).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </p>
                    {i < newsPanel.data!.length - 1 && (
                      <div className="border-t border-gray-100 mt-2" />
                    )}
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      )}
    </>
  );
};

export default ScannerTable;