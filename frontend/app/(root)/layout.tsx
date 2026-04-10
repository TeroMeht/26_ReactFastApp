'use client';

import * as React from "react";
import Sidebar from "@/components/Sidebar";
import RightSidebar from "@/components/RightSideBar";
import { API_PREFIX } from '@/lib/api_prefix';
import { components } from "@/generated/api"; // generated type from OpenAPI

type AlarmResponse = components["schemas"]["AlarmResponse"];


export default function RootLayout({ children }: { children: React.ReactNode }) {
  const [alarms, setAlarms] = React.useState<AlarmResponse[]>([]);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    // 1️⃣ Fetch initial alarms
    const fetchAlarms = async () => {
      try {
        const res = await fetch(`${API_PREFIX}/alarms/alarms`);
        if (!res.ok) throw new Error("Failed to fetch alarms");
        const data: AlarmResponse[] = await res.json();
        setAlarms(data);
      } catch (err: unknown) {
        if (err instanceof Error) setError(err.message);
        else setError(String(err));
      }
    };

    fetchAlarms();

    const eventSource = new EventSource(`${API_PREFIX}/alarms/stream`);

    eventSource.onmessage = (event) => {
      try {
        const newAlarm: AlarmResponse = JSON.parse(event.data);
        console.log(newAlarm)
        // Always prepend new alarm
        setAlarms(prev => [newAlarm, ...prev]);

      } catch (err) {
        console.error("Failed to parse SSE alarm:", err);
      }
    };

    eventSource.onerror = () => {
      // Browser auto-reconnects — only log if permanently closed
      if (eventSource.readyState === EventSource.CLOSED) {
        setError("SSE stream closed unexpectedly.");
      }
    };

    return () => {
      eventSource.close();
    };
  }, []);

  return (
    <main className="flex h-screen w-full font-inter">
      {/* Sidebar with portfolio/control system data */}
      <Sidebar />

      {/* Main content area */}
      <div className="flex-grow p-1">{children}</div>

      {/* Right Sidebar with alarms */}
      <RightSidebar alarms={alarms} pageSpecific={true} />

      {/* Optional error display */}
      {error && (
        <div className="fixed bottom-4 right-4 p-2 bg-red-500 text-white rounded">
          {error}
        </div>
      )}
    </main>
  );
}