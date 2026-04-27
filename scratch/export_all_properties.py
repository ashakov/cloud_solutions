import sqlite3
import pandas as pd
import os

db_path = "phuket_invest.db"
output_path = "all_properties.csv"

if not os.path.exists(db_path):
    print(f"Error: {db_path} not found.")
else:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query("SELECT * FROM properties", conn)
    df.to_csv(output_path, index=False, encoding='utf-8-sig')
    conn.close()
    print(f"Success! Exported {len(df)} properties to {output_path}")
