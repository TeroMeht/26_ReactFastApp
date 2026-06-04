"use client";

import React, { useState, useEffect, useCallback } from "react";
import { API_PREFIX } from "@/lib/api_prefix";
import { paths } from "@/generated/api";

import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";

import { Button } from "@/components/ui/button";

// Exit strategies the user can arm at entry time. Mirrors backend
// settings.EXIT_TRIGGERS — keep this in sync with the env config.
// swing_trade is a passive marker (no streamer trigger); the rest map to
// real alarms the streamer emits.
const EXIT_STRATEGY_OPTIONS = [
  { value: "momentum_long_exit", label: "momentum_long_exit" },
  { value: "momentum_short_exit", label: "momentum_short_exit" },
  { value: "endofday_exit", label: "endofday_exit" },
  { value: "vwap_exit", label: "vwap_exit" },
  { value: "swing_trade", label: "swing_trade" },
];

const TRIM_OPTIONS = [
  { value: 0.25, label: "25%" },
  { value: 0.5, label: "50%" },
  { value: 0.75, label: "75%" },
  { value: 1, label: "100%" },
];

// Small tolerance for floating-point sum comparisons (e.g. 0.25 + 0.5 + 0.25
// can drift by ~1e-17). All trim values are 0.25 multiples so any miss > 1e-9
// is a real discrepancy.
const SUM_EPS = 1e-9;

type ExitSpec = { strategy: string; trim_percentage: number };

type PendingOrder =
  paths["/api/pending_orders/orders"]["get"]["responses"]["200"]["content"]["application/json"][number];

type Props = {
  /**
   * Optional callback fired whenever this component fetches fresh data
   * (Refresh button click, post-Send cleanup). The page uses it to keep
   * the EntryAttemptsTable in the page header in sync.
   */
  onRefreshed?: () => void;
};

const PendingOrdersTable = ({ onRefreshed }: Props = {}) => {
  const [positions, setPositions] = useState<PendingOrder[]>([]);
  const [apiMessage, setApiMessage] = useState<string | null>(null);
  const [apiMessageAllowed, setApiMessageAllowed] = useState<boolean | null>(null);
  const [allowedOrders, setAllowedOrders] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Cooldown state — separate from `apiMessage` because the cooldown banner
  // must remain visible (with a countdown) until entries are allowed again,
  // whereas other messages auto-dismiss after a few seconds.
  const [cooldown, setCooldown] = useState<{
    until: number; // epoch ms when cooldown expires
    symbol: string;
    detail: string;
  } | null>(null);
  const [now, setNow] = useState<number>(() => Date.now());

  // Tick once per second while a cooldown is active so the countdown
  // re-renders. When the cooldown expires we clear the banner.
  useEffect(() => {
    if (!cooldown) return;
    const id = setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (t >= cooldown.until) {
        setCooldown(null);
      }
    }, 1000);
    return () => clearInterval(id);
  }, [cooldown]);

  const formatRemaining = (ms: number): string => {
    if (ms <= 0) return "0s";
    const total = Math.ceil(ms / 1000);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return m > 0 ? `${m}m ${s.toString().padStart(2, "0")}s` : `${s}s`;
  };

  const [contractTypes, setContractTypes] = useState<Record<string, "CFD" | "stock">>({});
  const [openDropdown, setOpenDropdown] = useState<string | null>(null);

  // Per-order exit_strategies the user has armed. Send is gated on >=1 entry
  // here. Cleared whenever an order is removed or successfully sent.
  const [exitsByOrder, setExitsByOrder] = useState<Record<string, ExitSpec[]>>({});
  // Inline add-exit form state, keyed by orderId so two rows can edit at once.
  const [exitDraft, setExitDraft] = useState<Record<string, { strategy: string; trim: number }>>({});

  const getExits = (orderId: string): ExitSpec[] => exitsByOrder[orderId] || [];

  const getDraft = (orderId: string) => exitDraft[orderId] || { strategy: "", trim: 1 };

  const setDraft = (orderId: string, patch: Partial<{ strategy: string; trim: number }>) => {
    setExitDraft((prev) => ({
      ...prev,
      [orderId]: { ...getDraft(orderId), ...patch },
    }));
  };

  const addExitToOrder = (orderId: string) => {
    const draft = getDraft(orderId);
    if (!draft.strategy) return;
    setExitsByOrder((prev) => {
      const list = prev[orderId] || [];
      if (list.some((e) => e.strategy === draft.strategy)) return prev;
      return {
        ...prev,
        [orderId]: [...list, { strategy: draft.strategy, trim_percentage: draft.trim }],
      };
    });
    setExitDraft((prev) => ({ ...prev, [orderId]: { strategy: "", trim: 1 } }));
  };

  const removeExitFromOrder = (orderId: string, strategy: string) => {
    setExitsByOrder((prev) => ({
      ...prev,
      [orderId]: (prev[orderId] || []).filter((e) => e.strategy !== strategy),
    }));
  };

  const clearOrderState = (orderId: string) => {
    setExitsByOrder((prev) => {
      const next = { ...prev };
      delete next[orderId];
      return next;
    });
    setExitDraft((prev) => {
      const next = { ...prev };
      delete next[orderId];
      return next;
    });
  };

  const fetchPositions = useCallback(async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_PREFIX}/pending_orders/orders`);
      const json = await res.json();
      setPositions(json as PendingOrder[]);
    } catch (err) {
      console.error("Fetch error:", err);
      setPositions([]);
    } finally {
      setLoading(false);
      // Notify parent so the EntryAttemptsTable in the page header refetches.
      onRefreshed?.();
    }
  }, [onRefreshed]);

  useEffect(() => {
    fetchPositions();
  }, [fetchPositions]);

  // Server-side removal of a pending order row.  Used both by the explicit
  // Delete button and by the Send flow (on a successful IB entry request we
  // don't want the now-live order hanging around in the pending list).
  // Returns true on success so callers can decide whether to update the UI.
  const removeOrderServerSide = async (order: PendingOrder): Promise<boolean> => {
    let res: Response;

    if (order.source === "ALPACA") {
      res = await fetch(`${API_PREFIX}/pending_orders/manual/${order.id}`, {
        method: "DELETE",
      });
    } else if (order.source === "DB") {
      res = await fetch(`${API_PREFIX}/pending_orders/auto/${order.id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: order.id }),
      });
    } else {
      console.warn("Unknown order source, cannot remove", order);
      return false;
    }

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Remove failed: ${text}`);
    }
    return true;
  };

  const handleSend = async (order: PendingOrder) => {
    try {
      const contractType = contractTypes[order.id] ?? "stock";
      const exits = getExits(order.id);
      const exitsSum = exits.reduce((s, e) => s + e.trim_percentage, 0);
      if (Math.abs(exitsSum - 1) > SUM_EPS) {
        setApiMessage(
          `Exit strategies must cover exactly 100% for ${order.symbol} ` +
            `(currently ${Math.round(exitsSum * 100)}%).`,
        );
        setApiMessageAllowed(false);
        setTimeout(() => {
          setApiMessage(null);
          setApiMessageAllowed(null);
        }, 5000);
        return;
      }
      const payload = {
        symbol: order.symbol,
        entry_price: order.latest_price,
        stop_price: order.stop_price,
        position_size: order.position_size,
        contract_type: contractType,
        exit_strategies: exits,
      };

      const res = await fetch(`${API_PREFIX}/portfolio/entry-request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
        parentOrderId: number;
        stopOrderId: number;
        reason?: string | null;
        cooldown_until?: string | null;
      } = await res.json();

      // Loss cooldown — show a persistent banner with a countdown instead
      // of the short-lived apiMessage. The banner clears itself once the
      // cooldown_until timestamp passes (handled by the interval above).
      if (
        !data.allowed &&
        data.reason === "loss_cooldown" &&
        data.cooldown_until
      ) {
        const untilMs = new Date(data.cooldown_until).getTime();
        if (!Number.isNaN(untilMs) && untilMs > Date.now()) {
          setCooldown({
            until: untilMs,
            symbol: data.symbol,
            detail: data.message,
          });
          // Do not also set the transient apiMessage — the cooldown banner
          // is the canonical surface for this state.
          setApiMessage(null);
          setApiMessageAllowed(null);
          return;
        }
      }

      setApiMessage(
        `Symbol: ${data.symbol}, Allowed: ${data.allowed}, Message: ${data.message}`
      );
      setApiMessageAllowed(data.allowed);

      // Order accepted by IB — remove it from the pending list so the row
      // doesn't linger after the entry has gone live.  Do this both in local
      // state (immediate) and server-side (so a refresh doesn't resurrect it).
      if (data.allowed) {
        setPositions((prev) => prev.filter((o) => o.id !== order.id));
        setAllowedOrders((prev) => {
          const next = new Set(prev);
          next.delete(order.id);
          return next;
        });
        clearOrderState(order.id);
        // Tell parent the underlying state changed so the EntryAttemptsTable
        // refetches once IB reports the fill.
        onRefreshed?.();
        try {
          await removeOrderServerSide(order);
        } catch (cleanupErr: any) {
          // Send succeeded but we couldn't clean up the pending row.  Surface
          // the problem rather than silently leaving a stale row; next
          // Refresh will bring it back into the table.
          console.error("Failed to remove pending order after send:", cleanupErr);
          setMessage(
            `Order sent but failed to remove pending row: ${cleanupErr.message || cleanupErr}`
          );
          setTimeout(() => setMessage(null), 5000);
        }
      }
    } catch (err: any) {
      console.error("Error sending order:", err);
      setApiMessage(`Error: ${err.message || err}`);
      setApiMessageAllowed(false);
    }

    setTimeout(() => {
      setApiMessage(null);
      setApiMessageAllowed(null);
    }, 10000);
  };

  const handleDelete = async (order: PendingOrder) => {
    try {
      const ok = await removeOrderServerSide(order);
      if (!ok) return;

      setPositions((prev) => prev.filter((o) => o.id !== order.id));
      clearOrderState(order.id);
      setMessage(`Order ${order.id} canceled successfully.`);
    } catch (err: any) {
      console.error("Error canceling order:", err);
      setMessage(`Error canceling order: ${err.message || err}`);
    }

    setTimeout(() => setMessage(null), 3000);
  };

  return (
    <div className="py-4">
      <h2 className="text-xl font-bold mb-4">Pending Orders</h2>

      <Button
        variant="outline"
        onClick={fetchPositions}
        disabled={loading}
      >
        {loading ? "Refreshing..." : "Refresh"}
      </Button>

      {message && (
        <div className="mb-4 p-2 bg-blue-100 text-blue-800 rounded-md text-sm">
          {message}
        </div>
      )}

      {cooldown && (
        <div
          role="alert"
          aria-live="polite"
          className="mb-4 p-3 rounded-md text-sm border border-amber-300 bg-amber-50 text-amber-900 break-words"
        >
          <div className="font-semibold">
            Loss cooldown active — entries blocked for {cooldown.symbol}
          </div>
          <div>{cooldown.detail}</div>
          <div className="mt-1">
            Entry allowed again in{" "}
            <span className="font-mono font-semibold">
              {formatRemaining(cooldown.until - now)}
            </span>
            {" "}
            <span className="text-amber-700">
              (at {new Date(cooldown.until).toLocaleTimeString()})
            </span>
          </div>
        </div>
      )}

      {apiMessage && (
        <div
          className={`mb-4 p-2 rounded-md text-sm break-words ${
            apiMessageAllowed === false
              ? "bg-red-100 text-red-800"
              : "bg-blue-100 text-blue-800"
          }`}
        >
          {apiMessage}
        </div>
      )}

      <Table className="w-full table-auto">
        <TableHeader>
          <TableRow>
            <TableHead>Id</TableHead>
            <TableHead>Symbol</TableHead>
            <TableHead>Contract</TableHead>
            <TableHead>Latest Price</TableHead>
            <TableHead>Stop Price</TableHead>
            <TableHead>Quantity</TableHead>
            <TableHead>Size</TableHead>
            <TableHead>Exits</TableHead>
            <TableHead className="text-center">Actions</TableHead>
          </TableRow>
        </TableHeader>

        <TableBody>
          {positions.length === 0 ? (
            <TableRow>
              <TableCell colSpan={9} className="text-gray-500">
                No orders found.
              </TableCell>
            </TableRow>
          ) : (
            positions.map((order) => {
              const exits = getExits(order.id);
              const draft = getDraft(order.id);
              const availableExitOptions = EXIT_STRATEGY_OPTIONS.filter(
                (opt) => !exits.some((e) => e.strategy === opt.value),
              );
              const exitsSum = exits.reduce(
                (s, e) => s + e.trim_percentage,
                0,
              );
              const remaining = Math.max(0, 1 - exitsSum);
              // Only trim values that fit inside the remaining capacity.
              // SUM_EPS guards against floating drift when remaining lands
              // exactly on 0.25/0.5/0.75/1.
              const availableTrimOptions = TRIM_OPTIONS.filter(
                (t) => t.value <= remaining + SUM_EPS,
              );
              const canAdd =
                availableExitOptions.length > 0 &&
                availableTrimOptions.length > 0;
              const sumIsFull = Math.abs(exitsSum - 1) <= SUM_EPS;
              const sendDisabled =
                allowedOrders.has(order.id) || !sumIsFull;
              return (
                <TableRow key={order.id}>
                  <TableCell>{order.id}</TableCell>
                  <TableCell>{order.symbol}</TableCell>
                  <TableCell>
                    <div className="relative">
                      <button
                        className="px-3 py-1 text-sm rounded-md border border-input bg-gray-200 hover:bg-gray-400 transition-colors"
                        onClick={() =>
                          setOpenDropdown(openDropdown === order.id ? null : order.id)
                        }
                      >
                        {contractTypes[order.id] ?? "stock"}
                      </button>

                      {openDropdown === order.id && (
                        <div className="absolute z-50 mt-1 w-28 rounded-md border border-input bg-white shadow-md">
                          {(["stock", "CFD"] as const).map((option) => (
                            <button
                              key={option}
                              className={`w-full text-left px-3 py-2 text-sm hover:bg-muted transition-colors ${
                                (contractTypes[order.id] ?? "stock") === option
                                  ? "bg-gray-200 text-primary font-medium"
                                  : "text-foreground hover:bg-gray-200"
                              }`}
                              onClick={() => {
                                setContractTypes((prev) => ({
                                  ...prev,
                                  [order.id]: option,
                                }));
                                setOpenDropdown(null);
                              }}
                            >
                              {option}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </TableCell>

                  <TableCell>{order.latest_price}</TableCell>
                  <TableCell>{order.stop_price}</TableCell>
                  <TableCell>{order.position_size}</TableCell>
                  <TableCell>{order.size}</TableCell>
                  <TableCell>
                    <div className="flex flex-col gap-1 min-w-[260px]">
                      {/* Already-added exits as removable chips, plus a
                          running coverage indicator. */}
                      <div className="flex flex-wrap items-center gap-1">
                        {exits.length === 0 ? (
                          <span className="text-[11px] text-red-600">
                            Required — cover 100%
                          </span>
                        ) : (
                          exits.map((e) => (
                            <span
                              key={e.strategy}
                              className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-blue-100 text-blue-800 rounded text-[11px] font-mono"
                            >
                              {e.strategy} @ {Math.round(e.trim_percentage * 100)}%
                              <button
                                type="button"
                                className="text-blue-700 hover:text-red-700"
                                onClick={() =>
                                  removeExitFromOrder(order.id, e.strategy)
                                }
                                aria-label={`remove ${e.strategy}`}
                              >
                                ×
                              </button>
                            </span>
                          ))
                        )}
                        {exits.length > 0 && (
                          <span
                            className={`text-[11px] font-mono ${
                              sumIsFull ? "text-green-700" : "text-amber-700"
                            }`}
                          >
                            {Math.round(exitsSum * 100)}%
                          </span>
                        )}
                      </div>

                      {/* Add-form: dropdowns + Add. Hidden once coverage is
                          full (sum >= 100%) or no strategies/trim values
                          remain. Trim dropdown is filtered to options that
                          fit inside the remaining capacity, so the picked
                          trim can never push the sum over 100%. */}
                      {canAdd && (
                        <div className="flex flex-wrap items-center gap-1">
                          <select
                            value={draft.strategy}
                            onChange={(e) =>
                              setDraft(order.id, { strategy: e.target.value })
                            }
                            className="border rounded px-1 py-0.5 text-xs"
                          >
                            <option value="" disabled>
                              strategy…
                            </option>
                            {availableExitOptions.map((opt) => (
                              <option key={opt.value} value={opt.value}>
                                {opt.label}
                              </option>
                            ))}
                          </select>
                          <select
                            value={
                              availableTrimOptions.some(
                                (t) => t.value === draft.trim,
                              )
                                ? draft.trim
                                : availableTrimOptions[
                                    availableTrimOptions.length - 1
                                  ].value
                            }
                            onChange={(e) =>
                              setDraft(order.id, { trim: Number(e.target.value) })
                            }
                            className="border rounded px-1 py-0.5 text-xs"
                          >
                            {availableTrimOptions.map((opt) => (
                              <option key={opt.value} value={opt.value}>
                                {opt.label}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            disabled={!draft.strategy}
                            onClick={() => addExitToOrder(order.id)}
                            className="px-2 py-0.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                          >
                            Add
                          </button>
                        </div>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-center whitespace-nowrap">
                    <div className="flex flex-row items-center justify-center gap-2">
                      <Button variant="ghost" onClick={() => handleDelete(order)}>
                        Delete
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => handleSend(order)}
                        disabled={sendDisabled}
                        title={
                          sumIsFull
                            ? undefined
                            : `Exit coverage must total 100% (currently ${Math.round(
                                exitsSum * 100,
                              )}%)`
                        }
                      >
                        Send
                      </Button>
                    </div>
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

export default PendingOrdersTable;
