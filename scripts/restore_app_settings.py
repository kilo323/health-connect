"""Restore app_settings from the app_settings.json backup and clean up stray data.

Run from repo root: .venv\\Scripts\\python.exe scripts/restore_app_settings.py
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.database import async_session_factory  # noqa: E402
from app.models.user import User  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
import asyncio  # noqa: E402

EXPORT_PATH = _ROOT / "app_settings.json"


def main():
    # Load the backup
    with open(EXPORT_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)

    rows = payload["rows"] if isinstance(payload, dict) and "rows" in payload else payload
    print(f"Backup contains {len(rows)} app_settings rows")

    admin_user = os.getenv("ADMIN_USER", "admin@domain.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "changeme")

    with TestClient(app) as client:
        # Login
        resp = client.post("/api/auth/login", json={"username": admin_user, "password": admin_password})
        assert resp.status_code == 200, f"login failed: {resp.text}"
        token = resp.json()["token"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Import — this upserts by key (updates existing, creates missing)
        with open(EXPORT_PATH, "rb") as f:
            resp = client.post(
                "/api/admin/settings/import",
                files={"file": ("app_settings.json", f, "application/json")},
                headers=headers,
            )
        result = resp.json()
        print(f"Import result: created={result['created']} updated={result['updated']} errors={result['errors']}")
        assert resp.status_code == 200, f"import failed: {resp.text}"

        # Verify all rows are back
        resp = client.get("/api/admin/settings/app_settings_export", headers=headers)
        data = resp.json()
        restored_keys = [r["key"] for r in data["rows"]]
        backup_keys = [r["key"] for r in rows]
        print(f"DB now has {len(restored_keys)} app_settings rows: {restored_keys}")
        missing = set(backup_keys) - set(restored_keys)
        assert not missing, f"Still missing keys: {missing}"

    # Clean up the stray admin@domain.com user created by earlier ad-hoc testing
    async def _cleanup_user():
        async with async_session_factory() as db:
            result = await db.execute(select(User).where(User.username == "admin@domain.com"))
            stray = result.scalar_one_or_none()
            if stray:
                await db.delete(stray)
                await db.commit()
                return True
            return False

    removed = asyncio.run(_cleanup_user())
    print(f"Stray admin@domain.com user removed: {removed}")

    print("\nRESTORE COMPLETE")


if __name__ == "__main__":
    main()
