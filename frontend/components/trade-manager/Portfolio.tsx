"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { API_PREFIX } from "@/lib/api_prefix";
import { paths } from "@/generated/api";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

import { Button } from "@/components/ui/button";

type OpenPosition =
  paths["/api/portfolio/open-risk-table"]["get"]["responses"]["200"]["content"]["application/json"][number];

type ReconcileResult = {
  deleted_count?: number;
};

const PortfolioTable = () => {
  const [positions, setPositions] = useState<OpenPosition[]>([]);
  const [connected, setConnected] = useState(false);
  const router = useRouter();
  // "Reconcile exits" button state. Message auto-fades so the section
  // header doesn't stay cluttered.
  const [reconciling, setReconciling] = useState(false);
  const [reconcileMsg, setReconcileMsg] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const handleReconcile = useCallback(async () => {
    setReconciling(true);
    setReconcileMsg(null);
    try {
      const res = await fetch(`${API_PREFIX}/exits/reconcile`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as ReconcileResult;
      const n = data.deleted_count ?? 0;
      setReconcileMsg(
        n === 0 ? "Nothing to clear" : `Cleared ${n} orphan exit${n === 1 ? "" : "s"}`,
      );
      // Server will notify() the openrisk hub which pushes a fresh snapshot.
    } catch (err) {
      setReconcileMsg(
        `Failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setReconciling(false);
      setTimeout(() => setReconcileMsg(null), 4000);
    }
  }, []);

  // ----------------------------------------------------------------------
  // SSE wiring — the backend pushes a fresh snapshot whenever a fill lands,
  // an order changes, NetLiq shifts, or an exit_request is armed/disarmed.
  // No polling, no Refresh button; reconnects with backoff on network drop.
  // ----------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      const es = new EventSource(
        `${API_PREFIX}/portfolio/open-risk-table/stream`,
      );
      esRef.current = es;

      es.onopen = () => setConnected(true);

      es.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data);
          if (payload.type === "snapshot") {
            setPositions((payload.rows ?? []) as OpenPosition[]);
          }
          // ping → ignore
        } catch (err) {
          console.error("Portfolio SSE parse error:", err);
        }
      };

      es.onerror = () => {
        setConnected(false);
        es.close();
        if (cancelled) return;
        retryTimer = setTimeout(connect, 2000);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      esRef.current?.close();
      esRef.current = null;
    };
  }, []);

  const handleManage = (position: OpenPosition) => {
    // Serialize the object as base64
    const encoded = encodeURIComponent(btoa(JSON.stringify(position)));

    // Navigate to dynamic page with encoded object in query
    router.push(`/trade-manager/${position.symbol}/manage?data=${encoded}`);
  };


  
  return (
    <div className="py-4">
      <h2 className="text-xl font-bold mb-4">
        Portfolio
        <span
          className={`ml-3 inline-block px-2 py-0.5 text-xs rounded-full border ${
            connected
              ? "bg-green-100 text-green-800 border-green-300"
              : "bg-red-100 text-red-800 border-red-300"
          }`}
        >
          {connected ? "live" : "disconnected"}
        </span>
      </h2>

          <div className="flex items-center gap-2">
            {/*  Clear-exits Button — drops armed exit_requests for symbols
                 the portfolio no longer holds. Amber styling reads as a
                 maintenance action. The table auto-refreshes over SSE, so
                 no manual Refresh button is needed. */}
            <Button
              onClick={handleReconcile}
              disabled={reconciling}
              title="Delete armed exit requests for symbols no longer held in the portfolio"
              className="border-2 border-amber-700 text-amber-700 bg-white hover:bg-amber-700 hover:text-white"
            >
              {reconciling ? "Clearing..." : "Clear exits"}
            </Button>

            {reconcileMsg && (
              <span className="text-xs text-gray-600">{reconcileMsg}</span>
            )}
          </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Exit Strategies</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Allocation</TableHead>
            <TableHead>Size</TableHead>
            <TableHead>Avg Cost</TableHead>
            <TableHead>Aux Price</TableHead>
            <TableHead>Position</TableHead>
            <TableHead>Open Risk</TableHead>
            <TableHead className="text-center">Action</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {positions.length === 0 ? (
            <TableRow>
              <TableCell colSpan={9} className="text-gray-500">
                No open positions.
              </TableCell>
            </TableRow>
          ) : (
            positions.map((pos, index) => (
              <TableRow key={`${pos.symbol}-${index}`}>
                <TableCell>
                  {pos.exit_strategies && pos.exit_strategies.length > 0
                    ? pos.exit_strategies.join(", ")
                    : "—"}
                </TableCell>
                <TableCell>{pos.symbol}</TableCell>
                <TableCell>{pos.allocation}</TableCell>
                <TableCell>{pos.size}</TableCell>
                <TableCell>{pos.avgcost}</TableCell>
                <TableCell>{pos.auxprice}</TableCell>
                <TableCell>{pos.position}</TableCell>
                <TableCell>{pos.openrisk}</TableCell>
                <TableCell className="text-center">
                  <Button
                  variant="outline"
                    onClick={() => handleManage(pos)}
                  >
                    Manage
                  </Button>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default PortfolioTable;