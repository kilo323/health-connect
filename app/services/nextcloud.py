import httpx
import json
from typing import Dict, List
from urllib.parse import unquote
from ..database import async_session_factory
from ..models.settings import AppSettings


class NextcloudService:

    async def get_config(self, user_id: int) -> dict:
        async with async_session_factory() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(AppSettings).where(AppSettings.key == f"nextcloud_config_{user_id}")
            )
            row = result.scalar_one_or_none()
            if not row:
                return {}
            try:
                return json.loads(row.value)
            except (json.JSONDecodeError, TypeError):
                return {}

    async def get_credentials(self, user_id: int) -> dict:
        config = await self.get_config(user_id)
        server_url = config.get("server_url", "").rstrip("/")
        username = config.get("username", "")
        password = config.get("password", "")
        sync_path = config.get("sync_path", "/").rstrip("/")
        if not server_url or not username or not password:
            raise ValueError("Nextcloud credentials not configured")
        return {"server_url": server_url, "username": username, "password": password, "sync_path": sync_path}

    def _webdav_url(self, creds: dict, path: str) -> str:
        return f"{creds['server_url']}/remote.php/dav/files/{creds['username']}{path}"

    async def ensure_folders(self, user_id: int):
        creds = await self.get_credentials(user_id)
        sync_path = creds["sync_path"]
        folders = [f"{sync_path}/Unprocessed", f"{sync_path}/Processed", f"{sync_path}/Archived"]
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            for folder in folders:
                url = self._webdav_url(creds, folder)
                try:
                    await client.request("MKCOL", url, auth=(creds["username"], creds["password"]))
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 405:
                        raise

    async def list_files(self, user_id: int, subfolder: str = "Unprocessed") -> List[Dict]:
        """List files (not folders) in a Nextcloud subfolder"""
        creds = await self.get_credentials(user_id)
        url = self._webdav_url(creds, f"{creds['sync_path']}/{subfolder}")
        body = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<d:propfind xmlns:d='DAV:'>"
            "<d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/></d:prop>"
            "</d:propfind>"
        )
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.request(
                "PROPFIND", url, auth=(creds["username"], creds["password"]),
                headers={"Depth": "1"}, content=body,
            )
        if resp.status_code not in (200, 207):
            return []

        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        ns = {"d": "DAV:"}

        files = []
        for response in root.findall("d:response", ns):
            href = unquote(response.findtext("d:href", "", ns))
            propstat = response.find("d:propstat", ns)
            is_folder = False
            if propstat is not None:
                prop = propstat.find("d:prop", ns)
                if prop is not None:
                    rt = prop.find("d:resourcetype", ns)
                    if rt is not None:
                        is_folder = rt.find("d:collection", ns) is not None or len(rt) > 0

            if not is_folder:
                filename = href.rstrip("/").split("/")[-1]
                if filename:
                    # Ensure full URL (prepend server if path is relative)
                    file_url = href if href.startswith("http") else f"{creds['server_url']}{href}"
                    files.append({"filename": filename, "url": file_url})

        return files

    async def download_file(self, user_id: int, file_url: str) -> bytes:
        """Download a file from Nextcloud"""
        creds = await self.get_credentials(user_id)
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            resp = await client.get(file_url, auth=(creds["username"], creds["password"]))
        if resp.status_code != 200:
            raise ValueError(f"Failed to download file: {resp.status_code}")
        return resp.content

    async def move_file(self, user_id: int, source_url: str, dest_folder: str) -> bool:
        """Move a file to a different folder within Nextcloud"""
        creds = await self.get_credentials(user_id)
        filename = unquote(source_url).rstrip("/").split("/")[-1]
        dest_url = self._webdav_url(creds, f"{creds['sync_path']}/{dest_folder}/{filename}")
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.request(
                "MOVE", source_url, auth=(creds["username"], creds["password"]),
                headers={"Destination": dest_url, "Overwrite": "T"},
            )
        return resp.status_code in (200, 201, 204)

    async def upload_file(self, user_id: int, filename: str, file_content: bytes, dest_path: str = "Unprocessed") -> str:
        """Upload a file to Nextcloud"""
        creds = await self.get_credentials(user_id)
        url = self._webdav_url(creds, f"{creds['sync_path']}/{dest_path}/{filename}")
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            resp = await client.put(
                url, content=file_content, auth=(creds["username"], creds["password"]),
                headers={"Content-Type": "application/octet-stream"},
            )
        if resp.status_code in (200, 201, 204):
            return url
        raise Exception(f"Upload failed: {resp.status_code}")

    async def list_folder(self, user_id: int, path: str = "/") -> List[Dict]:
        """List all items (files and folders) in a path"""
        creds = await self.get_credentials(user_id)
        url = self._webdav_url(creds, path)
        body = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<d:propfind xmlns:d='DAV:'>"
            "<d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/></d:prop>"
            "</d:propfind>"
        )
        async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
            resp = await client.request(
                "PROPFIND", url, auth=(creds["username"], creds["password"]),
                headers={"Depth": "1"}, content=body,
            )
        if resp.status_code not in (200, 207):
            return []

        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        ns = {"d": "DAV:"}
        items = []
        for response in root.findall("d:response", ns):
            href = unquote(response.findtext("d:href", "", ns))
            propstat = response.find("d:propstat", ns)
            is_folder = False
            if propstat is not None:
                prop = propstat.find("d:prop", ns)
                if prop is not None:
                    rt = prop.find("d:resourcetype", ns)
                    if rt is not None:
                        is_folder = rt.find("d:collection", ns) is not None or len(rt) > 0
            item_path = href.rstrip("/").replace(f"/remote.php/dav/files/{creds['username']}", "") or "/"
            items.append({"path": item_path, "name": item_path.split("/")[-1] or item_path, "is_folder": is_folder})
        return items


nextcloud_service = NextcloudService()
