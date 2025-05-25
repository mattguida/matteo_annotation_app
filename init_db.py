import psycopg2
import os

DATABASE_URL = os.environ.get("DATABASE_URL")  # Render injects this as an env variable

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS annotations (
    id SERIAL PRIMARY KEY,
    annotator_id TEXT,
    sentence TEXT,
    label TEXT,
    timestamp TEXT
);
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS annotators (
    annotator_id TEXT PRIMARY KEY,
    assigned_sentences TEXT[]
);
""")

conn.commit()
cur.close()
conn.close()
