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

const PortfolioTable = () => {
  const [positions, setPositions] = useState<OpenPosition[]>([]);
  const router = useRouter();
  const [loading, setLoading] = useState(false);

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

  useEffect(() => {
    fetchPortfolio();
  }, [fetchPortfolio]);

  const handleManage = (position: OpenPosition) => {
    // Serialize the object as base64
    const encoded = encodeURIComponent(btoa(JSON.stringify(position)));

    // Navigate to dynamic page with encoded object in query
    router.push(`/risk-levels/${position.symbol}/manage?data=${encoded}`);
  };


  
  return (
    <div className="py-4">
      <h2 className="text-xl font-bold mb-4">Portfolio</h2>
          
          {/*  Refresh Button */}
          <Button
            variant="outline"
            onClick={fetchPortfolio}
            disabled={loading}
          >
            {loading ? "Refreshing..." : "Refresh"}
          </Button>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Exit Requested</TableHead>
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
              <TableCell colSpan={8} className="text-gray-500">
                No open positions.
              </TableCell>
            </TableRow>
          ) : (
            positions.map((pos, index) => (
              <TableRow key={`${pos.symbol}-${index}`}>
                <TableCell>
                  {pos.exit_request ? "Yes" : "No"}
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