"""End-to-end test for the admin app_settings import endpoint.

Run from repo root: .venv\\Scripts\\python.exe scripts/test_import_app_settings.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# Load .env before importing the app so settings/JWT_SECRET_KEY are available
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def main():
    with TestClient(app) as client:
        # 1) Log in as the seeded admin to get a bearer token
        admin_user = os.getenv("ADMIN_USER", "admin@domain.com")
        admin_password = os.getenv("ADMIN_PASSWORD", "changeme")
        resp = client.post(
            "/api/auth/login",
            json={"username": admin_user, "password": admin_password},
        )
        assert resp.status_code == 200, f"login failed: {resp.text}"
        token = resp.json()["token"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2) Snapshot current keys before import
        resp = client.get("/api/admin/settings/llm", headers=headers)
        assert resp.status_code == 200
        before = resp.json()

        # 3) Craft a small synthetic app_settings.json with one existing key
        # (llm_config) and one brand-new key to verify both update and create paths.
        sync_payload = {
            "key": "llm_config",
            "value": json.dumps(
                {"base_url": "https://example.invalid/v1", "api_key": "test-key", "model": "test-model"}
            ),
            "description": "LLM configuration (test import)",
        }
        new_payload = {
            "key": "test_imported_setting",
            "value": json.dumps({"foo": "bar"}),
            "description": "A brand new setting created via import",
        }
        export = {
            "exported_at": "2026-09-25T00:00:00Z",
            "table": "app_settings",
            "row_count": 2,
            "rows": [sync_payload, new_payload],
        }

        # Write to a temp file so UploadFile can read it
        tmp_path = _ROOT / "data" / "_tmp_import_test.json"
        tmp_path.write_text(json.dumps(export), encoding="utf-8")

        try:
            resp = client.post(
                "/api/admin/settings/import",
                files={"file": ("app_settings.json", tmp_path.open("rb"), "application/json")},
                headers=headers,
            )
            assert resp.status_code == 200, f"import failed: {resp.text}"
            result = resp.json()
            print("Import result:", json.dumps(result, indent=2))
            assert result["created"] == 1, f"expected 1 created, got {result['created']}"
            assert result["updated"] == 1, f"expected 1 updated, got {result['updated']}"
            assert result["imported"] == 2
            assert result["errors"] == []

            # 4) Verify the existing key was overwritten
            resp = client.get("/api/admin/settings/llm", headers=headers)
            after = resp.json()
            assert after["base_url"] == "https://example.invalid/v1", "existing value not overwritten"
            assert after["api_key"] == "test-key"
            assert after["model"] == "test-model"
            print("Confirmed existing key (llm_config) was overwritten")

            # 5) Verify the new key was created by querying it back via direct DB
            # (there's no dedicated GET for arbitrary keys, so use sqlite3)
            import sqlite3

            db_path = _ROOT / "data" / "health_tracker.db"
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT value, description FROM app_settings WHERE key = 'test_imported_setting'"
            ).fetchone()
            conn.close()

            assert row is not None, "new setting was not created in DB"
            val = json.loads(row["value"])
            assert val == {"foo": "bar"}, f"unexpected value: {val}"
            assert row["description"] == "A brand new setting created via import"
            print("Confirmed new key (test_imported_setting) was created in DB")

            print("\nALL ASSERTIONS PASSED")
        finally:
            tmp_path.unlink(missing_ok=True)

        # 6) Cleanup: restore the original llm_config so we don't poison the live DB
        restore = {
            "key": "llm_config",
            "value": json.dumps(before),
            "description": "LLM configuration",
        }
        tmp_path.write_text(json.dumps({"rows": [restore]}), encoding="utf-8")
        resp = client.post(
            "/api/admin/settings/import",
            files={"file": ("app_settings.json", tmp_path.open("rb"), "application/json")},
            headers=headers,
        )
        assert resp.status_code == 200
        tmp_path.unlink(missing_ok=True)

        # Delete the test-only key
        from app.database import async_session_factory
        from app.models.settings import AppSettings
        from sqlalchemy import select, delete

        async def _cleanup():
            async with async_session_factory() as db:
                await db.execute(delete(AppSettings).where(AppSettings.key == "test_imported_setting"))
                await db.commit()

        asyncio.run(_cleanup())
        print("Cleanup done: restored llm_config, removed test-imported setting")


if __name__ == "__main__":
    main()
