"""Ad-hoc check: unit-system toggle + per-metric overrides over the real API.

Runs against a COPY of the database (see UNIT_TEST_DB) so nothing touches live data.
"""

import asyncio
import os
import shutil
import sys
import tempfile

from sqlalchemy import select

TMP = tempfile.mkdtemp(prefix="unit_test_")
DB_PATH = os.path.join(TMP, "health_tracker.db")
shutil.copy2(os.environ.get("UNIT_TEST_SOURCE_DB", "data/health_tracker.db"), DB_PATH)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH.replace(os.sep, '/')}"

sys.path.insert(0, os.getcwd())

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.database import async_session_factory, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.health_data import MetricDefinition  # noqa: E402
from app.models.user import User  # noqa: E402


async def main() -> None:
    await init_db()

    # Pick a user and mint a token for them.
    async with async_session_factory() as db:
        user = (await db.execute(select(User))).scalars().all()[0]
        username, user_id = user.username, user.id
        print(f"user: {username} (id={user_id}) unit_system={user.unit_system!r}")

    from jose import jwt

    from app.routers.users import SECRET_KEY

    token = jwt.encode({"sub": str(user_id)}, SECRET_KEY, algorithm="HS256")
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        async def snapshot(label: str) -> dict:
            res = await client.get("/api/users/me/units", headers=headers)
            assert res.status_code == 200, res.text
            data = res.json()
            interesting = {
                m["name"]: (m["canonical_unit"], m["preferred_unit"], m["system_unit"], m["effective_unit"])
                for m in data["metrics"]
                if m["system_unit"] is not None or m["preferred_unit"] is not None
            }
            print(f"\n--- {label}  (unit_system={data['unit_system']!r})")
            for name, (canon, pref, sysu, eff) in sorted(interesting.items()):
                print(f"    {name:22s} default={canon!r:10s} override={pref!r:8s} system={sysu!r:8s} -> {eff!r}")
            return data

        await snapshot("baseline (no system set)")

        res = await client.put("/api/users/me/unit-system", json={"unit_system": "imperial"}, headers=headers)
        assert res.status_code == 200, res.text
        await snapshot("after -> imperial")

        res = await client.put("/api/users/me/unit-system", json={"unit_system": "metric"}, headers=headers)
        assert res.status_code == 200, res.text
        await snapshot("after -> metric")

        # Pin Weight to kg, which must beat the metric system.
        async with async_session_factory() as db:
            weight = (
                await db.execute(select(MetricDefinition).where(MetricDefinition.name == "Weight"))
            ).scalar_one()
            weight_id = weight.id

        res = await client.put(
            "/api/users/me/unit-preferences",
            json={"metric_definition_id": weight_id, "preferred_unit": "kg"},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        await snapshot("Weight pinned to kg (override must win)")

        res = await client.delete(f"/api/users/me/unit-preferences/{weight_id}", headers=headers)
        assert res.status_code == 200, res.text
        await snapshot("override removed")

        res = await client.put("/api/users/me/unit-system", json={"unit_system": "nope"}, headers=headers)
        print(f"\ninvalid system -> {res.status_code} {res.json()}")
        assert res.status_code == 422

        res = await client.delete("/api/users/me/unit-preferences", headers=headers)
        print(f"clear all -> {res.status_code} {res.json()}")

        res = await client.put("/api/users/me/unit-system", json={"unit_system": None}, headers=headers)
        assert res.status_code == 200, res.text
        await snapshot("reverted to per-metric only")

    shutil.rmtree(TMP, ignore_errors=True)
    print("\nOK")


asyncio.run(main())
