"use client";

import React, { useState, useEffect, useCallback} from "react";
import { API_PREFIX } from "@/lib/api_prefix";
import { paths } from "@/generated/api";


// Import your reusable UI components
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

import { Button } from "@/components/ui/button";


type PendingOrder =
  paths["/api/pending_orders/orders"]["get"]["responses"]["200"]["content"]["application/json"][number];

const PendingOrdersTable = () => {

  const [positions, setPositions] = useState<PendingOrder[]>([]);
  const [apiMessage, setApiMessage] = useState<string | null>(null);
  const [allowedOrders, setAllowedOrders] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null); // add at top of component
  const [loading, setLoading] = useState(false);

  const [contractTypes, setContractTypes] = useState<Record<string, "CFD" | "stock">>({});
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);

  const fetchPositions = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_PREFIX}/pending_orders/orders`);
      const json = await res.json();
      setPositions(json as PendingOrder[]);
    } catch (err) {
      console.error("Fetch error:", err);
      setPositions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchPositions();
  }, [fetchPositions]);
  
const handleSend = async (order: PendingOrder) => {
  try {
    const contractType = contractTypes[order.id] ?? "stock";
    const payload = {
      symbol: order.symbol,
      entry_price: order.latest_price,
      stop_price: order.stop_price,
      position_size: order.position_size,
      contract_type: contractType
    };

    const res = await fetch(`${API_PREFIX}/portfolio/entry-request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Send failed: ${text}`);
    }

    const data: {
      allowed: boolean;
      message: string;
      symbol: string;
      parentOrderId: number;
      stopOrderId: number;
    } = await res.json();

    setApiMessage(
      `Symbol: ${data.symbol}, Allowed: ${data.allowed}, Message: ${data.message}, ParentOrderId: ${data.parentOrderId}, StopOrderId: ${data.stopOrderId}`
    );

    // If allowed, mark this order as sent/disabled
    if (data.allowed) {
      setAllowedOrders((prev) => new Set(prev).add(order.id));
    }

  } catch (err: any) {
    console.error("Error sending order:", err);
    setApiMessage(`Error: ${err.message || err}`);
  }

  // Optional: clear message after 5 seconds
  setTimeout(() => setApiMessage(null), 5000);
};

const handleDelete = async (order: PendingOrder) => {
  try {
    let res: Response;

    if (order.source === "ALPACA") {
      // Manual orders → DELETE endpoint with order ID in URL
      res = await fetch(`${API_PREFIX}/pending_orders/manual/${order.id}`, {
        method: "DELETE",
      });
    } else if (order.source === "DB") {
      // Automatic orders → POST endpoint
      res = await fetch(`${API_PREFIX}/pending_orders/auto/${order.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: order.id }),
      });
    } else {
      console.warn("Unknown order source, cannot cancel", order);
      return;
    }

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Cancel failed: ${text}`);
    }
    // Success
    setPositions((prev) => prev.filter((o) => o.id !== order.id));
    setMessage(`Order ${order.id} canceled successfully.`);

  } catch (err: any) {
    console.error("Error canceling order:", err);
    setMessage(`Error canceling order: ${err.message || err}`);
  }

  // Clear the message after 3 seconds
  setTimeout(() => setMessage(null), 3000);
};

  return (
    
    <div className="p-4">

      <h2 className="text-xl font-bold mb-4">Pending Orders</h2>

          {/*  Refresh Button */}
          <Button
            variant="outline"
            onClick={fetchPositions}
            disabled={loading}
          >
            {loading ? "Refreshing..." : "Refresh"}
          </Button>
              {message && (
          <div className="mb-4 p-2 bg-blue-100 text-blue-800 rounded-md text-sm">
            {message}
          </div>
          
        )}
        {apiMessage && (
  <div className="mb-4 p-2 bg-blue-100 text-blue-800 rounded-md text-sm break-words">
    {apiMessage}
  </div>
)}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Id</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Contract</TableHead>
            <TableHead>Latest Price</TableHead>
            <TableHead>Stop Price</TableHead>
            <TableHead>Quantity</TableHead>
            <TableHead>Size</TableHead>
            <TableHead className="text-center">Actions</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {positions.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="text-gray-500">
                No orders found.
              </TableCell>
            </TableRow>
          ) : (
            positions.map((order) => (
              <TableRow key={order.id}>
                <TableCell>{order.id}</TableCell>
                <TableCell>{order.symbol}</TableCell>
                <TableCell>
                  <div className="relative">
                    <button
                      className="px-3 py-1 text-sm rounded-md border border-input bg-gray-200 hover:bg-gray-400 transition-colors"
                      onClick={() => setOpenDropdown(openDropdown === order.id ? null : order.id)}
                    >
                      {contractTypes[order.id] ?? "stock"}
                    </button>

                    {openDropdown === order.id && (
                      <div className="absolute z-50 mt-1 w-28 rounded-md border border-input bg-white shadow-md">
                        {(["stock", "CFD"] as const).map((option) => (
                          <button
                            key={option}
                            className={`w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors ${
                              (contractTypes[order.id] ?? "stock") === option
                                ? "bg-gray-200 text-primary font-medium"
                                : "text-foreground hover:bg-gray-200"
                            }`}
                            onClick={() => {
                              
                              setContractTypes((prev) => ({ ...prev, [order.id]: option }));
                              setOpenDropdown(null);
                            }}
                          >
                            {option}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </TableCell>



                <TableCell>{order.latest_price}</TableCell>
                <TableCell>{order.stop_price}</TableCell>
                <TableCell>{order.position_size}</TableCell>
                <TableCell>{order.size}</TableCell>
                <TableCell className="text-center">
                  <Button
                    variant="ghost"
                    onClick={() => handleDelete(order)}
                  >
                    Delete
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => handleSend(order)}
                    disabled={allowedOrders.has(order.id)}
                  >
                    Send
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

export default PendingOrdersTable;