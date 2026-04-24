'use client';

import * as React from "react";
import { API_PREFIX } from '@/lib/api_prefix';

type InputTickersResponse = Record<string, string>;

const TickBoxAllExpandableAutoRefresh: React.FC = () => {
  const [files, setFiles] = React.useState<Record<string, string>>({});
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({});
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState<boolean>(true);
  const [savingFile, setSavingFile] = React.useState<string | null>(null);

  const fetchContent = React.useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const res = await fetch(`${API_PREFIX}/tickers`);
      if (!res.ok) throw new Error("Failed to fetch tickers");

      const data: InputTickersResponse = await res.json();
      setFiles(data);

      // Initialize expanded state
      setExpanded(prev => {
        const updated: Record<string, boolean> = {};
        Object.keys(data).forEach(f => {
          updated[f] = prev[f] ?? false;
        });
        return updated;
      });

    } catch (err: unknown) {
      if (err instanceof Error) setError(err.message);
      else setError(String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const handleChange = (filename: string, value: string) => {
    setFiles(prev => ({ ...prev, [filename]: value }));
  };

  const handleSave = async (filename: string) => {
    setSavingFile(filename);
    setError(null);

    try {
      const res = await fetch(`${API_PREFIX}/tickers`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename,
          content: files[filename],
        }),
      });

      if (!res.ok) throw new Error("Failed to save file");

      await fetchContent();

    } catch (err: unknown) {
      if (err instanceof Error) setError(err.message);
      else setError(String(err));
    } finally {
      console.log("Saved symbol to txt");
      setSavingFile(null);
    }
  };

  const toggleExpand = (filename: string) => {
    setExpanded(prev => ({
      ...prev,
      [filename]: !prev[filename],
    }));
  };

  React.useEffect(() => {
    fetchContent();
  }, [fetchContent]);

  return (
    <div className="space-y-3">
      {error && <p className="text-red-500">{error}</p>}
      {loading && <p>Loading...</p>}

      {Object.entries(files).map(([filename, content]) => (
        <div
          key={filename}
          className={`border rounded-md bg-white shadow-sm transition-all ${
            expanded[filename] ? "w-full max-w-md" : "w-1/2 mx-auto"
          }`}
        >
          {/* Header (click to expand) */}
          <div
            className="flex items-center justify-between px-3 py-2 cursor-pointer"
            onClick={() => toggleExpand(filename)}
          >
            <h3 className="font-semibold text-sm truncate">{filename}</h3>
            <span className="text-sm text-gray-500">
              {expanded[filename] ? "▼" : "▶"}
            </span>
          </div>

          {/* Expandable content */}
          {expanded[filename] && (
            <div className="p-4 border-t">
              <textarea
                value={content}
                onChange={(e) => handleChange(filename, e.target.value)}
                className="w-full h-32 p-2 border rounded text-sm font-mono"
              />
              <button
                onClick={() => handleSave(filename)}
                className="mt-2 px-3 py-1 bg-green-500 text-white rounded hover:bg-green-600 text-sm disabled:opacity-50"
                disabled={savingFile === filename}
              >
                {savingFile === filename ? "Saving..." : "Save"}
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

export default TickBoxAllExpandableAutoRefresh;
