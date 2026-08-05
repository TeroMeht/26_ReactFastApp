"""PendingApprovalsHub wiring.

Instantiates the hub (once) and stashes it on ``app.state`` so the
FastAPI dependency can hand it out to the entry-request router and the
SSE stream endpoint.

Unlike OpenRiskHub, this hub is purely producer-driven: no IB events
push into it, so there's nothing to bind. The router calls into it
when a request_type="automatic" entry passes all guards.

Must run after connect_ib / init_database (order-independent, but
kept next to wire_openrisk_hub for readability).
"""
import logging

from fastapi import FastAPI

from services.portfolio.pending_approvals_hub import PendingApprovalsHub

logger = logging.getLogger(__name__)


async def wire_pending_approvals_hub(app: FastAPI) -> None:
    hub = PendingApprovalsHub()
    app.state.pending_approvals_hub = hub
    logger.info("PendingApprovalsHub wired")
