'use client';

import * as React from "react";
import { API_PREFIX } from '@/lib/api_prefix';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  TableCaption,
} from "@/components/ui/table";

type LastRow = Record<string, string | number>;

export const LastRowsTable: React.FC = () => {
  const [data, setData] = React.useState<LastRow[]>([]);
  const [error, setError] = React.useState<string | null>(null);

  const fetchData = async () => {
    try {
      const res = await fetch(`${API_PREFIX}/livestream/latest`);
     
      if (!res.ok) throw new Error("Failed to fetch table data");
      const json = await res.json();
      console.log(json)
      // json is already an array of rows
      const rows: LastRow[] = (json as LastRow[]).filter(Boolean);

      // Sort by Rvol descending
      rows.sort((a, b) => (b.Rvol as number) - (a.Rvol as number));
      
      setData(rows);
            setError(null); // Clear any previous error
        } catch (err: unknown) {
            if (err instanceof Error) {
            setError(err.message);
            } else {
            setError(String(err));
            }

        }
        };


  React.useEffect(() => {
    // Initial fetch in the background
    fetchData();

    // Fetch every 10 seconds
    const intervalId = setInterval(fetchData, 10000);

    return () => clearInterval(intervalId);
  }, []);

  const displayedColumns = ["Symbol", "Time", "Relatr", "Rvol"];

  return (
    <>
      {error && <p className="text-red-500">Error: {error}</p>}
      <Table className="mt-4">
        <TableCaption>Last rows of all tables (sorted by Rvol ↓)</TableCaption>
        <TableHeader>
          <TableRow>
            {displayedColumns.map((col) => (
              <TableHead key={col}>{col}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((row, idx) => (
            <TableRow key={idx}>
              {displayedColumns.map((col) => (
                <TableCell key={col}>
                  {row[col] !== undefined ? row[col] : "-"}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
};

export default LastRowsTable;
