'use client';

import * as React from "react";
import ScannerTable from "@/components/scanner/ScannerTable";

type TableConfig = {
  title: string;
  endpoint: string;
};

type SequentialScannerProps = {
  tables: TableConfig[];
};

export const SequentialScannerTables: React.FC<SequentialScannerProps> = ({ tables }) => {
  const [currentIndex, setCurrentIndex] = React.useState<number | null>(null);
  const [triggerFetch, setTriggerFetch] = React.useState(false);

  const startSequentialFetch = () => {
    setCurrentIndex(0);
    setTriggerFetch(true);
  };

  // Auto-start on mount
  React.useEffect(() => {
    startSequentialFetch();
  }, []);

  return (
    <div>
      <button
        onClick={startSequentialFetch}
        className="px-4 py-2 mb-4 bg-blue-500 text-white rounded hover:bg-blue-600"
        disabled={triggerFetch && currentIndex !== null && currentIndex < tables.length}
      >
        {triggerFetch ? "Fetching..." : "Refresh All Scans"}
      </button>

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
        {tables.map((table, idx) => (
            <ScannerTable
            key={table.title}
            title={table.title}
            endpoint={table.endpoint}
            fetchTrigger={triggerFetch && idx === currentIndex} // only current table fetches
            onFetched={() => {
                if (currentIndex !== null && currentIndex < tables.length - 1) {
                setCurrentIndex(currentIndex + 1);
                } else {
                setTriggerFetch(false);
                setCurrentIndex(null);
                }
            }}
            />
        ))}
      </div>
    </div>
  );
};
