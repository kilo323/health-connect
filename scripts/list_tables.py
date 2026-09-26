"""List the tables in a SQLite file."""
import sqlite3
import sys

p = sys.argv[1]
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
print("db:", p)
print("tables:", sorted(r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")))
con.close()
