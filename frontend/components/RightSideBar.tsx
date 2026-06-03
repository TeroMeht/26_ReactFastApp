"use client";
import React, { useState } from "react";
import { paths } from "@/generated/api";
import { API_PREFIX } from "@/lib/api_prefix"; // import your API prefix
import { useRouter } from "next/navigation";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import WatchlistManager from "@/components/live-strategy-assistance/WatchlistManager";
import LastRowsTable from "@/components/live-strategy-assistance/RelatrTable";




type CandleRow =
  paths["/api/livestream/pricedata"]["get"]["responses"]["200"]["content"]["application/json"][number];

type AlarmData = {
  Symbol: string;
  Time: string;
  Alarm: string;
  Date: string;
};

interface RightSidebarProps {
  pageSpecific?: boolean;
  alarms?: AlarmData[];
}

// Streamer-status states drive the colored dot next to "Live Strategy
// Assistance". GET /api/streamer-status returns {status: "running" | "offline"
// | "error"}; any HTTP/network failure on the frontend side also collapses to
// "error" (red dot) so the user sees something amiss instead of a stale green.
type StreamerState = "running" | "offline" | "error";

const STATUS_COLOR: Record<StreamerState, string> = {
  running: "bg-green-500",
  offline: "bg-gray-400",
  error: "bg-red-500",
};

const STATUS_LABEL: Record<StreamerState, string> = {
  running: "Streamer running",
  offline: "Streamer offline",
  error: "Streamer status unavailable",
};

const RightSidebar: React.FC<RightSidebarProps> = ({ pageSpecific, alarms }) => {
  const [showTodayOnly, setShowTodayOnly] = useState(true);
  const [loadingSymbol, setLoadingSymbol] = useState<string | null>(null);
  const [streamerState, setStreamerState] = useState<StreamerState>("offline");
  // inside RightSidebar
  const router = useRouter();

  // Streamer status is now push-based. We seed once from /streamer-status
  // so the dot paints immediately, then open an SSE stream that emits a
  // message on every state transition (the backend's heartbeat watchdog
  // flips to "offline" when the streamer goes silent past the threshold).
  React.useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const res = await fetch(`${API_PREFIX}/streamer-status`);
        if (!res.ok) {
          if (!cancelled) setStreamerState("error");
          return;
        }
        const data: { status?: StreamerState } = await res.json();
        if (cancelled) return;
        if (
          data.status === "running" ||
          data.status === "offline" ||
          data.status === "error"
        ) {
          setStreamerState(data.status);
        } else {
          setStreamerState("error");
        }
      } catch {
        if (!cancelled) setStreamerState("error");
      }
    })();

    const es = new EventSource(`${API_PREFIX}/streamer-status/stream`);
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as { status?: StreamerState };
        if (
          data.status === "running" ||
          data.status === "offline" ||
          data.status === "error"
        ) {
          setStreamerState(data.status);
        }
      } catch {
        // Ignore malformed payloads
      }
    };
    es.onerror = () => {
      // EventSource auto-reconnects. While disconnected we don't know
      // the streamer's state — surface that with the red dot.
      if (!cancelled) setStreamerState("error");
    };

    return () => {
      cancelled = true;
      es.close();
    };
  }, []);

  const isToday = (dateStr: string) => {
    const today = new Date();
    const inputDate = new Date(dateStr);
    return (
      inputDate.getDate() === today.getDate() &&
      inputDate.getMonth() === today.getMonth() &&
      inputDate.getFullYear() === today.getFullYear()
    );
  };
  const fetchCandleData = async (symbol: string) => {
    try {
      setLoadingSymbol(symbol);

      const response = await fetch(`${API_PREFIX}/livestream/pricedata?symbol=${symbol}`);

      if (!response.ok) {
        // Try to parse backend error message
        let errorMessage = `Failed to fetch price data (status ${response.status})`;
        try {
          const errData = await response.json();
          if (errData?.detail) {
            errorMessage = errData.detail; // show the backend message
          }
        } catch {
          // fallback if response is not JSON
        }
        // Instead of throwing, show it to the user
        alert(errorMessage);
        return;
      }

      const data: CandleRow[] = await response.json();

      if (data.length === 0) {
        console.log(`No candle data for symbol ${symbol}`);
      } else {
        console.log("Candle data for", symbol, data);
      }
    } catch (err) {
      // Network or other errors
      console.error("Error fetching candle data:", err);
      alert(`Error fetching candle data: ${err}`);
    } finally {
      setLoadingSymbol(null);
    }
  };

  const sortedAlarms = alarms
    ? [...alarms]
        .filter((alarm) => !showTodayOnly || isToday(alarm.Date))
        .sort((a, b) => {
          const dateA = new Date(`${a.Date} ${a.Time}`);
          const dateB = new Date(`${b.Date} ${b.Time}`);
          return dateB.getTime() - dateA.getTime();
        })
    : [];

  return (
    <section className="right-sidebar">
      {pageSpecific && (
        <>
          {/* Alarms section */}
          <div className="sidebar-content px-4">
            <div className="flex justify-between items-center mb-4">
              <h3 className="font-semibold text-lg">All Alarms</h3>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={showTodayOnly}
                  onChange={() => setShowTodayOnly((prev) => !prev)}
                  className="accent-blue-500"
                />
                Show only today
              </label>
            </div>

            <div className="max-h-[300px] overflow-y-auto border rounded-md">
              <Table className="table-fixed w-full text-xs">
                <TableHeader className="bg-[#f9fafb] sticky top-0 z-10">
                  <TableRow>
                    <TableHead className="h-7 px-1.5 text-xs w-[26%]">Symbol</TableHead>
                    <TableHead className="h-7 px-1.5 text-xs w-[48%]">Alarm</TableHead>
                    <TableHead className="h-7 px-1.5 text-xs w-[26%]">Time</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedAlarms.length > 0 ? (
                    sortedAlarms.map((alarm, index) => {
                      const today = isToday(alarm.Date);
                      return (
                        <TableRow
                          key={index}
                          className={`hover:bg-gray-100 cursor-pointer ${today ? "bg-yellow-100" : ""}`}
                          onClick={() => router.push(`/pricedata/${alarm.Symbol}`)}
                        >
                          <TableCell className="px-1.5 py-1.5 text-xs break-words whitespace-normal">{alarm.Symbol}</TableCell>
                          <TableCell className="px-1.5 py-1.5 text-xs break-words whitespace-normal">{alarm.Alarm}</TableCell>
                          <TableCell className="px-1.5 py-1.5 text-xs break-words whitespace-normal">{alarm.Time}</TableCell>
                        </TableRow>
                      );
                    })
                  ) : (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-xs">
                        No alarms to display.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </div>
          </div>

          {/* Live Strategy Assistance section */}
          <div className="sidebar-content border-t-2 border-gray-200 mt-4 pt-4 px-4">
            <h3 className="font-semibold text-lg flex items-center gap-2">
              Live Strategy Assistance
              <span
                className={`inline-block w-3 h-3 rounded-full ${STATUS_COLOR[streamerState]}`}
                title={STATUS_LABEL[streamerState]}
                aria-label={STATUS_LABEL[streamerState]}
              />
            </h3>

            <div className="space-y-4 mt-2">
              {/* Watchlist manager: ticker + strategy picker + Start button,
                  backed by the `watchlist` / `watchlist_strategies` DB tables. */}
              <div className="w-full">
                <WatchlistManager />
              </div>

              {/* Live Relatr / Rvol table */}
              <div className="w-full overflow-x-auto">
                <LastRowsTable />
              </div>
            </div>
          </div>
        </>
      )}
    </section>
  );
};

export default RightSidebar;
