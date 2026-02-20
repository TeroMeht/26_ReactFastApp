"use client";

import React from "react";
import { useSearchParams } from "next/navigation";

const PopupPage = () => {
  const params = useSearchParams();

  const symbol = params.get("symbol");
  const auxPrice = params.get("aux");
  const avgCost = params.get("avg");
  const position = params.get("pos");
  const openRisk = params.get("risk");
  const orderId = params.get("orderid");

  const handleClose = () => {
    window.close();
  };

  const handleStopBE = async () => {
    try {
      await fetch("http://localhost:8080/api/stop-be", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          avgCost: Number(avgCost),
          position: Number(position),
        }),
      });

      alert("Stop BE order sent successfully.");
    } catch (error) {
      alert("Failed to send Stop BE order.");
      console.error(error);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-40 flex items-center justify-center">
      <div className="bg-white rounded-lg p-6 w-full max-w-md shadow-lg text-center">
        <h2 className="text-lg font-semibold mb-4">Manage Position – {symbol}</h2>
        <div className="text-left space-y-2">
          <p><strong>Aux Price:</strong> {auxPrice}</p>
          <p><strong>Avg Cost:</strong> {avgCost}</p>
          <p><strong>Position:</strong> {position}</p>
          <p><strong>Open Risk:</strong> {openRisk}</p>
          <p><strong>OrderId:</strong> {orderId}</p>
        </div>
        <div className="mt-6 flex justify-center gap-4">
          <button
            onClick={handleStopBE}
            className="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700"
          >
            Stop BE
          </button>
          <button
            onClick={handleClose}
            className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default PopupPage;
