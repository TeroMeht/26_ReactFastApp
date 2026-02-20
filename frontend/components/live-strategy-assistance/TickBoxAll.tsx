'use client';

import * as React from "react";
import { API_PREFIX } from '@/lib/api_prefix';

type InputTickersResponse = {
  files: Record<string, string>;
};

const TickBoxAllExpandableAutoRefresh: React.FC = () => {
  const [files, setFiles] = React.useState<Record<string, string>>({});
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({});
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState<boolean>(true);
  const [saving, setSaving] = React.useState<boolean>(false);

  const fetchContent = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_PREFIX}/tickers/`);
      if (!res.ok) throw new Error("Failed to fetch tickers");
      const data: InputTickersResponse = await res.json();
      console.log(data)
      setFiles(data.files || {});

      // Initialize expanded state if empty
      setExpanded(prev => {
        const newExpanded: Record<string, boolean> = {};
        Object.keys(data.files || {}).forEach(f => {
          newExpanded[f] = prev[f] ?? false;
        });
        return newExpanded;
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
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`http://127.0.0.1:8080/api/input_tickers?file=${filename}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: files[filename] }),
      });
      if (!res.ok) throw new Error("Failed to save file");
      // Optional: fetch fresh content after save
      fetchContent();
    } catch (err: unknown) {
      if (err instanceof Error) setError(err.message);
      else setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  const toggleExpand = async (filename: string) => {
    // If expanding, fetch fresh content first
    if (!expanded[filename]) {
      await fetchContent();
    }
    setExpanded(prev => ({ ...prev, [filename]: !prev[filename] }));
  };

  // ✅ Safe useEffect
  React.useEffect(() => {
    fetchContent();
  }, [fetchContent]); // include fetchContent here

  return (
    <div className="space-y-4">
      {error && <p className="text-red-500">{error}</p>}
      {loading && <p>Loading...</p>}

      {Object.entries(files).map(([filename, content]) => (
        <div
          key={filename}
          className="border rounded-md bg-white shadow-sm w-full max-w-md"
        >
          <div
            className="flex justify-between items-center p-4 cursor-pointer"
            onClick={() => toggleExpand(filename)}
          >
            <h3 className="font-semibold">{filename}</h3>
            <span className="text-sm text-gray-500">
              {expanded[filename] ? "▼" : "▶"}
            </span>
          </div>

          {expanded[filename] && (
            <div className="p-4 border-t">
              <textarea
                value={content}
                onChange={(e) => handleChange(filename, e.target.value)}
                className="w-full h-32 p-2 border rounded text-sm font-mono"
              />
              <button
                onClick={() => handleSave(filename)}
                className="mt-2 px-3 py-1 bg-green-500 text-white rounded hover:bg-green-600 text-sm"
                disabled={saving}
              >
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

export default TickBoxAllExpandableAutoRefresh;
