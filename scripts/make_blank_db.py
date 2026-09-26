"""Create a blank database with the app's schema (for testing seed_metric_definitions)."""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
target = sys.argv[1]

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + target.replace("\\", "/")
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from app.database import Base, engine, init_db  # noqa: E402

# create_all() only knows about models that have been IMPORTED, so register them
# before creating the schema. (app.main does this implicitly.)
import app.models  # noqa: E402,F401


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await init_db()
    print("schema created at", target)


asyncio.run(main())
