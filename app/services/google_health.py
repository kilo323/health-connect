import httpx
import json
from typing import Optional, Dict, Any
from sqlalchemy import select
from ..database import async_session_factory
from ..models.settings import AppSettings
from .encryption import encryption_service


class GoogleHealthService:
    BASE_URL = "https://fitness.googleapis.com"

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
                db.add(AppSettings(key=key, value=value, description="Google Fit OAuth tokens"))
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

        async with httpx.AsyncClient() as client:
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

    async def fetch_health_data(self, user_id: int, data_type: str = "steps") -> list[Dict[str, Any]]:
        """Fetch health data from Google Fit API"""
        tokens = await self._get_user_tokens(user_id)
        if not tokens.get("access_token"):
            raise ValueError("No access token - authorize first")

        try:
            access_token = encryption_service.decrypt(tokens["access_token"])
        except ValueError:
            raise ValueError("Failed to decrypt access token")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # Map data types to Google Fit API dataset names
        dataset_map = {
            "steps": "com.google.step_count.delta",
            "heart_rate": "com.google.heart_rate.bpm",
            "sleep": "com.google.sleep.segment",
            "weight": "com.google.weight",
            "blood_pressure": "com.google.blood_pressure",
            "blood_glucose": "com.google.blood_glucose",
            "body_temperature": "com.google.body.temperature",
            "distance": "com.google.distance.delta",
            "calories": "com.google.calories.expended",
        }

        dataset = dataset_map.get(data_type, data_type)

        # Build time range (last 90 days)
        from datetime import datetime, timedelta, timezone
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=90)
        start_ns = int(start_time.timestamp() * 1e9)
        end_ns = int(end_time.timestamp() * 1e9)

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Try aggregated dataset first
            response = await client.get(
                f"{self.BASE_URL}/fitness/v1/users/me/dataSources/{dataset}/datasets/{start_ns}-{end_ns}",
                headers=headers,
            )

            if response.status_code == 401:
                if await self.refresh_access_token(user_id):
                    tokens = await self._get_user_tokens(user_id)
                    access_token = encryption_service.decrypt(tokens["access_token"])
                    headers["Authorization"] = f"Bearer {access_token}"
                    response = await client.get(
                        f"{self.BASE_URL}/fitness/v1/users/me/dataSources/{dataset}/datasets/{start_ns}-{end_ns}",
                        headers=headers,
                    )

            if response.status_code != 200:
                raise ValueError(f"Failed to fetch health data ({response.status_code}): {response.text[:200]}")

            data = response.json()
            points = data.get("point", [])

            # If no aggregated points, try raw data sources for this type
            if not points:
                all_sources_resp = await client.get(
                    f"{self.BASE_URL}/fitness/v1/users/me/dataSources",
                    headers=headers,
                )
                if all_sources_resp.status_code == 200:
                    raw_sources = [
                        s["dataStreamId"]
                        for s in all_sources_resp.json().get("dataSource", [])
                        if s["dataStreamId"].startswith("raw:") and data_type in s["dataStreamId"]
                    ]
                    for raw_source in raw_sources[:5]:
                        raw_resp = await client.get(
                            f"{self.BASE_URL}/fitness/v1/users/me/dataSources/{raw_source}/datasets/{start_ns}-{end_ns}",
                            headers=headers,
                        )
                        if raw_resp.status_code == 200:
                            raw_points = raw_resp.json().get("point", [])
                            points.extend(raw_points)

            return points
