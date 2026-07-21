"use client";

import React, { useState, useEffect, useCallback } from "react";
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
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  // "Reconcile exits" button state. Message auto-fades so the section
  // header doesn't stay cluttered.
  const [reconciling, setReconciling] = useState(false);
  const [reconcileMsg, setReconcileMsg] = useState<string | null>(null);

  const fetchPortfolio = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_PREFIX}/portfolio/open-risk-table`);
      const json = await res.json();
      console.log(json);
      setPositions(json as OpenPosition[]);
    } catch (err) {
      console.error("Fetch error:", err);
      setPositions([]);
    } finally {
      setLoading(false);
    }
  }, []);

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
    } catch (err) {
      setReconcileMsg(
        `Failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setReconciling(false);
      setTimeout(() => setReconcileMsg(null), 4000);
    }
  }, []);

  useEffect(() => {
    fetchPortfolio();
  }, [fetchPortfolio]);

  const handleManage = (position: OpenPosition) => {
    // Serialize the object as base64
    const encoded = encodeURIComponent(btoa(JSON.stringify(position)));

    // Navigate to dynamic page with encoded object in query
    router.push(`/trade-manager/${position.symbol}/manage?data=${encoded}`);
  };


  
  return (
    <div className="py-4">
      <h2 className="text-xl font-bold mb-4">Portfolio</h2>

          <div className="flex items-center gap-2">
            {/*  Refresh Button */}
            <Button
              variant="outline"
              onClick={fetchPortfolio}
              disabled={loading}
            >
              {loading ? "Refreshing..." : "Refresh"}
            </Button>

            {/*  Clear-exits Button — drops armed exit_requests for symbols
                 the portfolio no longer holds. Kept next to Refresh so the
                 user can sync DB and IB from one place. Amber styling so
                 it visually reads as a maintenance action, distinct from
                 the blue Refresh. Border matches text color. */}
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