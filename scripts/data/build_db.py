"""Build local SQLite product databases for the SuperSearch retrieval layer.

This script normalizes raw scraper catalog JSON files through brand adapters
and writes the compact `products` tables consumed by `db_query.py`. It is a
data-prep entry point, not part of the live request path.

Run from the repo root:
    python3 scripts/data/build_db.py
"""

import sqlite3
import json
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from brand_adapters import load_catalog
from config import BRAND_DB_PATHS, CATALOG_PATHS

CATALOGS = CATALOG_PATHS

DB_DIR = str(ROOT_DIR)

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    product_type TEXT,
    colors TEXT,
    patterns TEXT,
    materials TEXT,
    occasions TEXT,
    gender TEXT,
    price REAL,
    url TEXT,
    image_url TEXT,
    description TEXT
);

CREATE INDEX IF NOT EXISTS idx_product_type ON products(product_type);
CREATE INDEX IF NOT EXISTS idx_gender ON products(gender);
CREATE INDEX IF NOT EXISTS idx_price ON products(price);
"""


def build_db(brand, catalog_path, max_products=None):
    db_path = BRAND_DB_PATHS.get(brand, os.path.join(DB_DIR, f"{brand}_products.db"))

    if os.path.exists(db_path):
        os.remove(db_path)

    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    print(f"Loading {brand} catalog...")
    adapter = load_catalog(brand, catalog_path, max_products=max_products)
    products = adapter.products
    print(f"  {len(products)} products loaded")

    rows = []
    for p in products:
        rows.append((
            p.id,
            p.title,
            p.product_type,
            ",".join(p.colors) if p.colors else None,
            ",".join(p.patterns) if p.patterns else None,
            ",".join(p.materials) if p.materials else None,
            ",".join(p.occasions) if p.occasions else None,
            p.gender,
            p.price,
            p.url,
            p.image_url,
            p.description[:500] if p.description else None,
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        rows
    )
    conn.commit()

    # Print stats
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM products")
    total = cur.fetchone()[0]
    cur.execute("SELECT product_type, COUNT(*) FROM products GROUP BY product_type ORDER BY COUNT(*) DESC LIMIT 10")
    types = cur.fetchall()
    print(f"  DB: {db_path} ({total} rows)")
    print(f"  Top types: {types}")

    conn.close()
    return db_path


if __name__ == "__main__":
    for brand, path in CATALOGS.items():
        max_p = 50000 if brand == "aza" else None
        build_db(brand, path, max_products=max_p)
        print()
