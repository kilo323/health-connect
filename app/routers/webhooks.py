"""Google Health API webhook receiver.

Receives push notifications when a user's health data changes, replacing
high-frequency scheduler polling. Registered via projects.subscribers with
subscriptionCreatePolicy=AUTOMATIC (see scripts/register_google_health_subscriber.py).

Protocol (per developers.google.com/health/webhooks):
  - Verification handshake at subscriber create/update: POST {"type": "verification"}
    with the configured Authorization header -> expect 200; without credentials -> 401.
  - Notifications: POST {"data": {healthUserId, dataType, operation, intervals[]}}
    with the Authorization header equal to settings.webhook_secret.
  - Must respond 204 immediately and process asynchronously (retries otherwise,
    and delivery must be idempotent — duplicates happen).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from ..config import settings
from ..database import async_session_factory
from ..models.settings import AppSettings
from ..services.scheduler import sync_health_data, API_TYPE_TO_INTERNAL

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Webhooks"])


def _is_authorized(request: Request) -> bool:
    """Constant-time-ish check of the shared secret sent in the Authorization header."""
    if not settings.webhook_secret:
        return False
    return request.headers.get("authorization", "") == settings.webhook_secret


async def _user_id_for_health_user(health_user_id: str) -> Optional[int]:
    """Map a Google Health healthUserId to a local user id (stored at OAuth time)."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(AppSettings).where(AppSettings.key == f"google_health_uid_{health_user_id}")
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        try:
            return int(row.value)
        except (TypeError, ValueError):
            return None


def _parse_intervals(data: dict) -> tuple[Optional[datetime], Optional[datetime]]:
    """Extract the physical start/end bounds from a notification's intervals list."""
    start = end = None
    for interval in data.get("intervals", []):
        physical = interval.get("physicalTimeInterval", {})
        start_str = physical.get("startTime")
        end_str = physical.get("endTime")
        try:
            if start_str:
                candidate = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                start = candidate if start is None or candidate < start else start
            if end_str:
                candidate = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                end = candidate if end is None or candidate > end else end
        except ValueError:
            continue
    return start, end


async def _process_notification(data: dict):
    """Background task: fetch the changed data and persist it (idempotent)."""
    health_user_id = data.get("healthUserId", "")
    api_type = data.get("dataType", "")
    operation = data.get("operation", "UPSERT")

    internal_type = API_TYPE_TO_INTERNAL.get(api_type)
    if not internal_type:
        logger.debug(f"Webhook: ignoring unmapped data type {api_type!r}")
        return

    user_id = await _user_id_for_health_user(health_user_id)
    if user_id is None:
        logger.warning(f"Webhook: no local user mapped for healthUserId {health_user_id!r}")
        return

    if operation == "DELETE":
        # Deletes are rare; rely on the next scheduler sweep to reconcile.
        logger.info(f"Webhook: DELETE for user {user_id} type {internal_type} — left for scheduler reconciliation")
        return

    start, end = _parse_intervals(data)
    if start is None or end is None:
        # No usable interval — fall back to a recent window.
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=2)
    else:
        # Pad the window to catch late-arriving adjacent points.
        start -= timedelta(hours=1)
        end += timedelta(hours=1)

    try:
        saved = await sync_health_data(user_id, start, end, data_types=[internal_type])
        if saved:
            logger.info(f"Webhook: saved {saved} {internal_type} record(s) for user {user_id}")
    except Exception as e:
        logger.warning(f"Webhook: failed to sync {internal_type} for user {user_id}: {e}")


@router.post("/webhooks/google-health")
async def google_health_webhook(request: Request):
    """Receive Google Health API push notifications."""
    if not settings.webhook_secret:
        return Response(status_code=404)  # webhooks not configured — stay dark

    try:
        body = await request.json()
    except Exception:
        return Response(status_code=400)

    # Subscriber verification handshake (authed -> 200, unauthed -> 401)
    if isinstance(body, dict) and body.get("type") == "verification":
        if _is_authorized(request):
            logger.info("Webhook verification handshake accepted")
            return Response(status_code=200)
        logger.warning("Webhook verification handshake rejected (missing/bad Authorization)")
        return Response(status_code=401)

    # Notifications: enforce the shared secret, ack immediately, process async.
    if not _is_authorized(request):
        return Response(status_code=401)

    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        asyncio.create_task(_process_notification(data))

    return Response(status_code=204)
