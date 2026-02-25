"use client";

import React from "react";
import { useSearchParams } from "next/navigation";
import { OpenPosition } from "@/lib/types";

const ManagePage = () => {
  const params = useSearchParams();
  const dataParam = params.get("data");

  let position: OpenPosition | null = null;

  if (dataParam) {
    try {
      position = JSON.parse(atob(decodeURIComponent(dataParam))) as OpenPosition;
    } catch (err) {
      console.error("Failed to parse position data:", err);
    }
  }

  const handleClose = () => window.history.back();

  if (!position) return <div>Loading position...</div>;

  return (
    <div className="p-6 max-w-md mx-auto bg-white shadow-lg rounded-md mt-10">
      <h2 className="text-lg font-semibold mb-4">Position Management View – {position.symbol}</h2>
      <div className="space-y-2 text-left">
        <p>
          <strong>Exit Requested:</strong>{" "}
          {position.exit_request ? "Yes" : "No"}
        </p>
        <p><strong>Aux Price:</strong> {position.auxprice}</p>
        <p><strong>Avg Cost:</strong> {position.avgcost}</p>
        <p><strong>Position:</strong> {position.position}</p>
        <p><strong>Open Risk:</strong> {position.openrisk}</p>
        <p><strong>Allocation:</strong> {position.allocation}</p>
        <p><strong>Size:</strong> {position.size}</p>
      </div>
      <div className="mt-6 flex justify-center gap-4">
        <button
          onClick={handleClose}
          className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
        >
          Close
        </button>
      </div>
    </div>
  );
};

export default ManagePage;