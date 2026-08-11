#!/usr/bin/env python3
"""Refresh the metric library using an LLM.

Reads METRIC_LLM_URL, METRIC_LLM_API_TOKEN, METRIC_LLM_MODEL from .env
(or falls back to LLM_URL, LLM_API_TOKEN, LLM_MODEL).

Queries the database for:
  - Unmatched health metrics (definition_id IS NULL)
  - All existing metric names/aliases for context

Sends them to the LLM to generate definitions for unmatched metrics,
then merges the results into data/metric_library.json.

Usage:
    python refresh_metric_library.py
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import httpx

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LIBRARY_PATH = DATA_DIR / "metric_library.json"
DB_PATH = DATA_DIR / "health_tracker.db"


# ── .env loader (simple, no dependency) ────────────────────────────────────
def load_dotenv(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Does NOT override existing env vars."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        env[key] = value
    return env


def get_config() -> dict[str, str]:
    """Load LLM config from .env with METRIC_LLM_ prefix (fallback to LLM_)."""
    dotenv = load_dotenv(ROOT / ".env")

    def _get(metric_key: str, fallback_key: str) -> str:
        # Env var > .env METRIC_LLM_* > .env LLM_*
        return (
            os.environ.get(metric_key)
            or dotenv.get(metric_key)
            or dotenv.get(fallback_key)
            or ""
        )

    url = _get("METRIC_LLM_URL", "LLM_URL")
    token = _get("METRIC_LLM_API_TOKEN", "LLM_API_TOKEN")
    model = _get("METRIC_LLM_MODEL", "LLM_MODEL")

    if not url or not token or not model:
        print("ERROR: Missing LLM configuration.")
        print("Set these in .env (or as environment variables):")
        print("  METRIC_LLM_URL=https://your-provider.com/v1")
        print("  METRIC_LLM_API_TOKEN=your-token")
        print("  METRIC_LLM_MODEL=your-model")
        print()
        print("Or fall back to LLM_URL, LLM_API_TOKEN, LLM_MODEL.")
        sys.exit(1)

    return {"url": url.rstrip("/"), "token": token, "model": model}


# ── Database queries ───────────────────────────────────────────────────────
def get_db_connection() -> sqlite3.Connection:
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_unmatched_metrics(conn: sqlite3.Connection) -> list[dict]:
    """Get health_metrics that have no matching definition."""
    rows = conn.execute("""
        SELECT metric_type, unit, COUNT(*) as cnt
        FROM health_metrics
        WHERE definition_id IS NULL
        GROUP BY metric_type, unit
        ORDER BY cnt DESC, metric_type
    """).fetchall()

    return [
        {"metric_type": r["metric_type"], "unit": r["unit"] or "", "count": r["cnt"]}
        for r in rows
    ]


def get_all_metric_names(conn: sqlite3.Connection) -> list[dict]:
    """Get all metric types in the DB (for LLM context)."""
    rows = conn.execute("""
        SELECT metric_type, unit, COUNT(*) as cnt
        FROM health_metrics
        GROUP BY metric_type, unit
        ORDER BY metric_type
    """).fetchall()

    return [
        {"metric_type": r["metric_type"], "unit": r["unit"] or "", "count": r["cnt"]}
        for r in rows
    ]


def get_existing_definitions() -> list[dict]:
    """Load existing metric_library.json."""
    if not LIBRARY_PATH.exists():
        return []
    try:
        return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: Could not read existing library: {e}")
        return []


# ── LLM call ──────────────────────────────────────────────────────────────
def build_prompt(unmatched: list[dict], all_metrics: list[dict], existing: list[dict]) -> str:
    """Build the LLM prompt for metric definition generation."""

    existing_names = [d["name"] for d in existing]
    existing_aliases = set()
    for d in existing:
        existing_aliases.update(d.get("aliases", []))

    # Filter out metrics that already have definitions (by name or alias)
    truly_unmatched = []
    for m in unmatched:
        mt = m["metric_type"]
        if mt in existing_names or mt in existing_aliases:
            continue
        # Also skip if a case-insensitive match exists
        mt_lower = mt.lower()
        if any(mt_lower == n.lower() for n in existing_names):
            continue
        if any(mt_lower == a.lower() for a in existing_aliases):
            continue
        truly_unmatched.append(m)

    if not truly_unmatched:
        return ""

    unmatched_text = "\n".join(
        f'  - "{m["metric_type"]}" (unit: {m["unit"] or "unknown"}, {m["count"]} record(s))'
        for m in truly_unmatched
    )

    return f"""You are a clinical data specialist. I have health metrics extracted from medical documents that don't yet have definitions in my metric library.

Here are the unmatched metrics:
{unmatched_text}

For each unmatched metric, provide a definition in the following JSON format. Use standard medical knowledge for reference ranges and aliases. If you're unsure about a reference range, omit it (use empty array) rather than guessing.

Return ONLY a JSON array — no markdown, no explanation, no code fences.

Each entry should follow this schema:
[
  {{
    "name": "Canonical Name",
    "category": "Category (e.g. CBC, Metabolic, Lipids, Thyroid, Hormones, Liver Function, Kidney Function, Electrolytes, Urinalysis, Body Composition, Vitamins, Inflammation, Iron, Diabetes, Screening)",
    "unit": "canonical unit",
    "data_type": "float or string",
    "description": "Brief description",
    "aliases": ["alias1", "alias2"],
    "reference_ranges": [
      {{"sex": "male", "low": 0.0, "high": 1.0, "source": "common"}},
      {{"sex": "female", "low": 0.0, "high": 1.0, "source": "common"}},
      {{"sex": null, "low": 0.0, "high": 1.0, "source": "common"}}
    ],
    "unit_conversions": {{"alternate_unit": multiplier}}
  }}
]

IMPORTANT:
- sex should be "male", "female", or null (for both)
- Use the EXACT metric_type string from the unmatched list as one of the aliases
- Include common alternate spellings/names as aliases
- Use null for low/high when it's a one-sided range (e.g. "> 60" means low=60, high=null, operator=">=")
- For operator-based ranges, add "operator": "<" or "<=" or ">" or ">=" to the range object
- reference_ranges.source should be "common" for standard published ranges
- If you truly cannot determine a reference range, use an empty array
- Be precise with units (mg/dL, ng/mL, g/dL, etc.)
"""


def call_llm(config: dict[str, str], prompt: str) -> str:
    """Call the LLM and return the response content."""
    base_url = config["url"]
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['token']}",
    }

    print(f"Calling LLM: {config['model']} at {base_url}/chat/completions ...")

    # SSL verification: set LLM_SSL_VERIFY=false to skip, or a path to a CA bundle
    dotenv = load_dotenv(ROOT / ".env")
    ssl_verify = (
        os.environ.get("LLM_SSL_VERIFY")
        or dotenv.get("LLM_SSL_VERIFY", "true")
    ).strip()
    if ssl_verify.lower() in ("false", "0", "no"):
        verify = False
    elif ssl_verify.lower() in ("true", "1", "yes", ""):
        verify = True
    else:
        verify = ssl_verify  # treat as CA bundle path

    with httpx.Client(timeout=120.0, verify=verify) as client:
        response = client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": config["model"],
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a clinical data specialist. Return valid JSON only — no markdown, no code fences, no explanation.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": 16384,
            },
        )

    if response.status_code != 200:
        print(f"ERROR: LLM returned {response.status_code}: {response.text[:500]}")
        sys.exit(1)

    result = response.json()
    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        print("ERROR: LLM returned empty response")
        print(json.dumps(result, indent=2)[:1000])
        sys.exit(1)

    return content


def parse_llm_response(content: str) -> list[dict]:
    """Parse the LLM response into a list of metric definitions."""
    # Try direct parse
    try:
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "definitions" in parsed:
            return parsed["definitions"]
    except json.JSONDecodeError:
        pass

    # Try stripping markdown fences
    import re
    cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    print("ERROR: Could not parse LLM response as JSON. Raw output:")
    print(content[:2000])
    sys.exit(1)


# ── Merge & output ────────────────────────────────────────────────────────
def validate_definition(entry: dict) -> bool:
    """Basic validation of a metric definition."""
    if not entry.get("name") or not isinstance(entry["name"], str):
        return False
    if not isinstance(entry.get("aliases", []), list):
        return False
    if not isinstance(entry.get("reference_ranges", []), list):
        return False
    if not isinstance(entry.get("unit_conversions", {}), dict):
        return False
    return True


def merge_definitions(existing: list[dict], new_entries: list[dict]) -> tuple[list[dict], dict]:
    """Merge new definitions into existing library.

    Returns (merged_list, stats).
    """
    existing_by_name = {d["name"]: d for d in existing}
    existing_names_lower = {d["name"].lower(): d["name"] for d in existing}
    existing_aliases_lower: dict[str, str] = {}
    for d in existing:
        for alias in d.get("aliases", []):
            existing_aliases_lower[alias.lower()] = d["name"]

    created = 0
    updated = 0
    skipped = 0

    for entry in new_entries:
        if not validate_definition(entry):
            skipped += 1
            continue

        name = entry["name"]
        name_lower = name.lower()

        # Check if it matches an existing definition by name or alias
        existing_key = existing_names_lower.get(name_lower) or existing_aliases_lower.get(name_lower)

        if existing_key and existing_key in existing_by_name:
            # Merge: add new aliases, update ranges/conversions
            existing_def = existing_by_name[existing_key]
            existing_aliases = set(existing_def.get("aliases", []))
            new_aliases = set(entry.get("aliases", []))
            existing_def["aliases"] = list(existing_aliases | new_aliases)

            # Only overwrite ranges if existing has none and new has some
            if not existing_def.get("reference_ranges") and entry.get("reference_ranges"):
                existing_def["reference_ranges"] = entry["reference_ranges"]

            # Merge unit conversions
            existing_convs = existing_def.get("unit_conversions", {})
            existing_convs.update(entry.get("unit_conversions", {}))
            existing_def["unit_conversions"] = existing_convs

            # Update category/description if missing
            if not existing_def.get("category") and entry.get("category"):
                existing_def["category"] = entry["category"]
            if not existing_def.get("description") and entry.get("description"):
                existing_def["description"] = entry["description"]

            updated += 1
        else:
            # New definition
            existing.append(entry)
            existing_by_name[name] = entry
            existing_names_lower[name_lower] = name
            for alias in entry.get("aliases", []):
                existing_aliases_lower[alias.lower()] = name
            created += 1

    return existing, {"created": created, "updated": updated, "skipped": skipped}


def print_diff_summary(existing_before: list[dict], merged: list[dict], stats: dict):
    """Print a summary of what changed."""
    names_before = {d["name"] for d in existing_before}
    names_after = {d["name"] for d in merged}
    new_names = names_after - names_before

    print()
    print("=" * 60)
    print("METRIC LIBRARY REFRESH SUMMARY")
    print("=" * 60)
    print(f"  Definitions before:  {len(existing_before)}")
    print(f"  Definitions after:   {len(merged)}")
    print(f"  Created:             {stats['created']}")
    print(f"  Updated (merged):    {stats['updated']}")
    print(f"  Skipped (invalid):   {stats['skipped']}")
    print()

    if new_names:
        print("NEW definitions added:")
        for name in sorted(new_names):
            entry = next(d for d in merged if d["name"] == name)
            aliases = entry.get("aliases", [])
            alias_str = f"  (aliases: {', '.join(aliases[:5])})" if aliases else ""
            print(f"  + {name}{alias_str}")
        print()

    if stats["updated"] > 0:
        print(f"  ~ {stats['updated']} existing definition(s) had aliases merged")
        print()


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Refresh metric library using LLM")
    args = parser.parse_args()

    config = get_config()
    conn = get_db_connection()

    # Gather context
    unmatched = get_unmatched_metrics(conn)
    all_metrics = get_all_metric_names(conn)
    existing = get_existing_definitions()
    conn.close()

    print(f"Existing definitions: {len(existing)}")
    print(f"Unmatched metrics:    {len(unmatched)}")
    print()

    if not unmatched:
        print("No unmatched metrics — nothing to refresh.")
        print("All health metrics are already mapped to definitions.")
        return

    # Build and send prompt
    prompt = build_prompt(unmatched, all_metrics, existing)
    if not prompt:
        print("All unmatched metrics already have definitions (by name or alias).")
        print("Nothing to do.")
        return

    print("Sending unmatched metrics to LLM for definition generation...")
    content = call_llm(config, prompt)
    new_entries = parse_llm_response(content)

    print(f"LLM returned {len(new_entries)} definition(s)")

    # Merge
    existing_before = json.loads(json.dumps(existing))  # deep copy
    merged, stats = merge_definitions(existing, new_entries)

    # Sort by name for consistent output
    merged.sort(key=lambda d: d["name"].lower())

    # Summary
    print_diff_summary(existing_before, merged, stats)

    # Always write proposed file for review
    tmp_path = DATA_DIR / "metric_library_proposed.json"
    tmp_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Proposed library written to: {tmp_path}")
    print()

    # Ask user whether to apply
    answer = input("Apply these changes to metric_library.json? [y/N] ").strip().lower()
    if answer != "y":
        print("No changes applied. Proposed file saved for review.")
        return

    # Back up existing
    if LIBRARY_PATH.exists():
        backup_name = f"metric_library.{datetime.now().strftime('%Y%m%d_%H%M%S')}.json.bak"
        backup_path = DATA_DIR / backup_name
        backup_path.write_text(LIBRARY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Backed up existing library to: {backup_name}")

    LIBRARY_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Written {len(merged)} definitions to {LIBRARY_PATH}")
    print()
    print("Next steps:")
    print("  1. Review the changes in data/metric_library.json")
    print("  2. In the app, click 'Refresh from Library' to load the new definitions")
    print("  3. Commit to git when satisfied")


if __name__ == "__main__":
    main()
