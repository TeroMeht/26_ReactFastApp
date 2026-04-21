'use client';

import { useMemo, useState } from 'react';
import { API_PREFIX } from '@/lib/api_prefix';
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';

export type SignalEvent = {
  symbol: string;
  price: number;
  last2_high: number;
  stop_level: number;
  position_size: number;
  contract_type?: string; // "stock" | "CFD"
  bar_time?: number;      // bar-open time (UTC sec) that fired the breakout
  ts: number;
};

type Props = {
  signal: SignalEvent | null;
  onDismiss: () => void;
};

/**
 * Auto-generated pending-order panel.  Renders the breakout signal in the
 * same shape as the manual PendingOrders table so the user just clicks Send
 * to submit it to /api/portfolio/entry-request (exactly the same endpoint
 * the manual flow uses).
 */
export default function SignalApproval({ signal, onDismiss }: Props) {
  const [busy, setBusy] = useState(false);
  const [contractType, setContractType] = useState<'stock' | 'CFD'>('stock');
  const [openDropdown, setOpenDropdown] = useState(false);
  const [sent, setSent] = useState(false);
  const [apiMessage, setApiMessage] = useState<string | null>(null);
  const [apiAllowed, setApiAllowed] = useState<boolean | null>(null);

  const rowId = useMemo(() => (signal ? `auto-${signal.symbol}-${signal.ts}` : ''), [signal]);

  const resetNotice = () => {
    setTimeout(() => {
      setApiMessage(null);
      setApiAllowed(null);
    }, 10000);
  };

  const handleSend = async () => {
    if (!signal) return;
    setBusy(true);
    setApiMessage(null);
    setApiAllowed(null);
    try {
      const payload = {
        symbol: signal.symbol,
        entry_price: signal.price,
        stop_price: signal.stop_level,
        position_size: signal.position_size,
        contract_type: contractType,
      };

      const res = await fetch(`${API_PREFIX}/portfolio/entry-request`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Send failed: ${text}`);
      }

      const data: {
        allowed: boolean;
        message: string;
        symbol: string;
        parentOrderId?: number;
        stopOrderId?: number;
      } = await res.json();

      setApiMessage(
        `Symbol: ${data.symbol}, Allowed: ${data.allowed}, Message: ${data.message}`,
      );
      setApiAllowed(data.allowed);
      if (data.allowed) {
        setSent(true);
      }
    } catch (err: unknown) {
      setApiMessage(`Error: ${err instanceof Error ? err.message : String(err)}`);
      setApiAllowed(false);
    } finally {
      setBusy(false);
      resetNotice();
    }
  };

  if (!signal) {
    return (
      <div className="p-4">
        <h2 className="text-xl font-bold mb-4">Auto Assist — Signal</h2>
        <div className="text-sm text-gray-500">Waiting for breakout signal…</div>
      </div>
    );
  }

  const size = Number((signal.position_size * signal.price).toFixed(2));

  return (
    <div className="p-4">
      <h2 className="text-xl font-bold mb-4">Auto Assist — Signal</h2>

      {apiMessage && (
        <div
          className={`mb-4 p-2 rounded-md text-sm break-words ${
            apiAllowed === false
              ? 'bg-red-100 text-red-800'
              : 'bg-blue-100 text-blue-800'
          }`}
        >
          {apiMessage}
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Id</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Contract</TableHead>
            <TableHead>Latest Price</TableHead>
            <TableHead>Stop Price</TableHead>
            <TableHead>Quantity</TableHead>
            <TableHead>Size</TableHead>
            <TableHead className="text-center">Actions</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          <TableRow>
            <TableCell className="font-mono text-xs">{rowId}</TableCell>
            <TableCell>{signal.symbol}</TableCell>
            <TableCell>
              <div className="relative">
                <button
                  className="px-3 py-1 text-sm rounded-md border border-input bg-gray-200 hover:bg-gray-400 transition-colors"
                  onClick={() => setOpenDropdown((v) => !v)}
                  disabled={sent || busy}
                >
                  {contractType}
                </button>

                {openDropdown && (
                  <div className="absolute z-50 mt-1 w-28 rounded-md border border-input bg-white shadow-md">
                    {(['stock', 'CFD'] as const).map((option) => (
                      <button
                        key={option}
                        className={`w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors ${
                          contractType === option
                            ? 'bg-gray-200 text-primary font-medium'
                            : 'text-foreground hover:bg-gray-200'
                        }`}
                        onClick={() => {
                          setContractType(option);
                          setOpenDropdown(false);
                        }}
                      >
                        {option}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </TableCell>
            <TableCell>{signal.price.toFixed(2)}</TableCell>
            <TableCell>{signal.stop_level.toFixed(2)}</TableCell>
            <TableCell>{signal.position_size}</TableCell>
            <TableCell>{size}</TableCell>
            <TableCell className="text-center">
              <Button variant="ghost" onClick={onDismiss} disabled={busy}>
                Dismiss
              </Button>
              <Button
                variant="outline"
                onClick={handleSend}
                disabled={busy || sent || signal.position_size <= 0}
              >
                {busy ? 'Sending…' : sent ? 'Sent' : 'Send'}
              </Button>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>
  );
}
