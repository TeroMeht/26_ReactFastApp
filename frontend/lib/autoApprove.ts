/**
 * Shared client-side toggle: "auto-accept automatic entry requests".
 *
 * Off by default. When ON, the AutomaticEntryApprovalDialog forwards
 * an Accept immediately for every pending row it receives, skipping
 * the confirmation modal.
 *
 * Design notes
 * ------------
 * - Source of truth is localStorage so the choice survives reloads.
 * - Cross-tab: the browser's built-in ``storage`` event fires in
 *   *other* tabs when this tab writes, so both the PendingOrders
 *   toggle and the ApprovalDialog stay in sync in every open tab.
 * - Same-tab: we dispatch a synthetic ``autoApproveChanged`` custom
 *   event alongside the write, because ``storage`` deliberately does
 *   *not* fire in the tab that made the change.
 *
 * FE-only gate on purpose: if the browser is closed the automatic
 * entry still parks in the backend hub as usual, so no order goes
 * through without a human ever having seen it.
 */

export const AUTO_APPROVE_KEY = "autoApproveAutomaticEntries";
export const AUTO_APPROVE_EVENT = "autoApproveChanged";

export function readAutoApprove(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(AUTO_APPROVE_KEY) === "true";
  } catch {
    return false;
  }
}

export function writeAutoApprove(value: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(AUTO_APPROVE_KEY, value ? "true" : "false");
  } catch {
    // localStorage may be blocked (private mode, disabled). The
    // toggle simply won't persist -- not a fatal condition.
  }
  window.dispatchEvent(
    new CustomEvent(AUTO_APPROVE_EVENT, { detail: value })
  );
}

/**
 * Subscribe to both the same-tab custom event and the cross-tab
 * ``storage`` event. Returns an unsubscribe function.
 */
export function subscribeAutoApprove(
  onChange: (value: boolean) => void
): () => void {
  if (typeof window === "undefined") return () => {};

  const onCustom = (e: Event) => {
    const detail = (e as CustomEvent<boolean>).detail;
    if (typeof detail === "boolean") onChange(detail);
  };
  const onStorage = (e: StorageEvent) => {
    if (e.key !== AUTO_APPROVE_KEY) return;
    onChange(e.newValue === "true");
  };

  window.addEventListener(AUTO_APPROVE_EVENT, onCustom);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(AUTO_APPROVE_EVENT, onCustom);
    window.removeEventListener("storage", onStorage);
  };
}
