"""Metric normalization service.

Matches LLM-extracted metric names to canonical MetricDefinitions using
exact name matching, alias matching, and case-insensitive fuzzy matching.
Handles unit conversion and reference range lookup.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.health_data import MetricDefinition, HealthMetric

logger = logging.getLogger(__name__)

# Threshold for fuzzy matching (0-1). 0.8 is fairly strict.
_FUZZY_THRESHOLD = 0.8
# Threshold for surfacing a "possible duplicate" suggestion to the admin.
_DUPLICATE_THRESHOLD = 0.75


@dataclass
class NormalizedMetric:
    """Result of normalizing a metric name."""
    definition: Optional[MetricDefinition]
    canonical_name: str
    normalized_unit: str
    match_type: str  # "exact", "alias", "fuzzy", "none"
    reference_range: Optional[str] = None  # from the document (LLM-extracted)


class MetricNormalizer:
    """Normalizes metric names against the MetricDefinitions table."""

    def __init__(self):
        # Populated by load()
        self._definitions: list[MetricDefinition] = []
        # Lookup maps
        self._by_name: dict[str, MetricDefinition] = {}  # lowercase name -> def
        self._by_alias: dict[str, MetricDefinition] = {}  # lowercase alias -> def
        self._loaded = False

    async def load(self, db: AsyncSession) -> None:
        """Load all MetricDefinitions and build lookup maps."""
        result = await db.execute(select(MetricDefinition))
        self._definitions = list(result.scalars().all())

        self._by_name.clear()
        self._by_alias.clear()

        for d in self._definitions:
            self._by_name[d.name.lower()] = d

            # Parse aliases from JSON
            aliases = self._parse_json_field(d.aliases)
            for alias in aliases:
                if isinstance(alias, str) and alias.strip():
                    self._by_alias[alias.strip().lower()] = d

        self._loaded = True
        logger.debug(
            f"MetricNormalizer loaded {len(self._definitions)} definitions "
            f"({len(self._by_name)} names, {len(self._by_alias)} aliases)"
        )

    async def normalize(
        self,
        db: AsyncSession,
        metric_name: str,
        unit: str = "",
        llm_reference_range: str = "",
    ) -> NormalizedMetric:
        """Normalize a metric name to its canonical definition.

        Args:
            db: Database session.
            metric_name: The metric name as extracted by the LLM.
            unit: The unit as extracted by the LLM.
            llm_reference_range: The reference range string from the LLM.

        Returns:
            NormalizedMetric with the matched definition (or None).
        """
        if not self._loaded:
            await self.load(db)

        name = metric_name.strip()
        if not name:
            return NormalizedMetric(
                definition=None, canonical_name=name, normalized_unit=unit,
                match_type="none", reference_range=llm_reference_range,
            )

        name_lower = name.lower()

        # 1. Exact name match
        if name_lower in self._by_name:
            d = self._by_name[name_lower]
            return self._build_result(d, name, unit, llm_reference_range, "exact")

        # 2. Alias match
        if name_lower in self._by_alias:
            d = self._by_alias[name_lower]
            return self._build_result(d, name, unit, llm_reference_range, "alias")

        # 3. Fuzzy match against names and aliases
        best_score = 0.0
        best_def = None

        all_keys: list[tuple[str, MetricDefinition]] = []
        all_keys.extend(self._by_name.items())
        all_keys.extend(self._by_alias.items())

        for key, d in all_keys:
            score = SequenceMatcher(None, name_lower, key).ratio()
            if score > best_score:
                best_score = score
                best_def = d

        if best_score >= _FUZZY_THRESHOLD and best_def is not None:
            logger.info(f"Fuzzy matched '{name}' -> '{best_def.name}' (score={best_score:.2f})")
            return self._build_result(best_def, name, unit, llm_reference_range, "fuzzy")

        # 4. No match
        return NormalizedMetric(
            definition=None, canonical_name=name, normalized_unit=unit,
            match_type="none", reference_range=llm_reference_range,
        )

    def get_canonical_names(self) -> list[str]:
        """Return all canonical metric names for LLM prompt injection."""
        if not self._loaded:
            return []
        return sorted(set(d.name for d in self._definitions))

    def find_similar_definition(self, metric_name: str) -> tuple[Optional[MetricDefinition], float]:
        """Find the most similar existing definition by name or alias.

        Returns the best-matching definition and its similarity score (0-1).
        """
        if not self._loaded or not metric_name:
            return None, 0.0

        query = metric_name.strip().lower()
        best_def: Optional[MetricDefinition] = None
        best_score = 0.0

        for d in self._definitions:
            candidates = [d.name.lower()]
            aliases = self._parse_json_field(d.aliases)
            if isinstance(aliases, list):
                candidates.extend(a.strip().lower() for a in aliases if isinstance(a, str))
            for candidate in candidates:
                score = SequenceMatcher(None, query, candidate).ratio()
                if score > best_score:
                    best_score = score
                    best_def = d

        return best_def, best_score

    async def get_unmatched_metrics(self, db: AsyncSession) -> list[dict]:
        """Find health_metrics that have no definition_id set.

        Returns a list of dicts with metric_type, count, latest value/unit,
        and the document names they came from.
        """
        if not self._loaded:
            await self.load(db)

        # Build a set of all known names + aliases for fast lookup
        known_lower: set[str] = set(self._by_name.keys()) | set(self._by_alias.keys())

        # Query all distinct metric_types from health_metrics
        from sqlalchemy import func as sa_func
        result = await db.execute(
            select(
                HealthMetric.metric_type,
                sa_func.count(HealthMetric.id).label("cnt"),
            )
            .where(HealthMetric.definition_id.is_(None))
            .group_by(HealthMetric.metric_type)
            .order_by(sa_func.count(HealthMetric.id).desc())
        )
        rows = result.all()

        unmatched = []
        for metric_type, cnt in rows:
            # Get latest value and unit
            latest_result = await db.execute(
                select(HealthMetric)
                .where(
                    HealthMetric.metric_type == metric_type,
                    HealthMetric.definition_id.is_(None),
                )
                .order_by(HealthMetric.recorded_at.desc())
                .limit(1)
            )
            latest = latest_result.scalar_one_or_none()

            # Get distinct source documents
            doc_result = await db.execute(
                select(HealthMetric.source_document)
                .where(
                    HealthMetric.metric_type == metric_type,
                    HealthMetric.definition_id.is_(None),
                    HealthMetric.source_document.isnot(None),
                )
                .distinct()
            )
            docs = [r[0] for r in doc_result.all() if r[0]]

            # Check if it already matches a known definition (name or alias)
            match_suggestion = None
            mt_lower = metric_type.lower()
            if mt_lower in known_lower:
                match_suggestion = (
                    self._by_name.get(mt_lower) or self._by_alias.get(mt_lower)
                )
                if match_suggestion:
                    match_suggestion = match_suggestion.name

            unmatched.append({
                "metric_type": metric_type,
                "count": cnt,
                "latest_value": str(latest.value) if latest else None,
                "latest_unit": latest.unit if latest else None,
                "documents": docs,
                "suggested_match": match_suggestion,
            })

        return unmatched

    async def retroactive_normalize(self, db: AsyncSession) -> int:
        """Normalize all health_metrics that have definition_id = NULL.

        Returns the number of metrics that were matched and updated.
        """
        if not self._loaded:
            await self.load(db)

        result = await db.execute(
            select(HealthMetric).where(HealthMetric.definition_id.is_(None))
        )
        metrics = result.scalars().all()
        updated = 0

        for m in metrics:
            norm = await self.normalize(db, m.metric_type, m.unit or "")
            if norm.definition is not None:
                m.definition_id = norm.definition.id
                m.metric_type = norm.canonical_name
                updated += 1

        if updated:
            await db.commit()
            logger.info(f"Retroactively normalized {updated} health metrics")

        return updated

    def _build_result(
        self,
        definition: MetricDefinition,
        original_name: str,
        unit: str,
        llm_reference_range: str,
        match_type: str,
    ) -> NormalizedMetric:
        """Build a NormalizedMetric from a matched definition."""
        normalized_unit = self._convert_unit(definition, unit)
        return NormalizedMetric(
            definition=definition,
            canonical_name=definition.name,
            normalized_unit=normalized_unit or unit,
            match_type=match_type,
            reference_range=llm_reference_range,
        )

    def _convert_unit(self, definition: MetricDefinition, unit: str) -> Optional[str]:
        """Convert a unit to the canonical unit if a conversion factor exists."""
        if not unit or not definition.unit:
            return None

        # Already in canonical unit
        if unit.strip().lower() == definition.unit.strip().lower():
            return definition.unit

        conversions = self._parse_json_field(definition.unit_conversions)
        if not conversions:
            return None

        # Check if the source unit has a conversion factor
        for known_unit, factor in conversions.items():
            if unit.strip().lower() == known_unit.strip().lower():
                return definition.unit  # Convertible — return canonical unit

        return None

    @staticmethod
    def _parse_json_field(value: Optional[str]) -> any:
        """Safely parse a JSON text field from the database."""
        if not value:
            return [] if value is None else value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return []
        return value


async def check_definition_duplicate(
    db: AsyncSession,
    name: str,
    aliases: list[str] | None = None,
    exclude_id: int | None = None,
) -> Optional[MetricDefinition]:
    """Return an existing definition that conflicts with the given name/aliases.

    Checks canonical name and aliases case-insensitively. Used to prevent
    duplicate metrics under different spellings.
    """
    if not name:
        return None

    aliases = aliases or []
    name_lower = name.strip().lower()
    alias_lowers = {a.strip().lower() for a in aliases if a.strip()}

    result = await db.execute(select(MetricDefinition))
    for d in result.scalars().all():
        if exclude_id is not None and d.id == exclude_id:
            continue
        if d.name.strip().lower() == name_lower:
            return d
        existing_aliases = check_definition_duplicate._parse_aliases(d.aliases)
        for existing_alias in existing_aliases:
            if existing_alias.strip().lower() == name_lower:
                return d
        for existing_alias in existing_aliases:
            if existing_alias.strip().lower() in alias_lowers:
                return d
    return None


check_definition_duplicate._parse_aliases = MetricNormalizer._parse_json_field


# Singleton instance
metric_normalizer = MetricNormalizer()
