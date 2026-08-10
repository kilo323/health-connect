"""Quick script to inspect metrics stored in the database."""
import sqlite3
import json

DB_PATH = "data/health_tracker.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# 1) Distinct metric types
print("=== HEALTH METRICS (distinct metric_type + unit) ===")
rows = conn.execute("""
    SELECT DISTINCT metric_type, unit, COUNT(*) as cnt
    FROM health_metrics
    GROUP BY metric_type, unit
    ORDER BY metric_type
""").fetchall()
for r in rows:
    print(f"  {r['metric_type']:45s} | unit: {(r['unit'] or '-'):15s} | count: {r['cnt']}")
print(f"\nTotal distinct metric types: {len(rows)}")
print(f"Total health metric records: {conn.execute('SELECT COUNT(*) FROM health_metrics').fetchone()[0]}")

# 2) Pending analyses — look at raw_analysis for reference ranges
print("\n=== PENDING ANALYSES — findings with reference_range ===")
analyses = conn.execute("""
    SELECT pa.id, d.filename, pa.raw_analysis
    FROM pending_analyses pa
    JOIN documents d ON d.id = pa.document_id
    ORDER BY pa.id
""").fetchall()

all_findings = []
for a in analyses:
    try:
        data = json.loads(a["raw_analysis"])
    except (json.JSONDecodeError, TypeError):
        continue
    findings = data.get("findings", [])
    print(f"\n  Document: {a['filename']}  ({len(findings)} findings)")
    for f in findings[:5]:
        name = f.get("metric_name", "?")
        val = f.get("value", "?")
        unit = f.get("unit", "")
        ref = f.get("reference_range", "")
        flag = f.get("notes", "")
        print(f"    {name:45s} | val: {str(val):15s} | unit: {(unit or '-'):12s} | ref_range: {(ref or 'N/A'):20s} | notes: {flag}")
        all_findings.append(f)
    if len(findings) > 5:
        print(f"    ... and {len(findings) - 5} more")

# 3) Summary: how many findings have reference ranges?
with_ref = [f for f in all_findings if f.get("reference_range")]
print(f"\n=== SUMMARY ===")
print(f"Total findings across all docs: {len(all_findings)}")
print(f"Findings WITH reference_range:  {len(with_ref)}")
print(f"Findings WITHOUT reference_range: {len(all_findings) - len(with_ref)}")

# 4) Show unique metric names to spot normalization issues
names = [f.get("metric_name", "") for f in all_findings]
unique_names = sorted(set(names))
print(f"\n=== UNIQUE METRIC NAMES ({len(unique_names)}) ===")
for n in unique_names:
    count = names.count(n)
    print(f"  {n:50s} (x{count})")

conn.close()
