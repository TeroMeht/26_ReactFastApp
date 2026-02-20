"use client";
import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";

type Orders = {
  symbol: string;
  id:string;
  latest_price: number;
  stop_price: number;
  position_size: number;
  position_value: number;
};

type ApiResponse = {
  status: string;
  message: string;
  data: Orders[];
};

// --- KEEP THIS EXACTLY AS BEFORE ---
const fetchPositions = async (): Promise<ApiResponse> => {
  const res = await fetch("http://localhost:8080/api/open-orders");
  const json = await res.json();
  return json as ApiResponse;
};

const PositionTable = ({ onComplete }: { onComplete: () => void }) => {
  const [positions, setPositions] = useState<Orders[]>([]);
  const [message, setMessage] = useState("");
  const [sentOrders, setSentOrders] = useState<Set<number>>(new Set());
  const [, setOrderMessages] = useState<Record<number, string>>({});
  const [popupMessage, setPopupMessage] = useState<{
  text: string;
  colorClass: string;
} | null>(null);
  const hasFetched = useRef(false);

  const updateTable = useCallback(async () => {
    try {
      const response = await fetchPositions();

      if (response.status === "success") {
        const positions = Array.isArray(response.data) ? response.data : [];
        setPositions(positions);
        setMessage(positions.length === 0 ? `${response.status}: ${response.message}` : "");
        setSentOrders(new Set());
        setOrderMessages({});
      } else {
        console.error("API returned error status:", response);
        setPositions([]);
        setMessage(response.message || "API error: Unexpected response.");
      }
    } catch (err) {
      console.error("Fetch error:", err);
      setPositions([]);
      const errorMessage =
        err instanceof Error
          ? err.message
          : typeof err === "string"
          ? err
          : "Error loading data. Please try again.";
      setMessage(`Fetch error: ${errorMessage}`);
    } finally {
      onComplete();
    }
  }, [onComplete]);

  useEffect(() => {
    if (!hasFetched.current) {
      hasFetched.current = true;
      updateTable();
    }
  }, [updateTable]);

  const handleSendOrder = async (order: Orders, index: number) => {
    if (sentOrders.has(index)) return;

    const requestBody = {
      symbol: order.symbol,
      entry_price: order.latest_price,
      stop_price: order.stop_price,
      position_size: order.position_size,
    };

    try {
      console.log("Sending Order:", JSON.stringify(requestBody, null, 2));
      const response = await fetch("http://localhost:8080/api/place-order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });

      const data = await response.json();
      console.log("Order response:", data);
          // Determine color based on entry_allowed
      const colorClass = data.entry_allowed ? "bg-green-600" : "bg-red-600";

      // Show popup with color
      setPopupMessage({ text: data.message, colorClass });
      setTimeout(() => setPopupMessage(null), 10000);

      if (response.ok && data.entry_allowed) {
        setSentOrders((prev) => new Set(prev).add(index));
        setOrderMessages((prev) => ({
          ...prev,
          [index]: data.message,
        }));
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Unknown error";
      setPopupMessage({ text: errMsg, colorClass: "bg-red-600" });
      setTimeout(() => setPopupMessage(null),10000);
      setOrderMessages((prev) => ({
        ...prev,
        [index]: errMsg,
      }));
    }
  };
  const handleCancelOrder = async (order: Orders) => {
    try {
      const response = await fetch("http://localhost:8080/api/deactivate_order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: order.id }),
      });

      const data = await response.json();

      const colorClass =
        data.status === "success" ? "bg-green-600" : "bg-red-600";

      setPopupMessage({ text: data.message, colorClass });
      setTimeout(() => setPopupMessage(null), 10000);

      if (data.status === "success") {
        // Remove the cancelled order from table
        setPositions((prev) => prev.filter((p) => p.id !== order.id));
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Cancel failed";
      setPopupMessage({ text: errMsg, colorClass: "bg-red-600" });
      setTimeout(() => setPopupMessage(null), 10000);
    }
  };
  return (
    <div className="relative">
      {/* --- POPUP MESSAGE --- */}
      {popupMessage && (
        <div
          className={`fixed top-4 right-4 text-white px-4 py-2 rounded shadow-lg z-50 animate-fade-in ${popupMessage.colorClass}`}
        >
          {popupMessage.text}
        </div>
      )}

      {/* --- TABLE HEADER --- */}
      <div className="relative flex items-center justify-center my-4">
        <Button
          onClick={updateTable}
          className="absolute left-0 bg-blue-500 text-white hover:bg-blue-600 px-4 py-2 rounded"
        >
          Update Table
        </Button>
        <div className="text-xl font-bold">Pending Orders</div>
      </div>

      <Table>
        <TableHeader className="bg-[#f9fafb]">
          <TableRow>
            <TableHead>Id</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Latest Price</TableHead>
            <TableHead>Stop price</TableHead>
            <TableHead>Position size</TableHead>
            <TableHead>Size</TableHead>
            <TableHead>Action</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {positions.length === 0 ? (
            <TableRow>
              <TableCell colSpan={8} className="text-center text-lg text-gray-500">
                {message || "No orders to display."}
              </TableCell>
            </TableRow>
          ) : (
            positions.map((position, index) => {
              const stopValue = position.stop_price;
              const isSent = sentOrders.has(index);

              return (
                <TableRow key={index} className="cursor-pointer hover:bg-gray-100">
                  <TableCell>{position.id}</TableCell>
                  <TableCell>{position.symbol}</TableCell>
                  <TableCell>{position.latest_price}</TableCell>
                  <TableCell>{stopValue}</TableCell>
                  <TableCell>{position.position_size}</TableCell>
                  <TableCell>
                    {(position.latest_price * position.position_size).toFixed(2)}
                  </TableCell>
                  <TableCell className="flex flex-col gap-1">
                    <Button
                      variant="secondary"
                      size="sm"
                      className={
                        isSent
                          ? "bg-gray-400 text-white"
                          : "bg-blue-500 hover:bg-blue-600 text-white"
                      }
                      disabled={isSent}
                      onClick={() => handleSendOrder(position, index)}
                    >
                      {isSent ? "Sent" : "Send Order"}
                    </Button>
                    <Button
                  variant="destructive"
                  size="sm"
                  className="bg-gray-400 hover:bg-gray-500 text-white"
                  onClick={() => handleCancelOrder(position)}
                >
                  Cancel
                </Button>
                  </TableCell>
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default PositionTable;
