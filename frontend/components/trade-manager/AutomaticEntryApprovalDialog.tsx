"use client";

import * as React from "react";
import { API_PREFIX } from "@/lib/api_prefix";
import {
  readAutoApprove,
  subscribeAutoApprove,
} from "@/lib/autoApprove";

/**
 * Global approval dialog for request_type="automatic" entry requests.
 *
 * Mounted once from the root layout so it works on every page. Opens a
 * single long-lived EventSource against
 *   GET  /api/portfolio/entry-request/pending/stream
 * and keeps a local queue of pending rows. Whenever the queue is non-empty
 * we render a modal for the head row with Accept / Decline buttons.
 *
 *  - Accept  -> POST /entry-request/approve with decision="accept". Backend
 *               places the bracket order and returns the standard
 *               EntryRequestResponse; on success we surface parent/stop
 *               order IDs in the "just handled" summary.
 *  - Decline -> POST with decision="decline". Backend drops the row.
 *
 * The SSE stream also emits "remove" events (e.g. if the row was consumed
 * elsewhere or the backend expired it), so we drop matching queue entries
 * to avoid stale prompts.
 *
 * Reconnect: mirrors LiveOrders.tsx — small delay + auto-reconnect on
 * onerror. No exponential backoff needed since the endpoint is local.
 */

type PendingApproval = {
  approval_id: string;
  symbol: string;
  contract_type: string;
  entry_price: number;
  stop_price: number;
  position_size: number;
  created_at: string;
};

type LastResult = {
  approval_id: string;
  symbol: string;
  allowed: boolean;
  message: string;
  parentOrderId?: number | null;
  stopOrderId?: number | null;
};

export default function AutomaticEntryApprovalDialog() {
  const [queue, setQueue] = React.useState<PendingApproval[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [lastResult, setLastResult] = React.useState<LastResult | null>(null);
  const esRef = React.useRef<EventSource | null>(null);

  // Auto-approve toggle (Off by default). Kept in a ref so the SSE
  // callback closure always reads the current value without needing
  // to re-subscribe when the toggle flips.
  const autoApproveRef = React.useRef<boolean>(false);
  React.useEffect(() => {
    autoApproveRef.current = readAutoApprove();
    return subscribeAutoApprove((v) => {
      autoApproveRef.current = v;
    });
  }, []);

  // --- SSE plumbing -------------------------------------------------------
  React.useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (cancelled) return;
      const es = new EventSource(
        `${API_PREFIX}/portfolio/entry-request/pending/stream`
      );
      esRef.current = es;

      es.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data);
          if (payload.type === "snapshot") {
            // On (re)connect the backend replays every pending row so the
            // dialog can't miss one just because the tab was closed.
            const rows = payload.pending as PendingApproval[];
            if (autoApproveRef.current) {
              // Fire-and-forget accept for every parked row; don't queue.
              rows.forEach((r) => autoAcceptRef.current(r));
            } else {
              setQueue(rows);
            }
          } else if (payload.type === "add") {
            const row = payload.pending as PendingApproval;
            if (autoApproveRef.current) {
              autoAcceptRef.current(row);
            } else {
              setQueue((prev) =>
                // Dedup by approval_id — snapshot might race with an add on
                // reconnect if the backend just parked a new one.
                prev.some((p) => p.approval_id === row.approval_id)
                  ? prev
                  : [...prev, row]
              );
            }
          } else if (payload.type === "remove") {
            const id = payload.approval_id as string;
            setQueue((prev) => prev.filter((p) => p.approval_id !== id));
          }
          // ping -> ignore
        } catch (err) {
          console.error("[AutoEntryApproval] SSE parse error:", err);
        }
      };

      es.onerror = () => {
        es.close();
        if (cancelled) return;
        retryTimer = setTimeout(connect, 2000);
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      esRef.current?.close();
    };
  }, []);

  // --- Decision dispatch --------------------------------------------------
  // ``silent`` skips the modal-busy state so an auto-approve doesn't
  // flash a "Sending…" label on a dialog that isn't even mounted.
  const decide = React.useCallback(
    async (
      approval: PendingApproval,
      decision: "accept" | "decline",
      opts: { silent?: boolean } = {}
    ) => {
      const { silent = false } = opts;
      if (!silent) setBusy(true);
      try {
        const res = await fetch(
          `${API_PREFIX}/portfolio/entry-request/approve`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              approval_id: approval.approval_id,
              decision,
            }),
          }
        );

        if (res.status === 404) {
          // Backend says it doesn't know this approval anymore. Drop it from
          // our queue so we don't keep showing a stale popup.
          setQueue((prev) =>
            prev.filter((p) => p.approval_id !== approval.approval_id)
          );
          return;
        }

        if (!res.ok) {
          const text = await res.text();
          throw new Error(`Approve failed: ${text}`);
        }

        const data = await res.json();
        setLastResult({
          approval_id: approval.approval_id,
          symbol: data.symbol,
          allowed: data.allowed,
          message: data.message,
          parentOrderId: data.parentOrderId,
          stopOrderId: data.stopOrderId,
        });

        // Belt-and-braces: the SSE remove event should already have cleared
        // this row, but pop it locally too so the modal closes instantly.
        setQueue((prev) =>
          prev.filter((p) => p.approval_id !== approval.approval_id)
        );
      } catch (err) {
        console.error("[AutoEntryApproval] decide error:", err);
        setLastResult({
          approval_id: approval.approval_id,
          symbol: approval.symbol,
          allowed: false,
          message:
            err instanceof Error ? err.message : "Failed to submit decision",
        });
      } finally {
        if (!silent) setBusy(false);
      }
    },
    []
  );

  // Fire the auto-approve POST without adding the row to the modal queue.
  // Kept as a plain function so both the ``add`` and ``snapshot`` SSE
  // handlers can share it without a fresh closure per render.
  const autoAcceptRef = React.useRef<(a: PendingApproval) => void>(() => {});
  React.useEffect(() => {
    autoAcceptRef.current = (a) => decide(a, "accept", { silent: true });
  }, [decide]);

  // Auto-dismiss the transient "last result" banner after 6s.
  React.useEffect(() => {
    if (!lastResult) return;
    const id = window.setTimeout(() => setLastResult(null), 6000);
    return () => window.clearTimeout(id);
  }, [lastResult]);

  const head = queue[0];

  return (
    <>
      {head && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="auto-entry-approval-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        >
          <div className="w-full max-w-md rounded-lg bg-white shadow-2xl border border-gray-200">
            <div className="px-5 py-4 border-b border-gray-200">
              <div
                id="auto-entry-approval-title"
                className="text-lg font-bold text-gray-900"
              >
                Automatic entry — accept?
              </div>
              <div className="mt-1 text-sm text-gray-500">
                An automatic request cleared all guards. It will only be
                placed if you accept.
              </div>
            </div>

            <div className="px-5 py-4 text-sm text-gray-800 space-y-1">
              <div>
                <span className="text-gray-500">Symbol:</span>{" "}
                <span className="font-semibold">{head.symbol}</span>
                <span className="text-gray-400"> ({head.contract_type})</span>
              </div>
              <div>
                <span className="text-gray-500">Entry:</span>{" "}
                <span className="font-mono">{head.entry_price}</span>
              </div>
              <div>
                <span className="text-gray-500">Stop:</span>{" "}
                <span className="font-mono">{head.stop_price}</span>
              </div>
              <div>
                <span className="text-gray-500">Size:</span>{" "}
                <span className="font-mono">{head.position_size}</span>
              </div>
              {queue.length > 1 && (
                <div className="pt-2 text-xs text-gray-500">
                  {queue.length - 1} more waiting after this one.
                </div>
              )}
            </div>

            <div className="px-5 py-4 flex items-center justify-end gap-2 border-t border-gray-200">
              <button
                type="button"
                onClick={() => decide(head, "decline")}
                disabled={busy}
                className="px-4 py-2 text-sm rounded-md border border-gray-300 bg-white text-gray-700 hover:bg-gray-100 disabled:opacity-50"
              >
                Decline
              </button>
              <button
                type="button"
                onClick={() => decide(head, "accept")}
                disabled={busy}
                className="px-4 py-2 text-sm rounded-md bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
              >
                {busy ? "Sending…" : "Accept"}
              </button>
            </div>
          </div>
        </div>
      )}

      {lastResult && (
        <div
          role="status"
          aria-live="polite"
          className={`fixed bottom-4 left-1/2 -translate-x-1/2 z-40 max-w-md rounded-md px-4 py-2 text-sm shadow-lg break-words ${
            lastResult.allowed
              ? "bg-green-100 text-green-900 border border-green-300"
              : "bg-red-100 text-red-900 border border-red-300"
          }`}
        >
          <div className="font-semibold">
            {lastResult.symbol}: {lastResult.allowed ? "sent" : "not sent"}
          </div>
          <div>{lastResult.message}</div>
          {lastResult.allowed && lastResult.parentOrderId ? (
            <div className="mt-0.5 font-mono text-xs">
              parent={lastResult.parentOrderId} stop={lastResult.stopOrderId}
            </div>
          ) : null}
        </div>
      )}
    </>
  );
}
