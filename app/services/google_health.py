import asyncio
import httpx
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlalchemy import select
from ..database import async_session_factory
from ..models.settings import AppSettings
from .encryption import encryption_service

logger = logging.getLogger(__name__)

# Marker substring of the Google Health API error returned when the authorized
# Google account has no linked Google Health (Fitbit) profile.
NOT_LINKED_MARKER = "not linked to Google Health"


class GoogleHealthService:
    BASE_URL = "https://health.googleapis.com"

    async def _get_user_tokens(self, user_id: int) -> dict:
        """Get stored tokens for a user from AppSettings"""
        async with async_session_factory() as db:
            result = await db.execute(
                select(AppSettings).where(AppSettings.key == f"google_health_tokens_{user_id}")
            )
            row = result.scalar_one_or_none()
            if not row:
                return {}
            try:
                return json.loads(row.value)
            except (json.JSONDecodeError, TypeError):
                return {}

    async def _save_user_tokens(self, user_id: int, encrypted_tokens: dict):
        """Save encrypted tokens for a user in AppSettings"""
        async with async_session_factory() as db:
            key = f"google_health_tokens_{user_id}"
            result = await db.execute(select(AppSettings).where(AppSettings.key == key))
            existing = result.scalar_one_or_none()

            value = json.dumps(encrypted_tokens)
            if existing:
                existing.value = value
            else:
                db.add(AppSettings(key=key, value=value, description="Google Health API OAuth tokens"))
            await db.commit()

    async def _get_admin_config(self) -> dict:
        """Get app-level Google OAuth config (set by admin)"""
        async with async_session_factory() as db:
            result = await db.execute(
                select(AppSettings).where(AppSettings.key == "google_oauth_config")
            )
            row = result.scalar_one_or_none()
            if not row:
                return {}
            try:
                return json.loads(row.value)
            except (json.JSONDecodeError, TypeError):
                return {}

    async def refresh_access_token(self, user_id: int) -> bool:
        """Refresh the access token using refresh token"""
        tokens = await self._get_user_tokens(user_id)
        admin_config = await self._get_admin_config()

        if not tokens.get("refresh_token") or not admin_config.get("client_id"):
            return False

        try:
            refresh_token = encryption_service.decrypt(tokens["refresh_token"])
        except ValueError:
            return False

        # Explicit timeout: without one, a hung/unreachable OAuth endpoint would
        # block the refresh forever and wedge the whole sync (the fetch paths
        # already use timeouts; this one did not).
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": admin_config["client_id"],
                    "client_secret": admin_config["client_secret"],
                    "refresh_token": refresh_token,
                },
            )

        if response.status_code == 200:
            new_tokens = response.json()
            encrypted_new_tokens = {
                "access_token": encryption_service.encrypt(new_tokens["access_token"]),
                "refresh_token": encryption_service.encrypt(new_tokens.get("refresh_token", refresh_token)),
                "expires_in": new_tokens.get("expires_in", 0),
            }
            await self._save_user_tokens(user_id, encrypted_new_tokens)
            return True

        return False

    # Maps internal data type names to Google Health API v4 identifiers.
    # Kebab-case is used in endpoint paths and filter expressions.
    # Types without a v4 equivalent (blood_pressure, bmr, speed) are intentionally omitted.
    DATA_TYPE_MAP = {
        "steps": "steps",
        "heart_rate": "heart-rate",
        "sleep": "sleep",
        "weight": "weight",
        "blood_glucose": "blood-glucose",
        "body_temperature": "core-body-temperature",
        "distance": "distance",
        "calories": "total-calories",
        "oxygen_saturation": "daily-oxygen-saturation",
        "body_fat_percentage": "body-fat",
        "height": "height",
        "heart_minutes": "active-zone-minutes",
        "move_minutes": "active-minutes",
    }

    # Google Health API v4 data category per data type (determines time field/filter pattern).
    _INTERVAL_TYPES = {"steps", "distance", "active-zone-minutes", "active-minutes"}
    _SAMPLE_TYPES = {"heart-rate", "weight", "blood-glucose", "core-body-temperature", "body-fat", "height"}
    _SESSION_TYPES = {"sleep"}
    _DAILY_TYPES = {"daily-oxygen-saturation", "total-calories"}

    def _filter_snake(self, api_type: str) -> str:
        """Filter expressions use snake_case data type names (e.g. heart-rate -> heart_rate)."""
        return api_type.replace("-", "_")

    def _build_filter(self, api_type: str, start_time, end_time) -> str | None:
        """Build an AIP-160 filter expression for the given data type and time range.

        Returns None when the type does not accept a server-side filter (verified
        against the live API 2026-08-14):
          - sleep (session type) rejects every data-type-member filter -> fetch
            unfiltered and filter client-side by interval.startTime.
        """
        snake = self._filter_snake(api_type)
        start_str = start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if api_type in self._SESSION_TYPES:
            return None  # session types reject time-member filters
        if api_type in self._INTERVAL_TYPES:
            field = f"{snake}.interval.start_time"
        elif api_type in self._SAMPLE_TYPES:
            field = f"{snake}.sample_time.physical_time"
        else:  # daily types are keyed by civil date (date only, not a timestamp)
            start_d = start_time.strftime("%Y-%m-%d")
            end_d = end_time.strftime("%Y-%m-%d")
            return f'{snake}.date >= "{start_d}" AND {snake}.date < "{end_d}"'
        return f'{field} >= "{start_str}" AND {field} < "{end_str}"'

    async def _get_valid_access_token(self, user_id: int, force_refresh: bool = False) -> str:
        """Return a decrypted access token, refreshing once when force_refresh is set."""
        if force_refresh:
            if not await self.refresh_access_token(user_id):
                raise ValueError("Access token expired and refresh failed - re-authorize Google Health")
        tokens = await self._get_user_tokens(user_id)
        if not tokens.get("access_token"):
            raise ValueError("No access token - authorize Google Health first")
        try:
            return encryption_service.decrypt(tokens["access_token"])
        except ValueError:
            raise ValueError("Failed to decrypt access token")

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        """Extract a readable message from a google.rpc Status error response."""
        try:
            error = response.json().get("error", {})
            message = error.get("message", "")
            status = error.get("status", "")
            return f"{status}: {message}" if status else (message or response.text[:200])
        except Exception:
            return response.text[:200]

    async def check_account_linked(self, user_id: int) -> bool | None:
        """Probe whether the user's Google account is linked to Google Health.

        Returns True/False when the link state is known, or None when it can't
        be determined (e.g. missing/invalid token — the caller treats that as
        "not connected" rather than "not linked").
        """
        try:
            access_token = await self._get_valid_access_token(user_id)
        except ValueError:
            return None

        url = f"{self.BASE_URL}/v4/users/me/dataTypes/steps/dataPoints"
        params = {"filter": 'steps.interval.start_time >= "2026-01-01T00:00:00Z"', "pageSize": 1}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)

        if response.status_code == 200:
            return True
        if NOT_LINKED_MARKER in response.text:
            return False
        return None  # some other error (e.g. 401) — link state unknown

    async def _request_with_retry(self, client: httpx.AsyncClient, method: str, url: str, headers: dict, **kwargs) -> httpx.Response:
        """Execute a request with exponential backoff on 429/5xx (per Google Health API guidance)."""
        max_attempts = 4
        for attempt in range(max_attempts):
            response = await client.request(method, url, headers=headers, **kwargs)
            if response.status_code not in (429, 500, 502, 503, 504):
                return response
            if attempt < max_attempts - 1:
                delay = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(f"Google Health API {response.status_code}, retrying in {delay}s: {url}")
                await asyncio.sleep(delay)
        return response

    async def fetch_health_data(self, user_id: int, data_type: str = "steps", days_back: int = 90, start_time=None, end_time=None) -> list[Dict[str, Any]]:
        """Fetch health data from the Google Health API (v4).

        Args:
            data_type: Internal type name (see DATA_TYPE_MAP), e.g. "steps", "sleep".
            days_back: Fallback window in days if start_time/end_time not provided
            start_time: Explicit start datetime (UTC). Overrides days_back.
            end_time: Explicit end datetime (UTC). Defaults to now.

        Returns:
            List of v4 DataPoint resources (payload under the type-specific union field).
        """
        api_type = self.DATA_TYPE_MAP.get(data_type, data_type)

        # Build time range
        from datetime import datetime, timedelta, timezone
        if end_time is None:
            end_time = datetime.now(timezone.utc)
        if start_time is None:
            start_time = end_time - timedelta(days=days_back)

        url = f"{self.BASE_URL}/v4/users/me/dataTypes/{api_type}/dataPoints"
        filter_expr = self._build_filter(api_type, start_time, end_time)

        refreshed = False
        page_token: Optional[str] = None
        points: list[Dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                access_token = await self._get_valid_access_token(user_id, force_refresh=refreshed)
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                }

                params: Dict[str, Any] = {}
                if filter_expr is not None:
                    params["filter"] = filter_expr
                if page_token:
                    params["pageToken"] = page_token

                response = await self._request_with_retry(client, "GET", url, headers, params=params)

                if response.status_code == 401 and not refreshed:
                    refreshed = True  # retry once with a refreshed token
                    continue

                if response.status_code != 200:
                    raise ValueError(f"Google Health API error ({response.status_code}): {self._error_message(response)}")

                data = response.json()
                points.extend(data.get("dataPoints", []))

                page_token = data.get("nextPageToken") or None
                if not page_token:
                    break

        # Session types are fetched unfiltered (the API rejects their time-member
        # filters), so trim to the requested window client-side by interval start.
        if api_type in self._SESSION_TYPES:
            points = [p for p in points if self._point_in_window(p, api_type, start_time, end_time)]

        return points

    @staticmethod
    def _point_in_window(point: Dict[str, Any], api_type: str, start_time, end_time) -> bool:
        """Return True if a session point's interval.startTime falls in [start, end).

        The session payload lives under the camelCase union field (e.g. "sleep"),
        not the first dict value (which may be dataSource/metadata).
        """
        union_field = api_type.replace("-", "_")
        # camelCase the snake_case (e.g. core_body_temperature -> coreBodyTemperature)
        head, *tail = union_field.split("_")
        camel = head + "".join(w.title() for w in tail)
        payload = point.get(camel) or point.get(union_field) or {}
        ts_str = payload.get("interval", {}).get("startTime")
        if not ts_str:
            return False
        try:
            start_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            return False
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        return start_time <= start_dt < end_time

    async def fetch_daily_rollup(self, user_id: int, data_type: str = "steps", days_back: int = 30, start_time=None, end_time=None) -> list[Dict[str, Any]]:
        """Fetch daily rollup aggregates (e.g. total steps per day) from the Google Health API (v4).

        The API caps a rollup request at 90 days (14 for calories/heart-rate/
        active-minutes), so larger windows are fetched in sequential chunks and
        concatenated.
        """
        api_type = self.DATA_TYPE_MAP.get(data_type, data_type)

        from datetime import datetime, timedelta, timezone
        if end_time is None:
            end_time = datetime.now(timezone.utc)
        if start_time is None:
            start_time = end_time - timedelta(days=days_back)

        url = f"{self.BASE_URL}/v4/users/me/dataTypes/{api_type}/dataPoints:dailyRollUp"
        # 14-day types vs 90-day types (per Google Health API limits).
        max_window_days = 14 if api_type in ("total-calories", "heart-rate", "active-minutes") else 90

        refreshed = False
        rollups: list[Dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Walk the full window forward in API-sized chunks.
            chunk_start = start_time
            while chunk_start < end_time:
                chunk_end = min(chunk_start + timedelta(days=max_window_days), end_time)
                body = {
                    "range": {
                        "start": {"date": {"year": chunk_start.year, "month": chunk_start.month, "day": chunk_start.day}},
                        "end": {"date": {"year": chunk_end.year, "month": chunk_end.month, "day": chunk_end.day}},
                    },
                    "windowSizeDays": 1,
                }

                page_token: Optional[str] = None
                while True:
                    access_token = await self._get_valid_access_token(user_id, force_refresh=refreshed)
                    headers = {
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    }

                    request_body = dict(body)
                    if page_token:
                        request_body["pageToken"] = page_token

                    response = await self._request_with_retry(client, "POST", url, headers, json=request_body)

                    if response.status_code == 401 and not refreshed:
                        refreshed = True
                        continue

                    if response.status_code != 200:
                        raise ValueError(f"Google Health API error ({response.status_code}): {self._error_message(response)}")

                    data = response.json()
                    rollups.extend(data.get("rollupDataPoints", []))

                    page_token = data.get("nextPageToken") or None
                    if not page_token:
                        break

                chunk_start = chunk_end

        return rollups
