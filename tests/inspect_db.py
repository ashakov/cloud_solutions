"""Show all tables, column schemas, and first 2 rows from each."""
import sqlite3, sys, textwrap
sys.stdout.reconfigure(encoding="utf-8")

con = sqlite3.connect("phuket_invest.db")
cur = con.cursor()

# ── Table list + row counts ───────────────────────────────────────────────────
tables = cur.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()

print("=" * 60)
print(" ALL TABLES")
print("=" * 60)
for (t,) in tables:
    count = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    print(f"  {t:<38} {count:>6} rows")

# ── Per-table schema + sample rows ───────────────────────────────────────────
for (t,) in tables:
    cols_meta = cur.execute(f'PRAGMA table_info("{t}")').fetchall()
    cols      = [c[1] for c in cols_meta]
    col_types = [f"{c[1]}:{c[2]}" for c in cols_meta]
    rows      = cur.execute(f'SELECT * FROM "{t}" LIMIT 2').fetchall()

    row_count = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    print()
    print("=" * 60)
    print(f" TABLE: {t}  ({len(cols)} columns, {row_count} rows)")
    print("=" * 60)

    # Column list (wrapped)
    wrapped = textwrap.fill(", ".join(col_types), width=110,
                            initial_indent="  Cols: ",
                            subsequent_indent="        ")
    print(wrapped)

    if not rows:
        print("  (empty)")
        continue

    # Sample rows — print each field on its own line for readability
    for ri, row in enumerate(rows, 1):
        print(f"\n  --- Row {ri} ---")
        for col, val in zip(cols, row):
            if val is None:
                continue
            sval = str(val)
            if len(sval) > 120:
                sval = sval[:117] + "..."
            print(f"    {col:<35} {sval}")

con.close()
