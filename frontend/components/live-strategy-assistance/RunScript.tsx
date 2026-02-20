'use client';

import { useState } from 'react';
import { API_PREFIX } from '@/lib/api_prefix';

type RunScriptProps = {
  onSymbolsChange?: (symbols: string[]) => void; // optional now
};

export default function RunScript({ onSymbolsChange }: RunScriptProps) {
  const [output, setOutput] = useState('');
  const [loading, setLoading] = useState(false);

  const handleRun = async () => {
    setLoading(true);
    setOutput('');

    try {
      const res = await fetch(`${API_PREFIX}/run-script`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });

      const data = await res.json();
      setOutput(data.output || 'Streamer started successfully.');

      // Optionally notify parent (if needed)
      if (onSymbolsChange) {
        onSymbolsChange([]); 
      }
    } catch (err) {
      console.error(err);
      setOutput('Connection error.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="w-80 p-3 bg-white rounded-lg shadow-sm border border-gray-200 text-sm space-y-2">
      <div className="font-medium text-gray-700">Start Streamer</div>

      <button
        onClick={handleRun}
        disabled={loading}
        className="w-full bg-blue-600 text-white py-1 rounded hover:bg-blue-700 disabled:opacity-60"
      >
        {loading ? 'Starting...' : 'Start'}
      </button>

      {output && (
        <div
          className={`p-2 rounded text-xs whitespace-pre-wrap max-h-32 overflow-auto ${
            output.toLowerCase().includes('error')
              ? 'bg-red-100 text-red-700'
              : 'bg-green-100 text-green-700'
          }`}
        >
          {output}
        </div>
      )}
    </div>
  );
}
