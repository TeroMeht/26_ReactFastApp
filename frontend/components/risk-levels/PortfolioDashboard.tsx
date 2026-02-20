"use client";

import React, { useState, useCallback } from "react";
import RiskTable from "./RiskTable";

// Strongly typed account data
interface AccountDataItem {
  AccountName: string;
  Currency: string;
  Key: string;
  Value: string;
}

// Strongly typed open risk levels
interface OpenRiskLevel {
  Allocation: number;
  Size: number;
  Symbol: string;
  AuxPrice: number;
  AvgCost: number;
  OpenRisk: number | string;
  Position: number;
}

// Define position type
interface Position {
  symbol: string;
  position: number;
  avgCost: number;
  currency: string;
}

// Define order type
interface Order {
  orderId: number;
  symbol: string;
  action: string;
  totalQuantity: number;
  auxPrice: number;
  status: string;
}

interface ApiResponse {
  accountdata: AccountDataItem[];
  risk_levels: OpenRiskLevel[];
  positions: Position[];
  orders: Order[];
}



function PortfolioDashboard() {
  const [, setAccountdata] = useState<AccountDataItem[]>([]);
  const [riskLevels, setRiskLevels] = useState<OpenRiskLevel[]>([]);
  const [, setPositions] = useState<Position[]>([]);
  const [, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  

  const fetchPortfolioData = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const res = await fetch("http://localhost:8080/api/ib_accountdata");
      if (!res.ok) throw new Error("Failed to fetch portfolio data");
      const json: ApiResponse = await res.json();

      setAccountdata(json.accountdata || []);
      setRiskLevels(json.risk_levels || []);
      setPositions(json.positions || []);
      setOrders(json.orders || []);
      } catch (err: unknown) {
        if (err instanceof Error) {
          setError(err.message);
        } else {
          setError("Unknown error fetching portfolio data");
        }
      } finally {
        setLoading(false);
      }
  }, []);
  return (
    <div>
      <button
        onClick={fetchPortfolioData}
        className="bg-blue-500 text-white px-4 py-2 rounded mb-4"
        disabled={loading}
      >
        {loading ? "Loading..." : "Fetch Portfolio Data"}
      </button>

      {error && <div className="text-red-500 mb-2">Error: {error}</div>}

      {/* Open Risk Table */}
      <RiskTable riskLevels={riskLevels} />

    </div>
  );
}

export default PortfolioDashboard;
