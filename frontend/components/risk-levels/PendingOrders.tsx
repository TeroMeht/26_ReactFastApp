"use client";

import React, { useState, useEffect } from "react";
import { API_PREFIX } from "@/lib/api_prefix";
import { paths } from "@/generated/api";

// Import your reusable table components
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

type PendingOrder =
  paths["/api/pending_orders/orders"]["get"]["responses"]["200"]["content"]["application/json"][number];

const PendingOrders = () => {
  const [positions, setPositions] = useState<PendingOrder[]>([]);

  useEffect(() => {
    const fetchPositions = async () => {
      try {
        const res = await fetch(`${API_PREFIX}/pending_orders/orders`);
        const json = await res.json();
        // API returns the array directly, no "status" or "data"
        setPositions(json as PendingOrder[]);
      } catch (err) {
        console.error("Fetch error:", err);
        setPositions([]);
      }
    };

    fetchPositions();
  }, []);

  return (
    <div className="p-4">
      <h2 className="text-xl font-bold mb-4">Pending Orders</h2>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Id</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Latest Price</TableHead>
            <TableHead>Stop Price</TableHead>
            <TableHead>Quantity</TableHead>
            <TableHead>Size</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {positions.length === 0 ? (
            <TableRow>
              <TableCell colSpan={5} className="text-center text-gray-500">
                No orders found.
              </TableCell>
            </TableRow>
          ) : (
            positions.map((order) => (
              <TableRow key={order.id}>
                <TableCell>{order.id}</TableCell>
                <TableCell>{order.symbol}</TableCell>
                <TableCell>{order.latest_price}</TableCell>
                <TableCell>{order.stop_price}</TableCell>
                <TableCell>{order.position_size}</TableCell>
                <TableCell>{order.size}</TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default PendingOrders;