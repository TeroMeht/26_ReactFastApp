"use client";

import React, { useState } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type RiskLevel = {
  Allocation: number | null;
  Symbol: string;
  AuxPrice: number | null;
  AvgCost: number;
  OpenRisk: number | string | null;
  Position: number;
  Size: number;
};

interface RiskTableProps {
  riskLevels: RiskLevel[];
  onExitRequest?: (symbol: string, selected: boolean) => void;
}

// Row component
const RiskTableRow: React.FC<{
  riskLevel: RiskLevel;
  isSelected: boolean;
  onToggle: (symbol: string) => void;
}> = ({ riskLevel, isSelected, onToggle }) => {
  const { Symbol, Allocation, AuxPrice, AvgCost, Size, Position, OpenRisk } =
    riskLevel;

  const handleClick = () => {
    onToggle(Symbol);
  };

  return (
    <TableRow>
      {/* Exit Toggle Button */}
      <TableCell className="text-center">
        <button
          onClick={handleClick}
          style={{
            width: 24,
            height: 24,
            borderRadius: "50%",
            border: "1px solid gray",
            backgroundColor: isSelected ? "black" : "white",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
        </button>
      </TableCell>

      <TableCell>{Symbol}</TableCell>
      <TableCell>{Allocation}</TableCell>
      <TableCell>{AuxPrice}</TableCell>
      <TableCell>{AvgCost}</TableCell>
      <TableCell>{Size}</TableCell>
      <TableCell>{Position}</TableCell>
      <TableCell>{OpenRisk}</TableCell>
    </TableRow>
  );
};

// Main table component
const RiskTable: React.FC<RiskTableProps> = ({ riskLevels, onExitRequest }) => {
  // Track toggle state per symbol
  const [selectedSymbols, setSelectedSymbols] = useState<Record<string, boolean>>({});

  // Toggle handler
  const handleToggle = async (symbol: string) => {
    const currentlySelected = selectedSymbols[symbol] || false;
    const newSelection = !currentlySelected;

    setSelectedSymbols((prev) => ({ ...prev, [symbol]: newSelection }));

    console.log(
      newSelection
        ? `Exit requested for: ${symbol}`
        : `Exit request cleared for: ${symbol}`
    );

    // Send immediately to backend
    if (onExitRequest) {
      onExitRequest(symbol, newSelection);
    } else {
      // Default behavior: call backend directly
      try {
        const response = await fetch("http://127.0.0.1:8080/api/exit_requests", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ symbol, exitRequested: newSelection }),
        });
        const data = await response.json();
        console.log("Backend response:", data);
      } catch (err) {
        console.error("Failed to update exit request:", err);
      }
    }
  };

  return (
    <div>
      <div className="flex justify-center my-4">
        <div className="text-xl font-bold">Position Management</div>
      </div>

      <Table>
        <TableHeader className="bg-[#f9fafb]">
          <TableRow>
            <TableHead>Exit Request</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Allocation</TableHead>
            <TableHead>Aux Price</TableHead>
            <TableHead>Avg Cost</TableHead>
            <TableHead>Size</TableHead>
            <TableHead>Position</TableHead>
            <TableHead>Open Risk</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {riskLevels.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="text-center text-gray-500">
                No risk data available.
              </TableCell>
            </TableRow>
          ) : (
            riskLevels.map((riskLevel) => (
              <RiskTableRow
                key={riskLevel.Symbol}
                riskLevel={riskLevel}
                isSelected={!!selectedSymbols[riskLevel.Symbol]}
                onToggle={handleToggle}
              />
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default RiskTable;
