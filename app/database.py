from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text
from sqlalchemy.pool import NullPool

from app.config import settings

# Use NullPool so connections are not kept open between requests. SQLite over a
# Docker bind mount (especially on Windows) is prone to "database is locked"
# errors when idle pooled connections hold file locks. A long busy timeout lets
# concurrent writers wait briefly instead of failing immediately.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool,
    connect_args={"timeout": 30},
)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    """Put SQLite in WAL mode with relaxed fsync on every new connection.

    Google sync writes tens of thousands of metric rows per backfill. Under the
    default rollback journal (journal_mode=delete) every COMMIT rewrites the
    whole journal and fsyncs with synchronous=FULL, which measured ~6.5 ms per
    row and made a 30-day backfill take longer than a sync run survives.

    WAL + synchronous=NORMAL keeps commits cheap and lets the API read while a
    sync writes. Durability is still crash-safe: WAL recovers to the last
    committed transaction, it just does not fsync on every commit (a power loss
    can lose the last few commits, which re-syncing from Google repairs).
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[type-var]
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add test_date column to pending_analyses if it doesn't exist (migration)
        try:
            await conn.execute(text("ALTER TABLE pending_analyses ADD COLUMN test_date DATETIME"))
        except Exception:
            pass  # Column already exists
        # Add source_document column to health_metrics if it doesn't exist (migration)
        try:
            await conn.execute(text("ALTER TABLE health_metrics ADD COLUMN source_document VARCHAR(255)"))
        except Exception:
            pass  # Column already exists
        # Add definition_id column to health_metrics (metric normalization)
        try:
            await conn.execute(text("ALTER TABLE health_metrics ADD COLUMN definition_id INTEGER REFERENCES metric_definitions(id)"))
        except Exception:
            pass  # Column already exists
        # Add reference_range column to health_metrics
        try:
            await conn.execute(text("ALTER TABLE health_metrics ADD COLUMN reference_range VARCHAR(200)"))
        except Exception:
            pass  # Column already exists
        # Add granularity column to health_metrics ("raw" granular point vs "daily" aggregate)
        try:
            await conn.execute(text("ALTER TABLE health_metrics ADD COLUMN granularity VARCHAR(20) NOT NULL DEFAULT 'raw'"))
        except Exception:
            pass  # Column already exists
        # ── One-time cleanup of legacy synced data ────────────────────────────
        # Older databases were missing the (user, metric, recorded_at, source)
        # idempotency constraint, so re-syncs inserted duplicate rows, and
        # rollup-cutoff changes left days with BOTH granular rows and a daily
        # aggregate (double counting). Collapse duplicates, tag daily rollups,
        # drop the superseded granular rows, then create the unique index.
        # All statements are idempotent, so they are safe to re-run at startup.
        #
        # A Google row at UTC midnight is classified as a daily rollup when:
        #   (a) it is the day's only row, OR
        #   (b) it dominates the day's granular rows (value >= 5x the largest
        #       same-day granular row), OR
        #   (c) it was created >= 1h AFTER the day's granular rows (a later
        #       rollup sync superseding earlier raw rows). The time gap avoids
        #       misclassifying granular midnight intervals written in the same
        #       sync batch (e.g. a "1 step at 00:00" interval row).
        cleanup_statements = [
            # Keep only the newest row per logical data point. This must run before
            # the unique index is created, since duplicates make that fail.
            text("""
                DELETE FROM health_metrics
                WHERE id NOT IN (
                    SELECT MAX(id) FROM health_metrics
                    GROUP BY user_id, metric_type, recorded_at, source
                )
            """),
            # Enforce sync idempotency going forward
            text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_health_metric_point
                ON health_metrics (user_id, metric_type, recorded_at, source)
            """),
            # Supports the "is there a daily row for this metric on this day?" probe
            # in the superseded-rows delete below, and the dashboard's per-day
            # aggregation now that health_metrics also holds raw heart-rate samples.
            # Created BEFORE that delete so the probe is an index lookup.
            text("""
                CREATE INDEX IF NOT EXISTS ix_health_metrics_day_lookup
                ON health_metrics (user_id, metric_type, granularity, recorded_at)
            """),
            # Serves the metric library's "unmatched" queue
            # (MetricNormalizer.get_unmatched_metrics), which filters on
            # definition_id IS NULL WITHOUT user_id -- so neither index above
            # applies. That function issues 1 GROUP BY plus, per unmatched
            # metric_type, a latest-row query (ORDER BY recorded_at DESC LIMIT 1)
            # and a distinct-documents query: 27 scans of the whole table for 13
            # types. Without a usable index every scan walks the heap, and through
            # a Docker bind mount that made the metric definitions page take ~29 s.
            # Measured on a 250k-row copy: 1.44s -> 0.10s (14.8x), with all three
            # patterns becoming index searches. recorded_at sits before
            # source_document so the latest-row query walks the index in order and
            # stops after one row.
            text("""
                CREATE INDEX IF NOT EXISTS ix_health_metrics_unmatched
                ON health_metrics (definition_id, metric_type, recorded_at, source_document)
            """),
            # Tag Google daily-rollup rows
            text("""
                UPDATE health_metrics SET granularity = 'daily'
                WHERE id IN (
                    SELECT h.id FROM health_metrics h
                    WHERE h.source = 'google_health_connect'
                      AND substr(h.recorded_at, 12, 8) = '00:00:00'
                      AND (
                        NOT EXISTS (
                            SELECT 1 FROM health_metrics h2
                            WHERE h2.user_id = h.user_id
                              AND h2.metric_type = h.metric_type
                              AND date(h2.recorded_at) = date(h.recorded_at)
                              AND h2.id <> h.id
                        )
                        OR (
                            (
                                lower(h.metric_type) LIKE '%steps%'
                                OR lower(h.metric_type) LIKE '%distance%'
                                OR lower(h.metric_type) LIKE '%calor%'
                                OR lower(h.metric_type) LIKE '%minutes%'
                                OR lower(h.metric_type) LIKE '%heart rate%'
                                OR lower(h.metric_type) LIKE '%weight%'
                                OR lower(h.metric_type) LIKE '%body fat%'
                            )
                            AND (
                                h.value >= 5.0 * (
                                    SELECT coalesce(MAX(h2.value), 0) FROM health_metrics h2
                                    WHERE h2.user_id = h.user_id
                                      AND h2.metric_type = h.metric_type
                                      AND date(h2.recorded_at) = date(h.recorded_at)
                                      AND h2.id <> h.id
                                      AND substr(h2.recorded_at, 12, 8) <> '00:00:00'
                                )
                                OR CAST(strftime('%s', h.created_at) AS INTEGER) >= 3600 + (
                                    SELECT coalesce(MAX(CAST(strftime('%s', h2.created_at) AS INTEGER)), 0)
                                    FROM health_metrics h2
                                    WHERE h2.user_id = h.user_id
                                      AND h2.metric_type = h.metric_type
                                      AND date(h2.recorded_at) = date(h.recorded_at)
                                      AND h2.id <> h.id
                                      AND substr(h2.recorded_at, 12, 8) <> '00:00:00'
                                )
                            )
                        )
                      )
                )
            """),
            # A daily aggregate supersedes that day's granular rows.
            #
            # This correlated probe is only fast because ix_health_metrics_day_lookup
            # (created above) includes `granularity`, narrowing the inner lookup to
            # the handful of daily rows for that metric. Without it SQLite used
            # uq_health_metric_point, searched on (user_id, metric_type) alone —
            # ~246k rows for heart_rate — and re-filtered by date for every
            # candidate row. That is O(n^2) and hung app startup for 15+ minutes
            # once raw heart-rate samples grew health_metrics past 270k rows.
            text("""
                DELETE FROM health_metrics
                WHERE granularity = 'raw'
                  AND source = 'google_health_connect'
                  AND EXISTS (
                    SELECT 1 FROM health_metrics d
                    WHERE d.user_id = health_metrics.user_id
                      AND d.metric_type = health_metrics.metric_type
                      AND d.granularity = 'daily'
                      AND date(d.recorded_at) = date(health_metrics.recorded_at)
                  )
            """),
        ]
        for stmt in cleanup_statements:
            try:
                await conn.execute(stmt)
            except Exception:
                pass  # Already cleaned up / index already exists
        # Add aliases column to metric_definitions
        try:
            await conn.execute(text("ALTER TABLE metric_definitions ADD COLUMN aliases TEXT"))
        except Exception:
            pass  # Column already exists
        # Add reference_ranges column to metric_definitions
        try:
            await conn.execute(text("ALTER TABLE metric_definitions ADD COLUMN reference_ranges TEXT"))
        except Exception:
            pass  # Column already exists
        # Add unit_conversions column to metric_definitions
        try:
            await conn.execute(text("ALTER TABLE metric_definitions ADD COLUMN unit_conversions TEXT"))
        except Exception:
            pass  # Column already exists
        # Add aggregation/cadence columns to metric_definitions: how the metric
        # collapses to a daily value (sum|avg|avg_minmax|latest) and which view
        # it belongs in (intraday|daily|event). See app/services/metric_registry.py.
        for _col in ("aggregation", "cadence"):
            try:
                await conn.execute(text(
                    f"ALTER TABLE metric_definitions ADD COLUMN {_col} VARCHAR(20)"))
            except Exception:
                pass  # Column already exists
        # Seed the new columns for rows that predate them (idempotent: NULLs only).
        try:
            from app.services.metric_registry import defaults_for

            _pending = (await conn.execute(text(
                "SELECT id, name FROM metric_definitions "
                "WHERE aggregation IS NULL OR cadence IS NULL"))).all()
            for _mid, _name in _pending:
                _agg, _cad = defaults_for(_name)
                await conn.execute(
                    text("UPDATE metric_definitions SET aggregation = :a, cadence = :c "
                         "WHERE id = :i"),
                    {"a": _agg, "c": _cad, "i": _mid},
                )
        except Exception:
            pass  # Never let seeding block startup
        # Create user_unit_preferences table (created by create_all if new, but ensure for existing DBs)
        try:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_unit_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    metric_definition_id INTEGER NOT NULL REFERENCES metric_definitions(id),
                    preferred_unit VARCHAR(50) NOT NULL,
                    created_at DATETIME NOT NULL,
                    CONSTRAINT uq_user_metric_pref UNIQUE (user_id, metric_definition_id)
                )
            """))
        except Exception:
            pass  # Table already exists
        # Add dashboard_metrics column to users
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN dashboard_metrics TEXT"))
        except Exception:
            pass  # Column already exists
