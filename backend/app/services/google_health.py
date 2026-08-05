import httpx
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..models.user import User
from .encryption import encryption_service


class GoogleHealthService:
    BASE_URL = "https://healthconnect.google.com"

    def __init__(self):
        self.session = httpx.AsyncClient()

    async def get_oauth_url(self, user_id: int) -> str:
        """Get OAuth URL for the user to authorize Google Health Connect"""
        # In production, this would use actual Google OAuth2 flow
        # For now, return a placeholder that shows how it works
        config = await self._get_user_config(user_id)
        if not config.get("client_id"):
            raise ValueError("Google Health Connect not configured for user")

        params = {
            "client_id": config["client_id"],
            "redirect_uri": config.get("redirect_uri", f"{self.BASE_URL}/oauth/callback"),
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/healthconnect.read_only",
            "access_type": "offline",
            "prompt": "consent"
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{'&'.join(f'{k}={v}' for k, v in params.items())}"

    async def exchange_code(self, user_id: int, code: str) -> Dict[str, Any]:
        """Exchange OAuth code for tokens"""
        config = await self._get_user_config(user_id)
        if not config.get("client_id") or not config.get("client_secret"):
            raise ValueError("Google Health Connect credentials not configured")

        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config["redirect_uri"],
            "client_id": config["client_id"],
            "client_secret": config["client_secret"]
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/oauth2/v4/token",
                data=token_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()
            tokens = response.json()

        # Encrypt and store tokens
        encrypted_tokens = {
            "access_token": encryption_service.encrypt(tokens["access_token"]),
            "refresh_token": encryption_service.encrypt(tokens.get("refresh_token", "")),
            "expires_at": str(tokens.get("expires_in", 0))
        }

        await self._save_user_config(user_id, encrypted_tokens)
        return {"status": "success", "message": "Google Health Connect authorized"}

    async def refresh_access_token(self, user_id: int) -> bool:
        """Refresh the access token using refresh token"""
        config = await self._get_user_config(user_id)
        if not config.get("refresh_token"):
            return False

        # Decrypt refresh token
        from .encryption import encryption_service as enc_svc
        try:
            refresh_token = enc_svc.decrypt(config["refresh_token"])
        except ValueError:
            return False

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/oauth2/v4/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                    "refresh_token": refresh_token
                }
            )

        if response.status_code == 200:
            new_tokens = response.json()
            encrypted_new_tokens = {
                "access_token": encryption_service.encrypt(new_tokens["access_token"]),
                "refresh_token": encryption_service.encrypt(new_tokens.get("refresh_token", refresh_token)),
                "expires_at": str(new_tokens.get("expires_in", 0))
            }
            await self._save_user_config(user_id, encrypted_new_tokens)
            return True

        return False

    async def fetch_health_data(self, user_id: int, data_type: str = "steps") -> list[Dict[str, Any]]:
        """Fetch health data from Google Health Connect API"""
        config = await self._get_user_config(user_id)
        if not config.get("access_token"):
            raise ValueError("No access token - authorize first")

        # Decrypt access token
        try:
            access_token = encryption_service.decrypt(config["access_token"])
        except ValueError:
            raise ValueError("Failed to decrypt access token")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        # Fetch data based on type (steps, sleep, heart_rate, etc.)
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/v1/{data_type}",
                headers=headers,
                params={"limit": 100}
            )

        if response.status_code == 401:
            # Try to refresh token and retry once
            if await self.refresh_access_token(user_id):
                config = await self._get_user_config(user_id)
                access_token = encryption_service.decrypt(config["access_token"])
                headers["Authorization"] = f"Bearer {access_token}"
                response = await client.get(
                    f"{self.BASE_URL}/v1/{data_type}",
                    headers=headers,
                    params={"limit": 100}
                )

        if response.status_code != 200:
            raise ValueError(f"Failed to fetch health data: {response.text}")

        return response.json()

    async def _get_user_config(self, user_id: int) -> dict:
        """Get Google Health Connect config for a user"""
        from ..models.health_data import SyncConfig
        db = None  # Would need to get DB session from caller
        try:
            result = await db.execute(
                select(SyncConfig).where(
                    SyncConfig.user_id == user_id,
                    SyncConfig.data_type == "google_health_connect"
                )
            )
            config_row = result.scalar_one_or_none()
            if not config_row:
                return {}

            # Decrypt stored values
            encrypted_config = {}
            for key in ["client_id", "client_secret", "redirect_uri"]:
                if hasattr(config_row, f"encrypted_{key}"):
                    try:
                        encrypted_config[key] = encryption_service.decrypt(getattr(config_row, f"encrypted_{key}"))
                    except ValueError:
                        pass

            return {**config_row.__dict__, **encrypted_config}
        except Exception as e:
            # Log error and return empty config
            print(f"Error getting user config: {e}")
            return {}

    async def _save_user_config(self, user_id: int, encrypted_data: dict):
        """Save encrypted tokens to database"""
        from ..models.health_data import SyncConfig
        db = None  # Would need to get DB session from caller
        try:
            result = await db.execute(
                select(SyncConfig).where(
                    SyncConfig.user_id == user_id,
                    SyncConfig.data_type == "google_health_connect"
                )
            )
            config_row = result.scalar_one_or_none()

            if config_row:
                # Update existing record
                for key in ["access_token", "refresh_token"]:
                    setattr(config_row, f"encrypted_{key}", encrypted_data.get(key))
                await db.commit()
            else:
                # Create new record with encrypted data
                new_config = SyncConfig(
                    user_id=user_id,
                    data_type="google_health_connect",
                    is_enabled=True
                )
                for key in ["access_token", "refresh_token"]:
                    setattr(new_config, f"encrypted_{key}", encrypted_data.get(key))
                db.add(new_config)
                await db.commit()
        except Exception as e:
            print(f"Error saving user config: {e}")
