"use client";

import React, { useEffect, useState } from "react";
import { API_PREFIX } from "@/lib/api_prefix";
import { paths } from "@/generated/api";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useParams, useRouter } from "next/navigation"; // App Router

type CandleRow =
  paths["/api/livestream/pricedata"]["get"]["responses"]["200"]["content"]["application/json"][number];

const PriceDataPage: React.FC = () => {
  const router = useRouter();
  const params = useParams();
  const symbol = params.symbol; // useParams instead of router.query

  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<CandleRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!symbol) return;

    const fetchData = async () => {
      setLoading(true);
      setError(null);

      try {
        const res = await fetch(`${API_PREFIX}/livestream/pricedata?symbol=${symbol}`);
        if (!res.ok) {
          const errJson = await res.json().catch(() => null);
          setError(errJson?.detail || `Failed to fetch data (status ${res.status})`);
          setData([]);
          return;
        }

        const json: CandleRow[] = await res.json();
        setData(json);
        if (json.length === 0) setError("No data found for this symbol.");
      } catch (err) {
        setError(`Network error: ${err}`);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [symbol]);

  return (
    <div className="p-4">
      <h2 className="text-xl font-bold mb-4">Price Data for {symbol}</h2>
      <button
        className="mb-4 px-3 py-1 border rounded bg-gray-200 hover:bg-gray-300"
        onClick={() => router.back()}
      >
        Close
      </button>

      {loading && <p>Loading...</p>}
      {error && <p className="text-red-500">{error}</p>}

      {!loading && !error && data.length > 0 && (
        <Table>
          <TableHeader className="bg-[#f9fafb]">
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Time</TableHead>
              <TableHead>Open</TableHead>
              <TableHead>High</TableHead>
              <TableHead>Low</TableHead>
              <TableHead>Close</TableHead>
              <TableHead>Volume</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.map((row, idx) => (
              <TableRow key={idx}>
                <TableCell>{row.Date}</TableCell>
                <TableCell>{row.Time}</TableCell>
                <TableCell>{row.Open}</TableCell>
                <TableCell>{row.High}</TableCell>
                <TableCell>{row.Low}</TableCell>
                <TableCell>{row.Close}</TableCell>
                <TableCell>{row.Volume}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
};

export default PriceDataPage;