'use client';

import Sidebar from "@/components/Sidebar";
import RightSidebar from "@/components/RightSideBar";
import { useState, useEffect } from "react";

type AlarmData = {
  Symbol: string;
  Time: string;
  Alarm: string;
  Date: string;
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const [alarms, setAlarms] = useState<AlarmData[]>([]);


  return (
    <main className="flex h-screen w-full font-inter">
      {/* Sidebar with portfolio/control system data */}
      <Sidebar />

      {/* Main content area */}
      <div className="flex-grow p-1">{children}</div>

      {/* Right Sidebar with alarms */}
      <RightSidebar alarms={alarms} pageSpecific={true} />
    </main>
  );
}
