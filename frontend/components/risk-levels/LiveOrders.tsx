"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { API_PREFIX } from "@/lib/api_prefix";

import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";

// Mirrors backend schemas/api_schemas.py::LiveOrder. Kept local to avoid
// having to regenerate the OpenAPI types just to land this component.
type LiveOrder = {
  perm_id: number;
  order_id: number;
  symbol: string | null;
  sec_type: string | null;
  action: string | null;
  order_type: string | null;
  total_qty: number;
  lmt_price: number | null;
  aux_price: number | null;
  parent_id: number;
  status: string | null;
  filled: number;
  remaining: number;
  avg_fill_price: number;
  last_error: string | null;
  last_error_code: number | null;
  submitted_at: number;
};

type CancelResult = {
  status: string;
  order_id: number;
  symbol: string | null;
  filled: number;
  remaining: number;
  message?: string | null;
};

const TERMINAL = new Set(["Filled", "Cancelled", "ApiCancelled", "Inactive"]);
// On the Risk Levels page we only want ACTIVE orders to be visible.
// Every terminal state is hidden here and shows up in /order-log instead.
const HIDDEN_STATUSES = TERMINAL;

const statusStyle = (status: string | null): string => {
  switch (status) {
    case "Filled":
      return "bg-green-100 text-green-800 border-green-300";
    case "Cancelled":
    case "ApiCancelled":
    case "Inactive":
      return "bg-gray-200 text-gray-700 border-gray-300";
    case "Submitted":
      return "bg-yellow-100 text-yellow-800 border-yellow-300";
    case "PreSubmitted":
    case "PendingSubmit":
    case "ApiPending":
      return "bg-blue-100 text-blue-800 border-blue-300";
    case "PendingCancel":
      return "bg-orange-100 text-orange-800 border-orange-300";
    default:
      return "bg-gray-100 text-gray-700 border-gray-200";
  }
};

const rowKey = (o: LiveOrder): string =>
  o.perm_id ? `p:${o.perm_id}` : `o:${o.order_id}`;

const LiveOrders = () => {
  const [orders, setOrders] = useState<LiveOrder[]>([]);
  const [connected, setConnected] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  // ----------------------------------------------------------------------
  // SSE wiring — single long-lived connection. Reconnect with backoff if the
  // browser/network drops it.
  // ----------------------------------------------------------------------
  const upsertOrder = useCallback((incoming: LiveOrder) => {
    setOrders((prev) => {
      const key = rowKey(incoming);

      // Only ACTIVE orders are shown on this page. Terminal transitions
      // (Filled/Cancelled/ApiCancelled/Inactive) are logged to the console
      // and recorded server-side in the event log (see /order-log).
      if (incoming.status && HIDDEN_STATUSES.has(incoming.status)) {
        console.info(
          `[LiveOrders] terminal: permId=${incoming.perm_id} symbol=${incoming.symbol} ` +
            `action=${incoming.action} qty=${incoming.total_qty} ` +
            `filled=${incoming.filled} status=${incoming.status}`
        );
        return prev.filter((o) => rowKey(o) !== key);
      }

      // If the incoming row now has a permId and we have a stale orderId-keyed
      // row for the same orderId, drop the stale one.
      const cleaned =
        incoming.perm_id > 0
          ? prev.filter(
              (o) =>
                !(o.perm_id === 0 && o.order_id === incoming.order_id)
            )
          : prev;

      const replaceIdx = cleaned.findIndex((o) => rowKey(o) === key);
      if (replaceIdx === -1) {
        return [incoming, ...cleaned];
      }
      const next = [...cleaned];
      next[replaceIdx] = { ...next[replaceIdx], ...incoming };
      return next;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      const es = new EventSource(`${API_PREFIX}/portfolio/order-status/stream`);
      esRef.current = es;

      es.onopen = () => setConnected(true);

      es.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data);
          if (payload.type === "snapshot") {
            const all = payload.orders as LiveOrder[];
            const visible: LiveOrder[] = [];
            for (const o of all) {
              if (o.status && HIDDEN_STATUSES.has(o.status)) {
                console.info(
                  `[LiveOrders] terminal (snapshot): permId=${o.perm_id} ` +
                    `symbol=${o.symbol} status=${o.status}`
                );
              } else {
                visible.push(o);
              }
            }
            setOrders(visible);
          } else if (payload.type === "update") {
            upsertOrder(payload.order as LiveOrder);
          }
          // ping → ignore
        } catch (err) {
          console.error("SSE parse error:", err);
        }
      };

      es.onerror = () => {
        setConnected(false);
        es.close();
        if (cancelled) return;
        // Reconnect with a small delay.
        retryTimer = setTimeout(connect, 2000);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      esRef.current?.close();
      esRef.current = null;
    };
  }, [upsertOrder]);

  // ----------------------------------------------------------------------
  // Actions
  // ----------------------------------------------------------------------
  const flash = (text: string, ms = 5000) => {
    setMessage(text);
    setTimeout(() => setMessage(null), ms);
  };

  const handleCancel = async (order: LiveOrder) => {
    if (!order.perm_id) {
      flash(`Order ${order.order_id} not acknowledged by IB yet — try again in a moment.`);
      return;
    }
    setBusyId(order.perm_id);
    try {
      const res = await fetch(
        `${API_PREFIX}/portfolio/cancel-order/${order.perm_id}`,
        { method: "POST" }
      );
      const data = (await res.json()) as CancelResult;

      if (!res.ok) {
        flash(`Cancel failed: ${(data as any).detail || res.statusText}`);
        return;
      }

      if (data.status === "Filled") {
        flash(
          `Order ${order.perm_id} (${data.symbol}) already filled before cancel landed: ${data.filled} @ ${order.avg_fill_price || "—"}.`
        );
      } else if (data.status === "Cancelled" || data.status === "ApiCancelled") {
        flash(`Order ${order.perm_id} (${data.symbol}) cancelled — 0 fills.`);
      } else if (data.status === "timeout") {
        flash(
          `Cancel for ${order.perm_id} timed out. Last status will update via the stream.`
        );
      } else {
        flash(`Cancel result for ${order.perm_id}: ${data.status}`);
      }
    } catch (err: any) {
      flash(`Cancel error: ${err.message || err}`);
    } finally {
      setBusyId(null);
    }
  };

  const handleCancelAll = async () => {
    if (
      !window.confirm(
        "Cancel ALL unfilled orders? Orders already partially or fully filled will be left alone."
      )
    ) {
      return;
    }
    setBulkBusy(true);
    try {
      const res = await fetch(`${API_PREFIX}/portfolio/cancel-all-unfilled`, {
        method: "POST",
      });
      const data = (await res.json()) as CancelResult[];

      if (!res.ok) {
        flash(`Cancel-all failed: ${(data as any).detail || res.statusText}`);
        return;
      }

      const cancelled = data.filter(
        (r) => r.status === "Cancelled" || r.status === "ApiCancelled"
      ).length;
      flash(
        `Cancel-all complete: ${cancelled}/${data.length} orders cancelled.`
      );
    } catch (err: any) {
      flash(`Cancel-all error: ${err.message || err}`);
    } finally {
      setBulkBusy(false);
    }
  };

  const unfilledCount = orders.filter(
    (o) => o.status && !TERMINAL.has(o.status) && o.filled === 0
  ).length;

  return (
    <div className="py-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold">
          Active IB Orders
          <span
            className={`ml-3 inline-block px-2 py-0.5 text-xs rounded-full border ${
              connected
                ? "bg-green-100 text-green-800 border-green-300"
                : "bg-red-100 text-red-800 border-red-300"
            }`}
          >
            {connected ? "live" : "disconnected"}
          </span>
        </h2>
        <Button
          variant="outline"
          onClick={handleCancelAll}
          disabled={bulkBusy || unfilledCount === 0}
        >
          {bulkBusy
            ? "Cancelling..."
            : `Cancel all unfilled (${unfilledCount})`}
        </Button>
      </div>

      {message && (
        <div className="mb-4 p-2 bg-blue-100 text-blue-800 rounded-md text-sm">
          {message}
        </div>
      )}

      <Table className="w-full table-auto">
        <TableHeader>
          <TableRow>
            <TableHead>Perm ID</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Side</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Qty</TableHead>
            <TableHead>Filled / Rem</TableHead>
            <TableHead>Price</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Last error</TableHead>
            <TableHead className="text-center">Action</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {orders.length === 0 ? (
            <TableRow>
              <TableCell colSpan={10} className="text-gray-500">
                No active orders.
              </TableCell>
            </TableRow>
          ) : (
            orders.map((o) => {
              const isTerminal = o.status ? TERMINAL.has(o.status) : false;
              const cancelDisabled =
                isTerminal || o.filled > 0 || busyId === o.perm_id;
              return (
                <TableRow key={rowKey(o)}>
                  <TableCell className="font-mono text-xs">
                    {o.perm_id || `(oid ${o.order_id})`}
                  </TableCell>
                  <TableCell>{o.symbol || "—"}</TableCell>
                  <TableCell>{o.action || "—"}</TableCell>
                  <TableCell>{o.order_type || "—"}</TableCell>
                  <TableCell>{o.total_qty}</TableCell>
                  <TableCell>
                    {o.filled} / {o.remaining}
                  </TableCell>
                  <TableCell>
                    {o.lmt_price ?? o.aux_price ?? "—"}
                  </TableCell>
                  <TableCell>
                    <span
                      className={`inline-block px-2 py-0.5 text-xs rounded-full border ${statusStyle(
                        o.status
                      )}`}
                    >
                      {o.status || "Unknown"}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs text-red-700">
                    {o.last_error
                      ? `${o.last_error_code ?? ""} ${o.last_error}`.trim()
                      : ""}
                  </TableCell>
                  <TableCell className="text-center">
                    <Button
                      variant="ghost"
                      onClick={() => handleCancel(o)}
                      disabled={cancelDisabled}
                    >
                      {busyId === o.perm_id ? "Cancelling..." : "Cancel"}
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default LiveOrders;
