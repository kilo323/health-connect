import httpx
from typing import Optional, Dict, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..models.user import User
from .encryption import encryption_service


class NextcloudService:
    def __init__(self):
        self.session = httpx.AsyncClient(timeout=30.0)

    async def get_base_url(self, user_id: int) -> str:
        """Get Nextcloud base URL for a user"""
        from ..models.health_data import SyncConfig
        db = None  # Would need to get DB session from caller
        try:
            result = await db.execute(
                select(SyncConfig).where(
                    SyncConfig.user_id == user_id,
                    SyncConfig.data_type == "nextcloud"
                )
            )
            config_row = result.scalar_one_or_none()
            if not config_row or not hasattr(config_row, 'encrypted_base_url'):
                raise ValueError("Nextcloud not configured for user")

            return encryption_service.decrypt(config_row.encrypted_base_url)
        except Exception as e:
            print(f"Error getting Nextcloud base URL: {e}")
            raise

    async def get_credentials(self, user_id: int) -> Dict[str, str]:
        """Get decrypted Nextcloud credentials"""
        from ..models.health_data import SyncConfig
        db = None  # Would need to get DB session from caller
        try:
            result = await db.execute(
                select(SyncConfig).where(
                    SyncConfig.user_id == user_id,
                    SyncConfig.data_type == "nextcloud"
                )
            )
            config_row = result.scalar_one_or_none()
            if not config_row or not hasattr(config_row, 'encrypted_username'):
                raise ValueError("Nextcloud credentials not configured")

            username = encryption_service.decrypt(config_row.encrypted_username)
            password = encryption_service.decrypt(config_row.encrypted_password)
            return {"username": username, "password": password}
        except Exception as e:
            print(f"Error getting Nextcloud credentials: {e}")
            raise

    async def ensure_folders(self, user_id: int):
        """Ensure required folder structure exists in Nextcloud"""
        base_url = await self.get_base_url(user_id)
        creds = await self.get_credentials(user_id)

        folders = [
            f"{base_url}/remote.php/dav/files/{creds['username']}/HealthTracker/Unprocessed",
            f"{base_url}/remote.php/dav/files/{creds['username']}/HealthTracker/Processed",
            f"{base_url}/remote.php/dav/files/{creds['username']}/HealthTracker/Archived"
        ]

        for folder in folders:
            try:
                await self.session.request(
                    "MKCOL",
                    folder,
                    auth=httpx.BasicAuth(creds["username"], creds["password"])
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 405:
                    # Folder already exists
                    pass
                else:
                    raise

    async def upload_file(self, user_id: int, filename: str, file_content: bytes, dest_path: str = "Unprocessed") -> str:
        """Upload a file to Nextcloud"""
        base_url = await self.get_base_url(user_id)
        creds = await self.get_credentials(user_id)

        url = f"{base_url}/remote.php/dav/files/{creds['username']}/HealthTracker/{dest_path}/{filename}"
        
        async with httpx.AsyncClient() as client:
            response = await client.put(
                url,
                content=file_content,
                headers={"Content-Type": "application/octet-stream"},
                auth=httpx.BasicAuth(creds["username"], creds["password"])
            )

        if response.status_code not in [201, 204]:
            raise ValueError(f"Failed to upload file: {response.text}")

        return url

    async def move_file(self, user_id: int, source_path: str, dest_path: str) -> bool:
        """Move a file within Nextcloud"""
        base_url = await self.get_base_url(user_id)
        creds = await self.get_credentials(user_id)

        # Extract the relative path from the full URL
        rel_source = source_path.replace(f"{base_url}/remote.php/dav/files/{creds['username']}/HealthTracker/", "")
        
        url = f"{base_url}/remote.php/dav/files/{creds['username']}/HealthTracker/{dest_path}"

        async with httpx.AsyncClient() as client:
            response = await client.request(
                "MOVE",
                source_path,
                headers={
                    "Destination": f"{base_url}/remote.php/dav/files/{creds['username']}/HealthTracker/{dest_path}",
                    "Overwrite": "T"
                },
                auth=httpx.BasicAuth(creds["username"], creds["password"])
            )

        return response.status_code in [201, 204]

    async def get_files(self, user_id: int, folder: str = "Unprocessed") -> List[Dict[str, str]]:
        """Get list of files in a Nextcloud folder"""
        base_url = await self.get_base_url(user_id)
        creds = await self.get_credentials(user_id)

        url = f"{base_url}/remote.php/dav/files/{creds['username']}/HealthTracker/{folder}"

        async with httpx.AsyncClient() as client:
            response = await client.request(
                "PROPFIND",
                url,
                headers={"Depth": "1"},
                auth=httpx.BasicAuth(creds["username"], creds["password"])
            )

        if response.status_code != 207:
            raise ValueError(f"Failed to get files list: {response.text}")

        # Parse WebDAV XML response (simplified)
        from xml.etree import ElementTree as ET
        namespaces = {'d': 'DAV:', 'ns': 'http://nextcloud.com'}
        
        root = ET.fromstring(response.content)
        files = []
        for elem in root.findall('.//d:response', namespaces):
            href = elem.find('d:href', namespaces)
            if href is not None and href.text:
                path = href.text.replace(f"{base_url}/remote.php/dav/files/{creds['username']}/HealthTracker/", "")
                files.append({"path": path, "url": href.text})

        return files


nextcloud_service = NextcloudService()
