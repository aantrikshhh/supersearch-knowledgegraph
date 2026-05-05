"""SQL retrieval layer for SuperSearch product recommendations.

The workflows hand this module a user query, extracted intents, and formatted
knowledge-graph context. It turns that semantic context into SQL, runs the
brand-specific SQLite product database, and falls back to deterministic SQL
when the LLM path fails or returns no candidates.
"""

import sqlite3
import json
import os
import re
import time
from llm_client import call_llm
from config import SQL_LLM_TIMEOUT

DB_DIR = os.path.dirname(__file__)

DB_PATHS = {
    "masaba": os.path.join(DB_DIR, "masaba_products.db"),
    "kalki": os.path.join(DB_DIR, "kalki_products.db"),
    "aza": os.path.join(DB_DIR, "aza_products.db"),
}

SQL_GENERATION_SYSTEM = """You are a SQL query generator for a fashion product database.

Given a user's fashion query, their extracted intents, and knowledge graph context about what clothing attributes are appropriate, generate a SQLite SELECT query to find matching products.

## Database Schema
```sql
CREATE TABLE products (
    id TEXT PRIMARY KEY,
    title TEXT,            -- full product name, often includes color and material info
    product_type TEXT,     -- e.g. 'saree', 'kurta', 'dress', 'lehenga', 'coord', 'kaftan', 'jacket'
    colors TEXT,           -- comma-separated, e.g. 'red,gold,pink' (may be NULL)
    patterns TEXT,         -- comma-separated, e.g. 'embroidered,floral,sequin' (may be NULL)
    materials TEXT,        -- comma-separated, e.g. 'silk,georgette' (may be NULL)
    occasions TEXT,        -- comma-separated (OFTEN NULL — do NOT filter by this)
    gender TEXT,           -- 'male', 'female', or 'both'
    price REAL,
    url TEXT,
    image_url TEXT,
    description TEXT
);
```

## Schema Linking — how user terms map to columns
- "color" / "red" / "blue" → colors column (comma-separated, use ',' || colors || ',' LIKE '%,red,%' OR title LIKE '%red%')
- "type" / "dress" / "kurta" → product_type column (exact match)
- "for women" / "men's" → gender column ('male', 'female', 'both')
- "silk" / "cotton" → materials column (comma-separated, use LIKE)
- "embroidered" / "floral" → patterns column (comma-separated, use LIKE)
- "under 5000" / "luxury" → price column
- "elegant" / "festive" → search in title column with LIKE

## CRITICAL Rules
1. product_type is mandatory in WHERE — use only exact values from "Available product_type values"
2. If intents include product_type, it is a hard user constraint; resolve it to exact available DB values and keep it first in WHERE
3. If the user did not request a product_type, use KG recommended + acceptable product types after resolving them to exact available DB values
4. Color, pattern, material, style, and KG recommended attributes normally belong in ORDER BY, not WHERE, because these columns are often NULL
5. Use CASE statements in ORDER BY to prioritize product type, then explicit user preferences, then KG recommended colors/patterns/materials, then price
6. Always add LIMIT 20
7. Return ONLY the raw SQL — no markdown, no backticks, no explanation
8. Must be valid SQLite
9. When KG says "all" for colours, skip color ranking
10. Apply gender filter only when intents.gender is exactly "female" or "male"; do not infer or default gender
11. Also search the title column with LIKE for color/pattern matching as fallback
12. Never filter on occasions column — it is almost always NULL
13. Never invent religion/culture, brand defaults, product types, colors, or gender not present in intents/KG context
14. If intents include "price_max", add WHERE price <= N as a HARD constraint (not just ORDER BY)
15. If intents include "price_min", add WHERE price >= N as a HARD constraint
16. Do not use comments, CTEs, multiple statements, INSERT, UPDATE, DELETE, DROP, ALTER, or PRAGMA

## COLOR ENFORCEMENT (important for cultural contexts)
When the KG "Avoid colours" list includes specific colors, ADD a WHERE clause to EXCLUDE them:
  AND NOT (',' || COALESCE(colors,'') || ',' LIKE '%,white,%' OR ',' || COALESCE(colors,'') || ',' LIKE '%,ivory,%')
When the KG "Recommended colours" has specific colors (not "all"), BOOST them in ORDER BY. Do not filter to recommended colors in WHERE unless the user explicitly requested a color or the cultural notes say a color is mandatory.

## PRICE / BUDGET HANDLING
- If user says "luxury", "premium", "expensive", "money is no object" → add ORDER BY price DESC
- If user says "budget", "cheap", "affordable", "economical" → add ORDER BY price ASC
- If user mentions a specific price ("under 5000") → add WHERE price <= 5000

## HARD CONSTRAINTS VS RANKING
- Hard exclusions from the KG "Avoid" list can be used in WHERE.
- Explicit user filters are hard constraints; KG recommendations are ranking guidance.
- Recommended colors, patterns, materials, and style goals should normally be ORDER BY ranking signals.
- Festival-specific colors and products come from the Knowledge Graph context; do not invent extra festival rules.
- Role-specific constraints in Cultural/Regional Notes can be hard exclusions, e.g. wedding guest avoiding bridal red.

## Few-Shot Examples

### Example 1: Place + Weather query
Intents: {"place": "beach", "weather": "summer"}
KG Context: Recommended products: shorts, top, bikini, swimsuit. Recommended colours: all. Recommended materials: cotton, linen.
```
SELECT * FROM products
WHERE product_type IN ('shorts', 'top', 'bikini', 'swimsuit', 'dress', 'skirt', 'coord')
ORDER BY
  CASE WHEN product_type IN ('shorts', 'top') THEN 0 ELSE 1 END,
  CASE WHEN ',' || materials || ',' LIKE '%,cotton,%' THEN 0 WHEN ',' || materials || ',' LIKE '%,linen,%' THEN 1 ELSE 2 END
LIMIT 20
```

### Example 2: Occasion + Religion + Gender query
Intents: {"occasion": "hindu wedding", "religion": "Hinduism", "gender": "female"}
KG Context: Recommended products: saree, lehenga, salwar, kurta. Recommended colours: red, yellow. Avoid: shorts, bikini. Recommended patterns: ethnic, embellished.
```
SELECT * FROM products
WHERE product_type IN ('saree', 'lehenga', 'salwar', 'kurta', 'coord', 'dress')
AND gender IN ('female', 'both')
ORDER BY
  CASE WHEN product_type IN ('saree', 'lehenga', 'salwar', 'kurta') THEN 0 ELSE 1 END,
  CASE WHEN ',' || colors || ',' LIKE '%,red,%' OR title LIKE '%red%' THEN 0 WHEN ',' || colors || ',' LIKE '%,yellow,%' OR title LIKE '%yellow%' THEN 1 ELSE 2 END,
  CASE WHEN ',' || patterns || ',' LIKE '%,embroidered,%' OR title LIKE '%embroidered%' THEN 0 ELSE 1 END
LIMIT 20
```

### Example 3: Body type + Occasion query
Intents: {"bodytype": "petite", "occasion": "bachelorette"}
KG Context: Recommended products: dress, coord, skirt, top. Recommended fit: fitted, mini. Avoid: saree, salwar.
```
SELECT * FROM products
WHERE product_type IN ('dress', 'coord', 'skirt', 'top', 'kurta', 'lehenga')
ORDER BY
  CASE WHEN product_type IN ('dress', 'coord', 'skirt', 'top') THEN 0 ELSE 1 END,
  CASE WHEN title LIKE '%mini%' OR title LIKE '%short%' THEN 0 ELSE 1 END
LIMIT 20
```

### Example 4: Activity query
Intents: {"activity": "yoga", "health": "back pain"}
KG Context: Recommended products: legging, top, pant. Recommended materials: cotton, lycra. Recommended fit: loose, comfortable.
```
SELECT * FROM products
WHERE product_type IN ('legging', 'top', 'pant', 'coord', 'dress')
ORDER BY
  CASE WHEN product_type IN ('legging', 'top', 'pant') THEN 0 ELSE 1 END,
  CASE WHEN ',' || materials || ',' LIKE '%,cotton,%' OR ',' || materials || ',' LIKE '%,lycra,%' THEN 0 ELSE 1 END
LIMIT 20
```

### Example 5: Event + Color preference
Intents: {"event": "Diwali"}
KG Context: Recommended products: saree, salwar, kurta, dhoti. Recommended colours: red, yellow, orange, gold. Recommended patterns: ethnic, embellished.
```
SELECT * FROM products
WHERE product_type IN ('saree', 'salwar', 'kurta', 'coord', 'dress', 'lehenga')
ORDER BY
  CASE WHEN product_type IN ('saree', 'salwar', 'kurta') THEN 0 ELSE 1 END,
  CASE WHEN ',' || colors || ',' LIKE '%,red,%' OR title LIKE '%red%' THEN 0
       WHEN ',' || colors || ',' LIKE '%,yellow,%' OR title LIKE '%yellow%' THEN 0
       WHEN ',' || colors || ',' LIKE '%,gold,%' OR title LIKE '%gold%' THEN 1
       ELSE 2 END,
  CASE WHEN ',' || patterns || ',' LIKE '%,embroidered,%' OR title LIKE '%embroidered%' OR title LIKE '%embellished%' THEN 0 ELSE 1 END
LIMIT 20
```

### Example 6: Luxury / premium budget — sort by price DESC
Intents: {"occasion": "anniversary", "budget": "luxury"}
KG Context: Recommended products: saree, dress, lehenga. Recommended colours: all. Recommended patterns: embellished.
```
SELECT * FROM products
WHERE product_type IN ('saree', 'dress', 'lehenga', 'coord', 'kurta')
ORDER BY
  CASE WHEN product_type IN ('saree', 'dress', 'lehenga') THEN 0 ELSE 1 END,
  price DESC
LIMIT 20
```

### Example 7: Cultural color exclusion — Sikh wedding (no white/black)
Intents: {"occasion": "sikh wedding", "religion": "Sikhism"}
KG Context: Recommended products: salwar, saree, lehenga. Recommended colours: red, pink, orange. Avoid colours: white, black.
```
SELECT * FROM products
WHERE product_type IN ('salwar', 'saree', 'lehenga', 'kurta', 'coord')
AND NOT (',' || COALESCE(colors,'') || ',' LIKE '%,white,%' OR ',' || COALESCE(colors,'') || ',' LIKE '%,ivory,%' OR ',' || COALESCE(colors,'') || ',' LIKE '%,black,%')
AND (',' || COALESCE(colors,'') || ',' LIKE '%,red,%' OR ',' || COALESCE(colors,'') || ',' LIKE '%,pink,%' OR ',' || COALESCE(colors,'') || ',' LIKE '%,orange,%' OR title LIKE '%red%' OR title LIKE '%pink%' OR colors IS NULL)
ORDER BY
  CASE WHEN product_type IN ('salwar', 'saree', 'lehenga') THEN 0 ELSE 1 END,
  CASE WHEN ',' || colors || ',' LIKE '%,red,%' OR title LIKE '%red%' THEN 0
       WHEN ',' || colors || ',' LIKE '%,pink,%' OR title LIKE '%pink%' THEN 0
       ELSE 1 END
LIMIT 20
```

### Example 8: Onam / Kerala — white and gold only
Intents: {"event": "Onam", "location": "Kerala"}
KG Context: Recommended products: saree, kurta. Recommended colours: white, cream, gold. Avoid colours: red, pink, bright colors.
```
SELECT * FROM products
WHERE product_type IN ('saree', 'kurta', 'salwar', 'coord', 'dress')
AND (',' || COALESCE(colors,'') || ',' LIKE '%,white,%' OR ',' || COALESCE(colors,'') || ',' LIKE '%,cream,%' OR ',' || COALESCE(colors,'') || ',' LIKE '%,ivory,%' OR ',' || COALESCE(colors,'') || ',' LIKE '%,off white,%' OR ',' || COALESCE(colors,'') || ',' LIKE '%,gold,%' OR title LIKE '%white%' OR title LIKE '%cream%' OR title LIKE '%ivory%' OR title LIKE '%gold%')
ORDER BY
  CASE WHEN product_type IN ('saree', 'kurta') THEN 0 ELSE 1 END
LIMIT 20
```

### Example 9: Bachelorette — prefer modern/western styles
Intents: {"occasion": "bachelorette"}
KG Context: Recommended products: dress, coord, skirt, top. Avoid: saree, salwar.
```
SELECT * FROM products
WHERE product_type IN ('dress', 'coord', 'skirt', 'top', 'kaftan')
AND product_type NOT IN ('saree', 'salwar', 'kurta', 'lehenga')
ORDER BY
  CASE WHEN product_type IN ('dress', 'coord') THEN 0 ELSE 1 END,
  CASE WHEN ',' || patterns || ',' LIKE '%,sequin%' OR title LIKE '%sequin%' THEN 0 ELSE 1 END
LIMIT 20
```

### Example 10: Budget-friendly — sort by price ASC
Intents: {"occasion": "housewarming", "budget": "economical"}
KG Context: Recommended products: kurta, salwar, saree. Recommended colours: yellow, orange. Recommended patterns: ethnic.
```
SELECT * FROM products
WHERE product_type IN ('kurta', 'salwar', 'saree', 'coord', 'dress')
ORDER BY
  CASE WHEN product_type IN ('kurta', 'salwar', 'saree') THEN 0 ELSE 1 END,
  price ASC
LIMIT 20
```

### Example 11: Numeric budget cap — "under 2000 for Diwali"
Intents: {"event": "Diwali", "price_max": 2000}
KG Context: Recommended products: saree, salwar, kurta. Recommended colours: red, yellow, orange, gold.
```
SELECT * FROM products
WHERE product_type IN ('kurta', 'salwar', 'saree', 'coord', 'dress', 'lehenga')
AND price <= 2000
ORDER BY
  CASE WHEN product_type IN ('saree', 'salwar', 'kurta') THEN 0 ELSE 1 END,
  CASE WHEN ',' || colors || ',' LIKE '%,red,%' OR title LIKE '%red%' THEN 0
       WHEN ',' || colors || ',' LIKE '%,yellow,%' OR title LIKE '%yellow%' THEN 0
       ELSE 1 END,
  price ASC
LIMIT 20
```

### Example 12: Explicit product type + functional need
Intents: {"product_type": "lehenga", "occasion": "sangeet", "functional_needs": "dance-friendly"}
KG Context: Recommended products: lehenga, saree. Recommended colours: pink, magenta. Recommended patterns: sequin, mirror work.
```
SELECT * FROM products
WHERE product_type IN ('lehenga', 'lehenga set')
ORDER BY
  CASE WHEN ',' || colors || ',' LIKE '%,pink,%' OR title LIKE '%pink%' THEN 0
       WHEN ',' || colors || ',' LIKE '%,magenta,%' OR title LIKE '%magenta%' THEN 0
       ELSE 1 END,
  CASE WHEN ',' || materials || ',' LIKE '%,georgette,%' OR ',' || materials || ',' LIKE '%,chiffon,%' THEN 0 ELSE 1 END,
  CASE WHEN ',' || patterns || ',' LIKE '%,sequin%' OR title LIKE '%sequin%' THEN 0 ELSE 1 END
LIMIT 20
```"""

SQL_GENERATION_USER = """## User Query
{query}

## Extracted Intents
{intents}

## Knowledge Graph Context
{kg_context}

## Budget Signal
{budget_signal}

## Additional SQL Guardrails
{cultural_notes}

## Available product_type values in this database
{available_types}

Generate a SQLite SELECT query to find the best matching products.

Before returning, silently verify:
- The query is one SELECT statement over products
- Every product_type literal appears in the available product_type list
- Hard constraints from explicit user intents are preserved
- Optional KG/style/color/material signals are ranking signals unless explicitly marked mandatory
- LIMIT 20 is present

Return ONLY the SQL query."""

SQL_RETRY_USER = """The previous SQL query returned 0 results. Here is the failed query:

```sql
{failed_sql}
```

The database has these product_type values: {available_types}

Please generate a LESS RESTRICTIVE query:
1. Keep product_type and explicit numeric price constraints in WHERE
2. Use more product_type values from the available list only when the user did not request a specific product_type
3. Move color, pattern, material, and style filters to ORDER BY
4. Remove a gender filter only when gender was not explicitly present in intents
5. Make sure product_type values match EXACTLY what's available in the database
6. Do not assume a specific wedding culture when intents contain "_needs_religion": true

Return ONLY the corrected SQL query."""


def get_available_types(db_path):
    """Get all distinct product_type values from the database."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT product_type FROM products WHERE product_type IS NOT NULL AND product_type != '' ORDER BY product_type")
    types = [row[0] for row in cur.fetchall()]
    conn.close()
    return types


BUDGET_SIGNALS = {
    "luxury": "LUXURY — sort by price DESC, prefer highest-priced items",
    "premium": "PREMIUM — sort by price DESC, prefer higher-priced items",
    "expensive": "EXPENSIVE — sort by price DESC",
    "high-end": "HIGH-END — sort by price DESC",
    "extravagant": "EXTRAVAGANT — sort by price DESC, top of the range",
    "cheap": "BUDGET — sort by price ASC, prefer lowest-priced items",
    "affordable": "AFFORDABLE — sort by price ASC",
    "budget-friendly": "BUDGET — sort by price ASC",
    "economical": "ECONOMICAL — sort by price ASC",
    "low-cost": "LOW-COST — sort by price ASC",
    "moderate": "MID-RANGE — no strong price preference",
    "mid-range": "MID-RANGE — no strong price preference",
}


def _split_csv(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _parse_kg_context(kg_context_str):
    """Parse formatted KG context back into a small structured dict."""
    parsed = {}
    for line in kg_context_str.splitlines():
        if ":" not in line:
            continue
        label, values = line.split(":", 1)
        label = label.strip().lower()
        values = _split_csv(values)
        if label.startswith("recommended "):
            bucket = "recommended"
            tag = label.replace("recommended ", "")
        elif label.startswith("acceptable "):
            bucket = "acceptable"
            tag = label.replace("acceptable ", "")
        elif label.startswith("avoid "):
            bucket = "avoid"
            tag = label.replace("avoid ", "")
        else:
            continue
        if tag.endswith("s"):
            tag = tag[:-1]
        parsed.setdefault(tag, {}).setdefault(bucket, []).extend(values)
    return parsed


def _product_terms(canonical):
    from taxonomy import PRODUCT_TYPE_ALIASES
    terms = [canonical]
    terms.extend(PRODUCT_TYPE_ALIASES.get(canonical, []))
    return [t.lower() for t in terms if t]


def _product_terms_overlap(left, right):
    left_terms = set(_product_terms(left))
    right_terms = set(_product_terms(right))
    return any(
        l == r
        or l in r
        or r in l
        or l.rstrip("s") == r.rstrip("s")
        for l in left_terms
        for r in right_terms
    )


def _resolve_db_types(canonical_types, available_types):
    """Map canonical KG/user product types to actual brand DB values."""
    resolved = []
    for canonical in canonical_types:
        terms = _product_terms(canonical)
        for db_type in available_types:
            db_lower = db_type.lower()
            if any(
                term == db_lower
                or term in db_lower
                or db_lower in term
                or db_lower.rstrip("s") == term.rstrip("s")
                for term in terms
            ):
                if db_type not in resolved:
                    resolved.append(db_type)
    return resolved


def _resolve_broad_db_types(canonical_types, intents, available_types):
    """Find broad catalog buckets that need title-backed product matching.

    Some brand exports use coarse product_type values such as "men" or
    "kidswear" while the actual garment appears only in the title. Keep these
    behind explicit product requests so they do not dilute normal KG retrieval.
    """
    if not canonical_types:
        return []

    gender = str(intents.get("gender", "")).lower()
    child_like = _is_child_agegroup(intents)

    broad_terms = set()
    if child_like:
        broad_terms.update({"kid", "kids", "kidswear", "children"})
    if gender == "male":
        broad_terms.update({"men", "mens", "men's", "menswear"})

    # Menswear in some catalogs is stored as one bucket even for specific
    # garments like sherwani, bandhgala, jacket, or kurta.
    male_leaning = {"sherwani", "kurta", "jacket", "pant", "top"}
    if any(str(c).lower() in male_leaning for c in canonical_types):
        if gender == "male":
            broad_terms.update({"men", "mens", "men's", "menswear"})

    resolved = []
    for db_type in available_types:
        db_lower = db_type.lower()
        if any(non_apparel in db_lower for non_apparel in ("footwear", "jewellery", "jewelry", "bags", "accessories")):
            continue
        db_tokens = set(re.findall(r"[a-z]+", db_lower))
        if any(term == db_lower or term in db_tokens for term in broad_terms):
            if db_type not in resolved:
                resolved.append(db_type)
    return resolved


def _is_child_agegroup(intents):
    agegroup = str(intents.get("agegroup", "")).lower()
    return agegroup in {"baby", "child", "infant", "toddler", "tween", "teenager", "youth"}


def _resolve_apparel_fallback_types(available_types):
    """Return likely garment product_type buckets, excluding accessories."""
    preferred = [
        "kurta", "dress", "ethnic dresses", "lehenga", "saree", "salwar",
        "coord", "ethnic co-ord sets", "pant sets", "topwear", "top",
        "fusion set", "kaftan", "skirt", "jacket", "jackets and sets",
        "ethnic jackets", "sherwanis", "bandhgalas", "men", "mens",
        "kidswear", "kids", "bridal", "indo western", "suits and tuxedos",
        "blazers & sets", "bottomwear", "swimwear",
    ]
    available_by_lower = {t.lower(): t for t in available_types if t}
    resolved = []
    for wanted in preferred:
        if wanted in available_by_lower and available_by_lower[wanted] not in resolved:
            resolved.append(available_by_lower[wanted])
    if resolved:
        return resolved

    blocked = ("footwear", "jewellery", "jewelry", "bags", "accessories", "earrings", "necklace", "bracelet")
    return [t for t in available_types if t and not any(b in t.lower() for b in blocked)]


def _title_match_clause(canonical_types):
    terms = []
    for canonical in canonical_types:
        terms.extend(_product_terms(canonical))
    terms = list(dict.fromkeys(t for t in terms if len(t) >= 3))
    if not terms:
        return ""
    clauses = []
    for term in terms[:10]:
        safe = str(term).replace("'", "''").lower()
        clauses.append(f"LOWER(title) LIKE '%{safe}%'")
    return "(" + " OR ".join(clauses) + ")"


def _sql_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def _extract_sql_literals(list_body):
    return [
        value.replace("''", "'")
        for value in re.findall(r"'((?:''|[^'])*)'", list_body)
    ]


def _validate_select_sql(sql, available_types):
    """Fail closed on SQL shapes that should not be generated."""
    stripped = sql.strip()
    upper = stripped.upper()
    if not upper.startswith("SELECT"):
        raise ValueError(f"Generated query is not a SELECT: {stripped[:100]}")
    if ";" in stripped.rstrip(";"):
        raise ValueError("Generated query contains multiple SQL statements")
    banned = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "PRAGMA", "ATTACH", "DETACH")
    if any(re.search(rf"\b{word}\b", upper) for word in banned):
        raise ValueError("Generated query contains a forbidden SQL operation")

    available = {str(t) for t in available_types}
    for body in re.findall(r"\bproduct_type\s+(?:NOT\s+)?IN\s*\(([^)]*)\)", stripped, flags=re.IGNORECASE):
        unknown = [value for value in _extract_sql_literals(body) if value not in available]
        if unknown:
            raise ValueError(f"Generated query used unavailable product_type values: {unknown[:5]}")


def _contains_expr(column, term):
    safe = str(term).replace("'", "''").lower()
    return (
        f"',' || COALESCE({column}, '') || ',' LIKE '%,{safe},%' "
        f"OR LOWER(title) LIKE '%{safe}%'"
    )


def _has_specific_wedding_context(intents):
    occasion = str(intents.get("occasion", "")).lower()
    event = str(intents.get("event", "")).lower()
    religion = str(intents.get("religion", "")).lower()
    haystack = " ".join((occasion, event, religion))
    return any(
        marker in haystack
        for marker in (
            "hindu", "muslim", "islam", "christian", "sikh",
            "nikah", "anand karaj",
        )
    )


def _has_actionable_wedding_context(intents):
    if _has_specific_wedding_context(intents):
        return True
    return intents.get("occasion") == "wedding" and not intents.get("_needs_religion")


def _is_wedding_guest_query(query):
    query_lower = query.lower()
    return "wedding" in query_lower and "guest" in query_lower


def _is_mother_of_wedding_query(query):
    query_lower = query.lower()
    return "wedding" in query_lower and "mother of" in query_lower


def _deterministic_sql(query, intents, kg_context_str, available_types):
    """Build a conservative SQL query without LLM help.

    This is used when the LLM CLI fails or produces unusable SQL. It preserves
    hard user constraints such as product_type and numeric budget.
    """
    kg = _parse_kg_context(kg_context_str)

    requested_types = _split_csv(intents.get("product_type"))
    title_backed_types = []
    if requested_types:
        exact_types = _resolve_db_types(requested_types, available_types)
        broad_types = [
            t for t in _resolve_broad_db_types(requested_types, intents, available_types)
            if t not in exact_types
        ]
        if _is_child_agegroup(intents):
            allowed_types = broad_types + exact_types
        else:
            allowed_types = exact_types + broad_types
        title_backed_types = broad_types
        if not allowed_types:
            allowed_types = _resolve_apparel_fallback_types(available_types)
            title_backed_types = allowed_types
    else:
        exact_types = []
        broad_types = []
        kg_types = kg.get("product", {})
        canonical = kg_types.get("recommended", []) + kg_types.get("acceptable", [])
        allowed_types = _resolve_db_types(canonical, available_types)

    if not allowed_types:
        allowed_types = [t for t in available_types if t][:10]

    avoided_canonical = [
        t for t in kg.get("product", {}).get("avoid", [])
        if not any(_product_terms_overlap(t, requested) for requested in requested_types)
    ]
    avoided_canonical += _split_csv(intents.get("avoid_product_type"))
    avoided_types = _resolve_db_types(avoided_canonical, available_types)

    where = [f"product_type IN ({', '.join(_sql_string(t) for t in allowed_types[:12])})"]
    if requested_types and title_backed_types:
        title_clause = _title_match_clause(requested_types)
        if title_clause:
            exact_clause = ""
            if exact_types:
                exact_clause = (
                    "product_type IN ("
                    + ", ".join(_sql_string(t) for t in exact_types[:12])
                    + ") OR "
                )
            where.append(f"({exact_clause}{title_clause})")
    if avoided_types:
        where.append(f"product_type NOT IN ({', '.join(_sql_string(t) for t in avoided_types[:12])})")

    gender = intents.get("gender")
    if gender in ("female", "male"):
        where.append(f"gender IN ({_sql_string(gender)}, 'both')")

    if intents.get("price_max") is not None:
        where.append(f"price <= {float(intents['price_max'])}")
    if intents.get("price_min") is not None:
        where.append(f"price >= {float(intents['price_min'])}")

    avoid_colours = [c for c in kg.get("colour", {}).get("avoid", []) if c.lower() != "all"]
    avoid_colours.extend(_split_csv(intents.get("avoid_colour") or intents.get("avoid_color")))
    wedding_context = _has_actionable_wedding_context(intents)
    ambiguous_wedding = intents.get("_needs_religion") and intents.get("occasion") == "wedding"
    if (wedding_context or ambiguous_wedding) and _is_wedding_guest_query(query):
        avoid_colours.extend(["red", "white", "ivory"])
    if (wedding_context or ambiguous_wedding) and _is_mother_of_wedding_query(query):
        avoid_colours.append("red")
    if avoid_colours:
        avoid_colours = list(dict.fromkeys(avoid_colours))
        where.append(
            "NOT ("
            + " OR ".join(_contains_expr("colors", c) for c in avoid_colours[:8])
            + ")"
        )

    explicit_colours = _split_csv(intents.get("colour") or intents.get("color"))
    if ambiguous_wedding:
        recommended_colours = []
    else:
        recommended_colours = [
            c for c in kg.get("colour", {}).get("recommended", [])
            if c.lower() != "all"
        ]
    if avoid_colours:
        avoid_set = {c.lower() for c in avoid_colours}
        recommended_colours = [c for c in recommended_colours if c.lower() not in avoid_set]
    recommended_materials = kg.get("material", {}).get("recommended", [])
    recommended_patterns = kg.get("pattern", {}).get("recommended", [])

    order_parts = [
        "CASE " + " ".join(
            f"WHEN product_type = {_sql_string(t)} THEN {idx}"
            for idx, t in enumerate(allowed_types[:12])
        ) + " ELSE 99 END"
    ]

    colour_rank = explicit_colours or recommended_colours
    if colour_rank:
        order_parts.append(
            "CASE WHEN "
            + " OR ".join(_contains_expr("colors", c) for c in colour_rank[:8])
            + " THEN 0 ELSE 1 END"
        )
    if recommended_materials:
        order_parts.append(
            "CASE WHEN "
            + " OR ".join(_contains_expr("materials", m) for m in recommended_materials[:8])
            + " THEN 0 ELSE 1 END"
        )
    if recommended_patterns:
        order_parts.append(
            "CASE WHEN "
            + " OR ".join(_contains_expr("patterns", p) for p in recommended_patterns[:8])
            + " THEN 0 ELSE 1 END"
        )

    budget = str(intents.get("budget", "")).lower()
    if budget in ("luxury", "premium", "expensive") or "luxury" in query.lower():
        order_parts.append("price DESC")
    else:
        order_parts.append("price ASC")

    return (
        "SELECT * FROM products\n"
        f"WHERE {' AND '.join(where)}\n"
        f"ORDER BY {', '.join(order_parts)}\n"
        "LIMIT 20"
    )


def generate_sql(query, intents, kg_context_str, brand, available_types):
    """Use LLM to generate a SQL query."""
    intents_str = json.dumps(intents, indent=2)
    types_str = ", ".join(f"'{t}'" for t in available_types)

    # Determine budget signal
    budget = intents.get("budget", "")
    budget_signal = BUDGET_SIGNALS.get(budget.lower(), "No specific budget constraint")
    if "money is no object" in query.lower() or "luxury" in query.lower():
        budget_signal = "LUXURY — sort by price DESC, prefer highest-priced items"

    # Numeric price constraints
    price_max = intents.get("price_max")
    price_min = intents.get("price_min")
    if price_max:
        budget_signal += f"\nHARD PRICE CAP: WHERE price <= {price_max}"
    if price_min:
        budget_signal += f"\nMINIMUM PRICE: WHERE price >= {price_min}"

    # Determine cultural notes
    cultural = []
    query_lower = query.lower()

    if intents.get("avoid_colour") or intents.get("avoid_color"):
        cultural.append(
            "User explicitly excluded colour(s): "
            + ", ".join(_split_csv(intents.get("avoid_colour") or intents.get("avoid_color")))
            + ". Add a hard WHERE exclusion for these colors."
        )

    wedding_context = _has_actionable_wedding_context(intents)
    ambiguous_wedding = intents.get("_needs_religion") and intents.get("occasion") == "wedding"
    if ambiguous_wedding:
        cultural.append(
            "Ambiguous wedding context: do NOT infer Hindu, Muslim, Christian, or Sikh wedding. "
            "Treat generic wedding KG colors as low-confidence styling only; do not make red a default."
        )

    if (wedding_context or ambiguous_wedding) and _is_mother_of_wedding_query(query):
        cultural.append("Mother of bride/groom: Must NOT wear red (bride's color). Prefer royal blue, emerald, purple, gold, maroon.")

    if (wedding_context or ambiguous_wedding) and _is_wedding_guest_query(query):
        cultural.append("Wedding guest: Should NOT wear red (bride's color) or white. Prefer jewel tones, pastels, or vibrant colors.")

    # Explicit product type request
    if "product_type" in intents:
        from taxonomy import PRODUCT_TYPE_ALIASES
        requested = intents["product_type"]
        aliases = PRODUCT_TYPE_ALIASES.get(requested, [])
        matching_db_types = [t for t in available_types
                            if t == requested or t in aliases or requested in t]
        if matching_db_types:
            cultural.append(
                f"USER EXPLICITLY REQUESTED product_type: {requested} "
                f"— prioritize {', '.join(matching_db_types)} in WHERE clause.")

    # Functional needs → material/fit guidance
    FUNCTIONAL_TO_GUIDANCE = {
        "breathable": "cotton, linen, khadi — prioritize breathable fabrics",
        "waterproof": "nylon, polyester — prioritize water-resistant fabrics",
        "haldi-proof": "dark colors or machine-washable fabrics — avoid white, cream, light pastels",
        "sweat-proof": "cotton, linen, moisture-wicking — avoid silk, satin",
        "wrinkle-free": "polyester blends, georgette — avoid linen, cotton",
        "dance-friendly": "stretchable, flowy fabrics — georgette, chiffon, crepe. Prefer flared/A-line fit",
        "lightweight": "cotton, linen, chiffon, georgette — avoid velvet, heavy silk",
        "stain-resistant": "darker colors, polyester blends — avoid white, cream, silk",
        "travel-friendly": "wrinkle-free, lightweight, versatile — polyester, jersey",
        "warm": "wool, velvet, heavy silk, fleece — layerable fabrics",
    }
    functional = intents.get("functional_needs", "")
    if functional:
        for fn in functional.split(","):
            fn = fn.strip()
            if fn in FUNCTIONAL_TO_GUIDANCE:
                cultural.append(f"Functional requirement ({fn}): {FUNCTIONAL_TO_GUIDANCE[fn]}")

    # Style goals → fit/color guidance
    STYLE_TO_GUIDANCE = {
        "slimming": "Prefer dark colors, vertical patterns, A-line/empire waist silhouettes. Search title for 'slim', 'fitted', 'A-line'.",
        "flattering": "Prefer well-fitted cuts, empire waist, A-line. Boost structured fabrics.",
        "elongating": "Prefer vertical stripes, high-waist, long/maxi length. Avoid horizontal patterns.",
        "modest": "Prefer full sleeves, high neck, long length. Avoid off-shoulder, mini, crop.",
        "trendy": "Prefer current styles — coord sets, contemporary cuts, modern embellishments.",
        "classic": "Prefer timeless silhouettes — saree, A-line dress, clean cuts. Avoid bold/experimental.",
        "minimalist": "Prefer plain, solid colors, clean lines. Avoid heavy embellishment, sequins.",
        "bold": "Prefer statement pieces — bright colors, heavy embellishment, dramatic silhouettes.",
    }
    style = intents.get("style_goals", "")
    if style:
        for sg in style.split(","):
            sg = sg.strip()
            if sg in STYLE_TO_GUIDANCE:
                cultural.append(f"Style goal ({sg}): {STYLE_TO_GUIDANCE[sg]}")

    cultural_notes = "\n".join(cultural) if cultural else "No specific cultural constraints."

    prompt = SQL_GENERATION_USER.format(
        query=query,
        intents=intents_str,
        kg_context=kg_context_str,
        budget_signal=budget_signal,
        cultural_notes=cultural_notes,
        available_types=types_str,
    )

    start = time.time()
    raw = call_llm(prompt, system_prompt=SQL_GENERATION_SYSTEM, timeout=SQL_LLM_TIMEOUT)
    elapsed = (time.time() - start) * 1000

    # Extract SQL from response — strip any markdown fences
    sql = raw
    sql = re.sub(r'^```\w*\n?', '', sql)
    sql = re.sub(r'\n?```$', '', sql)
    sql = sql.strip()

    # Ensure LIMIT
    if "LIMIT" not in sql.upper():
        sql += "\nLIMIT 20"
    _validate_select_sql(sql, available_types)

    return sql, raw, elapsed


def execute_sql(db_path, sql):
    """Execute a SQL query against the product database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    start = time.time()
    try:
        cur.execute(sql)
        rows = [dict(row) for row in cur.fetchall()]
    except sqlite3.Error as e:
        conn.close()
        raise RuntimeError(f"SQL execution error: {e}\nQuery: {sql}")
    elapsed = (time.time() - start) * 1000

    conn.close()
    return rows, elapsed


def query_products(query, intents, kg_context_str, brand):
    """Full flow: generate SQL from LLM, execute against DB, return results.

    Returns:
        dict with sql, raw_llm_response, products, timings
    """
    db_path = DB_PATHS.get(brand.lower())
    if not db_path or not os.path.exists(db_path):
        raise FileNotFoundError(
            f"No database for brand: {brand}. Run scripts/data/build_db.py first."
        )

    available_types = get_available_types(db_path)

    result = {
        "brand": brand,
        "db_path": db_path,
        "available_types": available_types,
        "sql": None,
        "raw_llm_sql_response": None,
        "products": [],
        "product_count": 0,
        "timings": {},
        "errors": [],
    }

    # Step 1: LLM generates SQL
    try:
        sql, raw, elapsed = generate_sql(query, intents, kg_context_str, brand, available_types)
        result["sql"] = sql
        result["raw_llm_sql_response"] = raw
        result["timings"]["sql_generation_ms"] = round(elapsed)
    except Exception as e:
        result["errors"].append(f"SQL generation error: {str(e)}")
        result["sql"] = _deterministic_sql(query, intents, kg_context_str, available_types)
        sql = result["sql"]
        result["raw_llm_sql_response"] = None
        result["errors"].append("Used deterministic SQL because LLM SQL generation failed")

    # Step 2: Execute SQL
    try:
        rows, elapsed = execute_sql(db_path, sql)
        result["products"] = rows
        result["product_count"] = len(rows)
        result["timings"]["sql_execution_ms"] = round(elapsed)
    except Exception as e:
        result["errors"].append(f"SQL execution error: {e}")
        rows = []

    # Step 3: Self-correction — if 0 results, retry with relaxed query
    result["retries"] = []
    if len(rows) == 0 and result["sql"]:
        for attempt in range(2):
            try:
                retry_sql, retry_raw, retry_elapsed = _retry_sql_generation(
                    result["sql"], available_types, intents
                )

                result["retries"].append({
                    "attempt": attempt + 1,
                    "failed_sql": result["sql"],
                    "retry_sql": retry_sql,
                    "elapsed_ms": round(retry_elapsed),
                })

                retry_rows, exec_elapsed = execute_sql(db_path, retry_sql)
                if len(retry_rows) > 0:
                    result["sql"] = retry_sql + f"  -- RETRY #{attempt + 1}"
                    result["products"] = retry_rows
                    result["product_count"] = len(retry_rows)
                    result["timings"]["sql_execution_ms"] = round(exec_elapsed)
                    result["timings"]["retry_ms"] = round(retry_elapsed)
                    break
            except Exception as e:
                result["retries"].append({
                    "attempt": attempt + 1,
                    "error": str(e),
                })

    # Step 4: Final fallback — deterministic query using KG product types
    if result["product_count"] == 0:
        try:
            fallback_sql = _deterministic_sql(query, intents, kg_context_str, available_types)
            fallback_rows, exec_elapsed = execute_sql(db_path, fallback_sql)
            result["sql"] = fallback_sql + "  -- DETERMINISTIC FALLBACK"
            result["products"] = fallback_rows
            result["product_count"] = len(fallback_rows)
            result["timings"]["sql_execution_ms"] = round(exec_elapsed)
            result["errors"].append("Used deterministic fallback after retries failed")
        except Exception:
            pass

    return result


def _retry_sql_generation(failed_sql, available_types, intents=None):
    """Ask the LLM to fix a SQL query that returned 0 results."""
    intents = intents or {}
    types_str = ", ".join(f"'{t}'" for t in available_types)
    prompt = SQL_RETRY_USER.format(
        failed_sql=failed_sql,
        available_types=types_str,
    )
    hard_constraints = []
    if intents.get("price_max") is not None:
        hard_constraints.append(f"price <= {intents['price_max']}")
    if intents.get("price_min") is not None:
        hard_constraints.append(f"price >= {intents['price_min']}")
    if intents.get("product_type"):
        hard_constraints.append(f"requested product_type = {intents['product_type']}")
    if hard_constraints:
        prompt += "\n\nPreserve these hard constraints: " + "; ".join(hard_constraints)

    start = time.time()
    raw = call_llm(prompt, system_prompt=SQL_GENERATION_SYSTEM, timeout=SQL_LLM_TIMEOUT)
    elapsed = (time.time() - start) * 1000
    sql = re.sub(r'^```\w*\n?', '', raw)
    sql = re.sub(r'\n?```$', '', sql)
    sql = sql.strip()

    if not sql.upper().startswith("SELECT"):
        raise ValueError(f"Retry did not produce a SELECT: {sql[:100]}")
    if "LIMIT" not in sql.upper():
        sql += "\nLIMIT 20"
    _validate_select_sql(sql, available_types)

    return sql, raw, elapsed


if __name__ == "__main__":
    # Quick test
    from knowledge_graph import KnowledgeGraph

    from config import KG_PATH
    kg = KnowledgeGraph(KG_PATH)
    intents = {"place": "restaurant", "weather": "cloudy"}
    kg_result = kg.lookup(intents)
    kg_context = kg.format_context(kg_result)

    print("Testing SQL generation for: restaurant + cloudy weather")
    print(f"KG Context:\n{kg_context}\n")

    result = query_products(
        "What to wear to a restaurant in cloudy weather?",
        intents, kg_context, "masaba"
    )

    print(f"Generated SQL:\n{result['sql']}\n")
    print(f"Results: {result['product_count']} products")
    print(f"Timings: {result['timings']}")
    if result['errors']:
        print(f"Errors: {result['errors']}")
    for p in result['products'][:5]:
        print(f"  [{p['product_type']}] {p['title']} - colors: {p['colors']}, price: {p['price']}")
