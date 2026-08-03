"""
Offline integration test for /cancel-order/{order_id} error translation.

Verifies the three router outcomes:
  * OrderNotFoundError from the service -> HTTP 404
  * Any other Exception from the service -> HTTP 500
  * Happy-path result dict -> HTTP 200 with CancelOrderResult payload

Uses FastAPI's TestClient + dependency overrides. No IB connection, no
network. Included in fastapi[all] so no extra installs needed.

Run from backend/:

    python scripts/dev_cancel_order_test.py

Exits 0 on all pass, 1 on any failure.
"""

from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from dependencies import get_ib, get_order_tracker  # noqa: E402
from routers import portfolio as portfolio_router  # noqa: E402
from services.portfolio.ib_client import (  # noqa: E402
    IbClient,
    OrderNotFoundError,
)


# ---- Stubs ----------------------------------------------------------

class StubIb:
    """Placeholder for the request.app.state.ib object -- unused by the
    router because we also patch cancel_order_by_id below."""


class StubTracker:
    """Same idea for the OrderTracker dependency."""


# ---- Test app -------------------------------------------------------

app = FastAPI()
app.include_router(portfolio_router.router)
app.dependency_overrides[get_ib] = lambda: StubIb()
app.dependency_overrides[get_order_tracker] = lambda: StubTracker()

client = TestClient(app)


# ---- Cases ----------------------------------------------------------

def with_patched_cancel(replacement):
    """Swap IbClient.cancel_order_by_id for the test, restore after."""
    original = IbClient.cancel_order_by_id
    IbClient.cancel_order_by_id = replacement
    return original


def restore(original):
    IbClient.cancel_order_by_id = original


def case_404_on_not_found():
    async def raising(self, order_id, timeout=5.0):
        raise OrderNotFoundError(order_id)

    original = with_patched_cancel(raising)
    try:
        resp = client.post("/api/portfolio/cancel-order/999999")
        assert resp.status_code == 404, (
            f"expected 404, got {resp.status_code}: {resp.text}"
        )
        detail = resp.json().get("detail", "")
        assert "999999" in detail, f"detail should mention order id: {detail!r}"
    finally:
        restore(original)


def case_500_on_other_exception():
    async def raising(self, order_id, timeout=5.0):
        raise RuntimeError("IB gateway exploded")

    original = with_patched_cancel(raising)
    try:
        resp = client.post("/api/portfolio/cancel-order/42")
        assert resp.status_code == 500, (
            f"expected 500, got {resp.status_code}: {resp.text}"
        )
        assert "exploded" in resp.json().get("detail", ""), (
            f"detail should carry str(e): {resp.text}"
        )
    finally:
        restore(original)


def case_200_on_happy_path():
    async def happy(self, order_id, timeout=5.0):
        return {
            "status": "Cancelled",
            "order_id": order_id,
            "symbol": "AAPL",
            "filled": 0.0,
            "remaining": 100.0,
        }

    original = with_patched_cancel(happy)
    try:
        resp = client.post("/api/portfolio/cancel-order/12345")
        assert resp.status_code == 200, (
            f"expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["status"] == "Cancelled"
        assert body["order_id"] == 12345
        assert body["symbol"] == "AAPL"
    finally:
        restore(original)


# ---- Runner ---------------------------------------------------------

CASES = [
    ("OrderNotFoundError -> 404 with order_id in detail", case_404_on_not_found),
    ("Generic Exception   -> 500 with str(e) in detail",  case_500_on_other_exception),
    ("Happy-path dict     -> 200 with CancelOrderResult", case_200_on_happy_path),
]


def main():
    passed = 0
    failed = 0
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}")
            print(f"      {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {name}")
            print(f"      {e.__class__.__name__}: {e}")
            print(traceback.format_exc(limit=3))
        else:
            passed += 1
            print(f"OK    {name}")

    print()
    print("=" * 50)
    print(f"PASSED: {passed}   FAILED: {failed}")
    print("=" * 50)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
