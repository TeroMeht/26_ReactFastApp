import React, { useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

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

  const isToday = (dateStr: string) => {
    const today = new Date();
    const inputDate = new Date(dateStr);
    return (
      inputDate.getDate() === today.getDate() &&
      inputDate.getMonth() === today.getMonth() &&
      inputDate.getFullYear() === today.getFullYear()
    );
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
                      className={`hover:bg-gray-100 ${today ? "bg-yellow-100" : ""}`}
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
