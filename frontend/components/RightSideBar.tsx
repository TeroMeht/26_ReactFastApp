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

const RightSidebar: React.FC<RightSidebarProps> = ({ pageSpecific, alarms }) => {
  const [showTodayOnly, setShowTodayOnly] = useState(true);
  const [loadingSymbol, setLoadingSymbol] = useState<string | null>(null);
  // inside RightSidebar
  const router = useRouter();

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
        <div className="sidebar-content">
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

          <Table>
            <TableHeader className="bg-[#f9fafb]">
              <TableRow>
                <TableHead>Symbol</TableHead>
                <TableHead>Alarm</TableHead>
                <TableHead>Date</TableHead>
                <TableHead>Time</TableHead>
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
                    <TableCell>{alarm.Symbol}</TableCell>
                    <TableCell>{alarm.Alarm}</TableCell>
                    <TableCell>{today ? "Today" : alarm.Date}</TableCell>
                    <TableCell>{alarm.Time}</TableCell>
                  </TableRow>
                  );
                })
              ) : (
                <TableRow>
                  <TableCell colSpan={4} className="text-center text-sm">
                    No alarms to display.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
};

export default RightSidebar;