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

  // Exit plan chosen per pending order. Each pending order keeps a record
  // mapping strategy -> trim percentage (0, 0.25, 0.5, 0.75, 1). The Send
  // button only enables when the trims across all three strategies sum
  // to exactly 1.0 — every entry must carry a fully-allocated exit plan.
  type ExitStrategy = "momentum_exit" | "swing_exit" | "vwap_exit";
  type TrimValue = 0 | 0.25 | 0.5 | 0.75 | 1;
  const EXIT_STRATEGIES: { value: ExitStrategy; label: string }[] = [
    { value: "momentum_exit", label: "momentum (EMA9 cross)" },
    { value: "swing_exit", label: "swing (manual trim)" },
    { value: "vwap_exit", label: "vwap (price near VWAP)" },
  ];
  const TRIM_VALUES: { value: TrimValue; label: string }[] = [
    { value: 0, label: "Off" },
    { value: 0.25, label: "25%" },
    { value: 0.5, label: "50%" },
    { value: 0.75, label: "75%" },
    { value: 1, label: "100%" },
  ];
  // exitPlans[orderId] = { momentum_exit: 0.5, swing_exit: 0.5, vwap_exit: 0 }
  const [exitPlans, setExitPlans] = useState<
    Record<string, Record<ExitStrategy, TrimValue>>
  >({});

  const getPlan = (orderId: string): Record<ExitStrategy, TrimValue> =>
    exitPlans[orderId] ?? {
      momentum_exit: 0,
      swing_exit: 0,
      vwap_exit: 0,
    };
  const planTotal = (orderId: string): number => {
    const p = getPlan(orderId);
    return p.momentum_exit + p.swing_exit + p.vwap_exit;
  };
  const planIsValid = (orderId: string): boolean =>
    Math.abs(planTotal(orderId) - 1) < 1e-9;
  const planLegs = (
    orderId: string,
  ): { strategy: ExitStrategy; trim_percentage: TrimValue }[] => {
    const p = getPlan(orderId);
    return (Object.keys(p) as ExitStrategy[])
      .filter((s) => p[s] > 0)
      .map((s) => ({ strategy: s, trim_percentage: p[s] }));
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
      // Hard gate: the exit plan's trims must add up to exactly 100%
      // before we even talk to the backend. The schema rejects bad
      // sums too, but failing fast in the UI saves a round-trip and
      // gives the user a clearer message.
      if (!planIsValid(order.id)) {
        const pct = Math.round(planTotal(order.id) * 100);
        setApiMessage(
          `Exit plan for ${order.symbol} must total 100% (currently ${pct}%).`,
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
        exit_plan: planLegs(order.id),
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
            <TableHead>Exit Plan</TableHead>
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
            positions.map((order) => (
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
                  {/* Multi-leg exit-plan picker. One trim selector per
                      strategy — pick "Off" to disable a leg. The total
                      must equal 100% or Send stays disabled. */}
                  <div className="flex flex-col gap-1 text-xs">
                    {EXIT_STRATEGIES.map((s) => (
                      <div
                        key={s.value}
                        className="flex items-center gap-2 whitespace-nowrap"
                      >
                        <span className="w-32 truncate" title={s.label}>
                          {s.label}
                        </span>
                        <select
                          value={getPlan(order.id)[s.value]}
                          onChange={(e) =>
                            setExitPlans((prev) => {
                              const cur = prev[order.id] ?? {
                                momentum_exit: 0 as TrimValue,
                                swing_exit: 0 as TrimValue,
                                vwap_exit: 0 as TrimValue,
                              };
                              return {
                                ...prev,
                                [order.id]: {
                                  ...cur,
                                  [s.value]: Number(e.target.value) as TrimValue,
                                },
                              };
                            })
                          }
                          className="border border-input rounded px-1 py-0.5 bg-white"
                        >
                          {TRIM_VALUES.map((opt) => (
                            <option key={opt.value} value={opt.value}>
                              {opt.label}
                            </option>
                          ))}
                        </select>
                      </div>
                    ))}
                    <div
                      className={`mt-1 font-mono ${
                        planIsValid(order.id)
                          ? "text-green-700"
                          : "text-red-700"
                      }`}
                    >
                      Total: {Math.round(planTotal(order.id) * 100)}%
                    </div>
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
                      disabled={
                        allowedOrders.has(order.id) ||
                        !planIsValid(order.id)
                      }
                      title={
                        !planIsValid(order.id)
                          ? "Exit plan trims must total exactly 100%"
                          : undefined
                      }
                    >
                      Send
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
};

export default PendingOrdersTable;
