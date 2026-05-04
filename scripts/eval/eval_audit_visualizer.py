"""Build a shareable audit UI from saved SuperSearch eval results.

The visualizer joins eval output back to product databases and local PDP image
manifests so reviewers can inspect the user query, recommended products, SQL
trace, scorer rationale, and catalog-fit evidence in one static HTML file.
"""

import argparse
import glob
import json
import os
import sqlite3
import sys
from pathlib import Path
from collections import Counter, defaultdict

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import EVAL_RESULTS_DIR, GOLDEN_EVAL_PATH
from knowledge_graph import PRODUCT_TYPE_ALIASES
from router import classify


SCRAPER_DATA_DIR = "/Users/aant/repos/scraper-infra/data"
LOCAL_IMAGE_CATALOG_DIR = os.path.join(SCRAPER_DATA_DIR, "pdp_catalogs_with_local_images")
LOCAL_IMAGE_ROOT = SCRAPER_DATA_DIR


EXPECTED_ALIASES = {
    "kurta set": "kurta",
    "kurti": "kurta",
    "co-ord set": "coord",
    "co ord set": "coord",
    "palazzo set": "pant",
    "palazzo": "pant",
    "sharara set": "salwar",
    "anarkali": "salwar",
    "gown": "dress",
    "swimwear": "swimsuit",
    "t-shirt": "top",
    "tee": "top",
    "jeans": "pant",
    "shawl": "scarf",
}


def normalize_type(value):
    value = (value or "").strip().lower()
    return EXPECTED_ALIASES.get(value, value)


def type_terms(canonical):
    canonical = normalize_type(canonical)
    terms = {canonical}
    terms.update(PRODUCT_TYPE_ALIASES.get(canonical, []))
    for raw, mapped in EXPECTED_ALIASES.items():
        if mapped == canonical:
            terms.add(raw)
    return {t.lower() for t in terms if t}


def type_matches(expected, actual):
    actual = (actual or "").lower()
    if not actual:
        return False
    terms = type_terms(expected)
    return any(
        t == actual
        or t in actual
        or actual in t
        or t.rstrip("s") == actual.rstrip("s")
        for t in terms
    )


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_local_image_map():
    """Map (brand_key, product_id/title) to a local image path in scraper-infra."""
    image_map = {}
    files = glob.glob(os.path.join(LOCAL_IMAGE_CATALOG_DIR, "*products_with_local_images*.json"))
    for path in files:
        if "kalki_fashion" in os.path.basename(path):
            brand = "kalki"
        elif "house_of_masaba" in os.path.basename(path):
            brand = "masaba"
        else:
            continue
        try:
            data = load_json(path)
        except Exception:
            continue
        for product in data.get("products", []):
            local_images = product.get("local_images") or []
            first = next((img for img in local_images if img.get("status") in ("downloaded", "existing")), None)
            if not first:
                continue
            local_path = first.get("local_path")
            if not local_path:
                continue
            abs_path = os.path.join(LOCAL_IMAGE_ROOT, local_path)
            product_id = str(product.get("id", ""))
            title = (product.get("title") or "").strip().lower()
            if product_id:
                image_map[(brand, product_id)] = abs_path
            if title:
                image_map[(brand, title)] = abs_path
    return image_map


def load_brand_products(brand, image_map):
    db_path = f"{brand}_products.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM products")]
    conn.close()

    by_title = {}
    title_counts = Counter()
    by_id = {}
    type_counts = Counter()
    prices = []
    for row in rows:
        row["brand"] = brand
        row_id = str(row.get("id", ""))
        title_key = (row.get("title") or "").strip().lower()
        if title_key:
            title_counts[title_key] += 1
        local = image_map.get((brand, row_id)) or image_map.get((brand, title_key))
        row["audit_image"] = local or row.get("image_url", "")
        row["audit_image_source"] = "local" if local else ("remote" if row.get("image_url") else "none")
        by_id[row_id] = row
        if title_key and title_key not in by_title:
            by_title[title_key] = row
        if row.get("product_type"):
            type_counts[row["product_type"]] += 1
        try:
            prices.append(float(row.get("price") or 0))
        except (TypeError, ValueError):
            pass

    return {
        "rows": rows,
        "by_title": by_title,
        "title_counts": dict(title_counts),
        "duplicate_title_groups": sum(1 for count in title_counts.values() if count > 1),
        "by_id": by_id,
        "type_counts": dict(type_counts.most_common()),
        "total": len(rows),
        "price_min": min(prices) if prices else None,
        "price_max": max(prices) if prices else None,
    }


def catalog_counts(products, expected_types):
    counts = {}
    for expected in expected_types or []:
        counts[expected] = sum(1 for p in products if type_matches(expected, p.get("product_type", "")))
    return counts


def text_matches(expected, actual):
    expected = (expected or "").strip().lower()
    actual = (actual or "").strip().lower()
    if not expected or not actual:
        return False
    groups = {
        "pastel": ["pastel", "blush", "mint", "lavender", "peach", "powder", "baby pink", "light"],
        "dark": ["black", "navy", "maroon", "wine", "brown", "charcoal"],
        "light": ["white", "ivory", "cream", "beige", "pastel", "mint", "peach"],
        "bright": ["yellow", "orange", "pink", "coral", "red", "lime", "turquoise"],
        "jewel tones": ["emerald", "ruby", "sapphire", "royal blue", "wine", "maroon"],
        "ethnic": ["ethnic", "zari", "embroidered", "embroidery", "mirror", "bandhani", "banarasi"],
        "traditional": ["traditional", "zari", "banarasi", "embroidered", "ethnic"],
        "light embroidery": ["light embroidery", "embroidery", "embroidered"],
        "heavy embellishment": ["heavy", "sequin", "sequins", "embellished", "zardozi"],
    }
    terms = groups.get(expected, [expected])
    return any(term in actual for term in terms)


def catalog_attribute_counts(products, expected_attributes):
    columns = {"colors": "colors", "materials": "materials", "patterns": "patterns"}
    coverage = {}
    for attr_name, column in columns.items():
        spec = expected_attributes.get(attr_name, {}) if expected_attributes else {}
        if not spec:
            continue
        coverage[attr_name] = {}
        for bucket in ("ideal", "acceptable", "avoid"):
            terms = spec.get(bucket) or []
            if terms:
                coverage[attr_name][bucket] = {
                    term: sum(1 for p in products if text_matches(term, p.get(column, "")))
                    for term in terms
                }
    return coverage


def product_flags(product, expected_types, expected_attributes):
    flags = []
    actual_type = product.get("product_type", "")
    if any(type_matches(t, actual_type) for t in expected_types.get("ideal", [])):
        flags.append({"label": "ideal type", "kind": "good"})
    elif any(type_matches(t, actual_type) for t in expected_types.get("acceptable", [])):
        flags.append({"label": "acceptable type", "kind": "ok"})
    elif any(type_matches(t, actual_type) for t in expected_types.get("avoid", [])):
        flags.append({"label": "avoid type violation", "kind": "bad"})
    else:
        flags.append({"label": "type miss", "kind": "warn"})

    columns = {"colors": "colors", "materials": "materials", "patterns": "patterns"}
    for attr_name, column in columns.items():
        spec = expected_attributes.get(attr_name, {}) if expected_attributes else {}
        actual = product.get(column, "")
        if spec.get("avoid") and any(text_matches(term, actual) for term in spec["avoid"]):
            flags.append({"label": f"avoid {attr_name}", "kind": "bad"})
        elif spec.get("ideal") and any(text_matches(term, actual) for term in spec["ideal"]):
            flags.append({"label": f"ideal {attr_name}", "kind": "good"})
        elif spec.get("acceptable") and any(text_matches(term, actual) for term in spec["acceptable"]):
            flags.append({"label": f"acceptable {attr_name}", "kind": "ok"})
    return flags


def product_lookup(rec, brand_products):
    product_id = str(rec.get("product_id", ""))
    title = (rec.get("title") or "").strip().lower()
    if product_id and product_id in brand_products["by_id"]:
        product = dict(brand_products["by_id"][product_id])
        product["_match_method"] = "id"
        product["_title_duplicate_count"] = brand_products["title_counts"].get(title, 0)
        return product
    if title and title in brand_products["by_title"]:
        product = dict(brand_products["by_title"][title])
        product["_match_method"] = "title"
        product["_title_duplicate_count"] = brand_products["title_counts"].get(title, 0)
        return product
    return {"_match_method": "none", "_title_duplicate_count": brand_products["title_counts"].get(title, 0)}


def diagnose(result, golden, coverage):
    scores = result.get("scores") or []
    zeroes = sum(1 for s in scores if s == 0)
    ideal_total = sum(coverage.get("ideal_counts", {}).values())
    acceptable_total = sum(coverage.get("acceptable_counts", {}).values())
    goodish = sum(1 for s in scores if s >= 2)
    if ideal_total == 0:
        return {
            "label": "Ideal catalog gap",
            "detail": (
                "No products matching ideal expected types were found in this brand catalog."
                if acceptable_total == 0
                else "No ideal product types were found, but acceptable substitutes exist in the catalog."
            ),
            "severity": "warn",
            "kind": "catalog",
        }
    if result.get("ndcg5", 0) == 0:
        return {
            "label": "Pipeline failure likely",
            "detail": "Ideal-looking inventory exists, but all displayed recommendations scored irrelevant.",
            "severity": "bad",
            "kind": "pipeline",
        }
    if zeroes >= 3:
        return {
            "label": "Mixed candidate quality",
            "detail": "The ranking includes several irrelevant items even though some relevant inventory exists.",
            "severity": "bad",
            "kind": "ranking",
        }
    if goodish == 0:
        return {
            "label": "Only acceptable matches",
            "detail": "Ranking order may be fine, but absolute relevance is weak.",
            "severity": "warn",
            "kind": "weak",
        }
    return {
        "label": "Mostly healthy",
        "detail": "Catalog coverage and ranking are both producing usable recommendations.",
        "severity": "ok",
        "kind": "healthy",
    }


def build_dataset(eval_path, golden_path):
    eval_data = load_json(eval_path)
    golden_items = {item["id"]: item for item in load_json(golden_path)}
    image_map = load_local_image_map()
    brands = {b: load_brand_products(b, image_map) for b in ("masaba", "kalki", "aza")}

    results = []
    for result in eval_data["results"]:
        brand = result["brand"]
        brand_products = brands[brand]
        golden = golden_items.get(result["query_id"], {})
        expected = golden.get("expected_product_types", {})
        expected_attributes = golden.get("expected_attributes", {})

        recs = []
        for rec in result.get("recommendations", []):
            product = product_lookup(rec, brand_products)
            enriched = dict(rec)
            enriched.update({
                "id": product.get("id", rec.get("product_id", "")),
                "product_type": product.get("product_type", rec.get("product_type", "")),
                "colors": product.get("colors", ""),
                "materials": product.get("materials", ""),
                "patterns": product.get("patterns", ""),
                "price": product.get("price", ""),
                "url": product.get("url", ""),
                "description": product.get("description", ""),
                "image": product.get("audit_image", ""),
                "image_source": product.get("audit_image_source", "none"),
                "match_method": product.get("_match_method", "none"),
                "title_duplicate_count": product.get("_title_duplicate_count", 0),
            })
            enriched["audit_flags"] = product_flags(enriched, expected, expected_attributes)
            enriched["matched_db_product"] = product.get("_match_method") in ("id", "title")
            recs.append(enriched)

        coverage = {
            "brand_total": brand_products["total"],
            "brand_price_min": brand_products["price_min"],
            "brand_price_max": brand_products["price_max"],
            "ideal_counts": catalog_counts(brand_products["rows"], expected.get("ideal", [])),
            "acceptable_counts": catalog_counts(brand_products["rows"], expected.get("acceptable", [])),
            "avoid_counts": catalog_counts(brand_products["rows"], expected.get("avoid", [])),
            "attribute_counts": catalog_attribute_counts(brand_products["rows"], expected_attributes),
            "top_types": list(brand_products["type_counts"].items())[:12],
        }

        workflow = classify(result.get("intents", {}), result.get("query", "")).value
        item = {
            "query_id": result["query_id"],
            "row_key": f"{result['query_id']}:{brand}",
            "query": result["query"],
            "brand": brand,
            "category": golden.get("category", "unknown"),
            "workflow": workflow,
            "intents": result.get("intents", {}),
            "expected": expected,
            "attributes": expected_attributes,
            "rubric": golden.get("scoring_rubric", {}),
            "cultural": golden.get("cultural_constraints", ""),
            "formality": golden.get("formality_level", ""),
            "db_products": result.get("db_products", 0),
            "recommendations": recs,
            "scores": result.get("scores", []),
            "ndcg5": result.get("ndcg5", 0),
            "mrr": result.get("mrr", 0),
            "hit_rate": result.get("hit_rate", 0),
            "sql": result.get("sql", ""),
            "db_trace": result.get("db_trace", {}),
            "recommendation_trace": result.get("recommendation_trace", {}),
            "scorer_trace": result.get("scorer_trace", {}),
            "kg_context": result.get("kg_context", ""),
            "coverage": coverage,
        }
        item["diagnosis"] = diagnose(item, golden, coverage)
        results.append(item)

    summary = dict(eval_data["summary"])
    summary["image_map_keys"] = len(image_map)
    summary["brand_inventory"] = {
        brand: {
            "total": data["total"],
            "price_min": data["price_min"],
            "price_max": data["price_max"],
            "duplicate_title_groups": data["duplicate_title_groups"],
            "top_types": list(data["type_counts"].items())[:12],
        }
        for brand, data in brands.items()
    }
    image_sources = Counter(
        rec.get("image_source", "none")
        for item in results
        for rec in item.get("recommendations", [])
    )
    summary["recommendation_image_sources"] = dict(image_sources)
    summary["eval_path"] = os.path.abspath(eval_path)
    summary["local_image_catalog_dir"] = LOCAL_IMAGE_CATALOG_DIR
    return {"summary": summary, "results": results}


def write_html(dataset, output_path):
    payload = json.dumps(dataset, ensure_ascii=False)
    css = r"""
*{box-sizing:border-box}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f6f7f9;color:#171a21}button,input,select{font:inherit}button{cursor:pointer}.app{height:100vh;display:grid;grid-template-rows:auto 1fr;overflow:hidden}.top{padding:14px 18px;border-bottom:1px solid #d9dee8;background:#fff}.brandline{display:flex;align-items:flex-start;justify-content:space-between;gap:24px}.brandline h1{font-size:20px;line-height:1;margin:0 0 4px;letter-spacing:0}.brandline p{margin:0;color:#667085;font-size:13px}.metrics{display:flex;gap:18px;align-items:center}.metric{font-size:12px;color:#667085}.metric b{display:block;color:#171a21;font-size:18px}.controls{display:grid;grid-template-columns:1.2fr repeat(4,minmax(130px,180px));gap:10px;margin-top:14px}.controls input,.controls select{height:36px;border:1px solid #cfd6e3;background:#fff;border-radius:7px;padding:0 10px;color:#171a21}.main{display:grid;grid-template-columns:59% 41%;min-height:0}.left,.right{min-width:0;min-height:0;overflow:auto}.left{border-right:1px solid #d9dee8;background:#f8fafc}.right{background:#151922;color:#eef2f7}.query-strip{display:flex;gap:8px;overflow:auto;padding:12px 18px;border-bottom:1px solid #d9dee8;background:#eef2f7;scrollbar-width:thin}.qchip{border:1px solid #ccd5e2;background:#fff;border-radius:999px;padding:8px 12px;white-space:nowrap;color:#1f2937;transition:.15s ease;max-width:420px;overflow:hidden;text-overflow:ellipsis}.qchip.active{background:#0f766e;color:#fff;border-color:#0f766e}.qchip.bad{border-color:#d65f4d}.qchip.warn{border-color:#c58a2a}.chat{padding:18px;max-width:1120px;margin:0 auto}.bubble{max-width:84%;padding:14px 16px;border-radius:14px;margin-bottom:14px;line-height:1.45}.user{margin-left:auto;background:#171a21;color:#fff;border-bottom-right-radius:4px}.assistant{background:#fff;border:1px solid #d9dee8;border-bottom-left-radius:4px}.assistant .chips,.flags{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}.chip,.flag{font-size:12px;border-radius:999px;padding:5px 9px;background:#eef6f5;border:1px solid #b7d8d4;color:#155e58}.flag.good{background:#e8f6ef;border-color:#acd9c2;color:#146c43}.flag.ok{background:#edf3ff;border-color:#bfd0f5;color:#31599b}.flag.warn{background:#fff6dd;border-color:#eed08b;color:#805d12}.flag.bad{background:#fff0ed;border-color:#efb6ad;color:#9f2c22}.scoreline{display:flex;align-items:center;gap:10px;margin:18px 0 12px}.scorebadge{height:38px;min-width:76px;border-radius:7px;display:grid;place-items:center;font-weight:750;color:#fff;background:#0f766e}.scorebadge.warn{background:#b7791f}.scorebadge.bad{background:#c24135}.diagnosis{font-size:13px;color:#667085}.products{display:grid;grid-template-columns:repeat(5,minmax(140px,1fr));gap:12px}.product{background:#fff;border:1px solid #d9dee8;border-radius:8px;overflow:hidden;display:flex;flex-direction:column;min-height:388px;transition:transform .16s ease,border-color .16s ease}.product:hover{transform:translateY(-2px);border-color:#0f766e}.photo{height:190px;background:#e6ebf2;position:relative;overflow:hidden}.photo img{width:100%;height:100%;object-fit:cover;display:block}.photo .missing{height:100%;display:grid;place-items:center;color:#667085;font-size:12px;padding:12px;text-align:center}.rank{position:absolute;top:8px;left:8px;background:rgba(23,26,33,.9);color:#fff;border-radius:999px;padding:4px 8px;font-size:12px}.pscore{position:absolute;top:8px;right:8px;border-radius:999px;padding:4px 8px;font-size:12px;background:#c24135;color:#fff}.pscore.s1{background:#a86d16}.pscore.s2{background:#0f766e}.pscore.s3{background:#146c43}.pbody{padding:10px;display:flex;flex-direction:column;gap:7px;flex:1}.ptype{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#667085}.ptitle{font-size:13px;font-weight:700;line-height:1.25}.meta{font-size:12px;color:#667085}.reason{font-size:12px;line-height:1.35;color:#344054;margin-top:auto}.right-inner{padding:18px 18px 28px}.trace-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:14px}.trace-head h2{margin:0;font-size:18px}.trace-head p{margin:4px 0 0;color:#aab4c3;font-size:12px}.pill{border:1px solid #4b5565;color:#f8fafc;border-radius:999px;padding:5px 9px;font-size:12px}.trace-nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px}.trace-nav a{color:#d1d5db;border:1px solid #303846;border-radius:999px;padding:5px 8px;text-decoration:none;font-size:12px}.timeline{display:flex;flex-direction:column;gap:12px}.step{border-left:2px solid #475467;padding-left:12px}.step h3{font-size:13px;margin:0 0 6px;color:#7dd3c7;text-transform:uppercase;letter-spacing:.08em}.step pre{white-space:pre-wrap;word-break:break-word;background:#0f131a;border:1px solid #2a3240;border-radius:8px;padding:10px;margin:0;color:#f8fafc;font-size:12px;line-height:1.45}.kv{display:grid;grid-template-columns:124px 1fr;gap:6px 10px;font-size:12px;color:#e5e7eb}.kv b{color:#98a2b3;font-weight:600}.coverage{display:grid;grid-template-columns:1fr 1fr;gap:10px}.coverage-box{background:#0f131a;border:1px solid #2a3240;border-radius:8px;padding:10px}.coverage-box h4{margin:0 0 8px;color:#7dd3c7;font-size:12px}.barrow{display:grid;grid-template-columns:1fr 54px;gap:8px;align-items:center;font-size:12px;margin:5px 0}.bar{height:6px;background:#303846;border-radius:999px;overflow:hidden}.bar span{display:block;height:100%;background:#0f766e}.empty{padding:40px;color:#667085}.note{font-size:12px;line-height:1.45;color:#cbd5e1;background:#0f131a;border:1px solid #2a3240;border-radius:8px;padding:10px}@media(max-width:1100px){.main{grid-template-columns:1fr}.right{height:58vh}.products{grid-template-columns:repeat(2,minmax(140px,1fr))}.controls{grid-template-columns:1fr 1fr}.brandline{display:block}.metrics{margin-top:10px}.bubble{max-width:100%}}
"""
    css += r"""
.main{grid-template-columns:320px minmax(560px,1fr) minmax(460px,40%)}.query-rail{min-height:0;overflow:hidden;background:#fff;border-right:1px solid #d9dee8;display:grid;grid-template-rows:auto 1fr}.rail-head{height:46px;padding:12px 14px;border-bottom:1px solid #d9dee8;display:flex;justify-content:space-between;align-items:center;color:#344054;font-size:13px}.rail-head span{color:#98a2b3;font-size:12px}.query-strip{display:flex;flex-direction:column;gap:8px;overflow:auto;padding:12px;background:#f8fafc;border-bottom:0}.qchip{width:100%;max-width:none;white-space:normal;text-align:left;border-radius:8px;padding:10px 11px;display:grid;gap:6px}.qchip.active{box-shadow:inset 3px 0 0 #7dd3c7}.qtop{display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:center;font-size:12px}.qtop span{color:inherit;opacity:.76}.qtop strong{font-size:13px}.qtext{font-size:13px;line-height:1.35;color:inherit}.qmeta{font-size:11px;color:#667085;line-height:1.25}.qchip.active .qmeta{color:#d2f4ef}.left{border-right:1px solid #d9dee8}.chat{max-width:980px;padding:22px}.bubble{max-width:94%;font-size:14px}.scoreline{align-items:flex-start;background:#fff;border:1px solid #d9dee8;border-radius:8px;padding:12px}.products{grid-template-columns:repeat(2,minmax(240px,1fr));gap:14px}.product{min-height:0}.photo{height:260px}.pbody{gap:8px;padding:12px}.ptitle{font-size:14px}.reason{font-size:12.5px}.right-inner{padding:20px}.step pre{font-size:12.5px;line-height:1.5}.coverage{grid-template-columns:1fr}.brand-compare{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:0 0 16px}.brand-mini{border:1px solid #d9dee8;background:#fff;border-radius:8px;padding:9px 10px;text-align:left;color:#344054}.brand-mini.active{border-color:#0f766e;background:#eef6f5}.brand-mini b{display:block;font-size:12px}.brand-mini span{display:block;font-size:13px;margin-top:3px}.brand-mini small{display:block;color:#667085;margin-top:2px}@media(max-width:1320px){.main{grid-template-columns:280px minmax(480px,1fr) minmax(420px,40%)}.products{grid-template-columns:1fr}.photo{height:300px}}@media(max-width:1100px){.main{grid-template-columns:1fr}.query-rail{height:230px;border-right:0;border-bottom:1px solid #d9dee8}.query-strip{display:flex;flex-direction:row;overflow:auto}.qchip{min-width:280px}.right{height:58vh}.products{grid-template-columns:repeat(2,minmax(160px,1fr))}.controls{grid-template-columns:1fr 1fr}.brandline{display:block}.metrics{margin-top:10px}.bubble{max-width:100%}}
/* Keep the run list readable with large exhaustive eval sets. */
.query-strip{align-content:start}.qchip{flex:0 0 auto;min-height:108px;overflow:visible;line-height:1.25;align-content:start}.qtop,.qtext,.qmeta{min-width:0}.qtext{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;word-break:break-word}.qmeta{display:block;white-space:normal}.qtop b,.qtop span,.qtop strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}@media(max-width:1320px){.qchip{min-height:118px}.qtext{-webkit-line-clamp:4}}@media(max-width:1100px){.qchip{flex:0 0 300px;min-height:120px}}
"""
    js = r"""
const DATA = __DATA__;
let state = { q: '', brand: 'all', category: 'all', outcome: 'all', sort: 'worst', selected: null };
const $ = sel => document.querySelector(sel);
const fmt = n => (typeof n === 'number' ? n.toFixed(3).replace(/\.?0+$/,'') : n);
const money = n => (n === '' || n === null || n === undefined ? 'n/a' : '₹' + Number(n).toLocaleString('en-IN'));
const esc = s => String(s ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
function severityClass(r){ if(r.ndcg5===0 || (r.scores||[]).filter(s=>s===0).length>=3) return 'bad'; if(r.ndcg5<.8 || (r.scores||[]).every(s=>s<2)) return 'warn'; return 'ok'; }
function filtered(){
  let rows = DATA.results.filter(r => {
    const text = (r.query+' '+r.brand+' '+r.category+' '+JSON.stringify(r.intents)).toLowerCase();
    if(state.q && !text.includes(state.q.toLowerCase())) return false;
    if(state.brand !== 'all' && r.brand !== state.brand) return false;
    if(state.category !== 'all' && r.category !== state.category) return false;
    if(state.outcome === 'fail' && !(r.ndcg5===0 || (r.scores||[]).filter(s=>s===0).length>=3)) return false;
    if(state.outcome === 'weak' && !(r.ndcg5>0 && r.ndcg5<.8)) return false;
    if(state.outcome === 'healthy' && !(r.ndcg5>=.9 && r.hit_rate>0)) return false;
    if(state.outcome === 'catalog' && r.diagnosis.kind !== 'catalog') return false;
    if(state.outcome === 'pipeline' && !['pipeline','ranking'].includes(r.diagnosis.kind)) return false;
    return true;
  });
  rows.sort((a,b)=> state.sort==='best' ? b.ndcg5-a.ndcg5 : state.sort==='id' ? a.query_id-b.query_id : a.ndcg5-b.ndcg5);
  return rows;
}
function setOptions(){
  const cats = [...new Set(DATA.results.map(r=>r.category))].sort();
  $('#category').innerHTML = '<option value="all">All categories</option>' + cats.map(c=>`<option>${esc(c)}</option>`).join('');
}
function renderTop(){
  $('#summary').innerHTML = `
    <div class="metric"><b>${fmt(DATA.summary.avg_ndcg5)}</b>NDCG@5</div>
    <div class="metric"><b>${fmt(DATA.summary.avg_mrr)}</b>MRR</div>
    <div class="metric"><b>${fmt(DATA.summary.avg_hit_rate)}</b>Hit rate</div>
    <div class="metric"><b>${DATA.summary.total_queries}</b>queries</div>`;
}
function renderStrip(){
  const rows = filtered();
  if(!rows.length){ $('#queryStrip').innerHTML = '<div class="empty">No matching queries.</div>'; return; }
  if(!state.selected || !rows.some(r=>r.row_key===state.selected)) state.selected = rows[0].row_key;
  $('#queryStrip').innerHTML = rows.map(r=>`
    <button class="qchip ${r.row_key===state.selected?'active':''} ${severityClass(r)}" data-key="${esc(r.row_key)}">
      <span class="qtop"><b>#${r.query_id}</b><span>${r.brand.toUpperCase()}</span><strong>${fmt(r.ndcg5)}</strong></span>
      <span class="qtext">${esc(r.query)}</span>
      <span class="qmeta">${esc(r.category)} · ${esc(r.diagnosis.label)}</span>
    </button>`).join('');
  document.querySelectorAll('.qchip').forEach(btn=>btn.onclick=()=>{state.selected=btn.dataset.key; render();});
}
function selected(){ return DATA.results.find(r=>r.row_key===state.selected) || filtered()[0]; }
function scoreClass(s){ return 's'+(s ?? 0); }
function imgTag(p){
  if(!p.image) return '<div class="missing">No image found</div>';
  return `<img src="${esc(p.image)}" alt="${esc(p.title)}" loading="lazy" onerror="this.parentElement.innerHTML='<div class=&quot;missing&quot;>Image unavailable</div>'">`;
}
function flagsHtml(flags){
  return `<div class="flags">${(flags||[]).map(f=>`<span class="flag ${esc(f.kind)}">${esc(f.label)}</span>`).join('')}</div>`;
}
function brandCompareHtml(r){
  const siblings = DATA.results
    .filter(x => x.query_id === r.query_id)
    .sort((a,b) => a.brand.localeCompare(b.brand));
  if(siblings.length < 2) return '';
  return `<div class="brand-compare">${siblings.map(x => `
    <button class="brand-mini ${x.row_key===r.row_key?'active':''}" data-key="${esc(x.row_key)}">
      <b>${x.brand.toUpperCase()}</b>
      <span>NDCG ${fmt(x.ndcg5)} · ${x.db_products} candidates</span>
      <small>${esc(x.diagnosis.label)}</small>
    </button>`).join('')}</div>`;
}
function renderChat(r){
  const sev = severityClass(r);
  const recs = r.recommendations || [];
  $('#chat').innerHTML = `
    <div class="bubble user">${esc(r.query)}</div>
    <div class="bubble assistant">
      <b>${r.brand.toUpperCase()} recommendation audit</b><br>
      Workflow <b>${esc(r.workflow)}</b>, ${r.db_products} DB candidates, scores ${esc(JSON.stringify(r.scores))}.
      <div class="chips">
        <span class="chip">${esc(r.category)}</span>
        <span class="chip">${esc(r.diagnosis.label)}</span>
        <span class="chip">images: ${recs.filter(p=>p.image_source==='local').length} local</span>
      </div>
    </div>
    <div class="scoreline">
      <div class="scorebadge ${sev}">${fmt(r.ndcg5)}</div>
      <div class="diagnosis"><b>${esc(r.diagnosis.label)}</b><br>${esc(r.diagnosis.detail)}</div>
    </div>
    ${brandCompareHtml(r)}
    <div class="products">
      ${recs.map(p=>`
        <article class="product">
          <div class="photo">${imgTag(p)}<span class="rank">#${p.rank}</span><span class="pscore ${scoreClass(p.relevance_score)}">${p.relevance_score}/3</span></div>
          <div class="pbody">
            <div class="ptype">${esc(p.product_type || 'unknown')} · ${esc(p.image_source || 'no image')}</div>
            <div class="ptitle">${esc(p.title)}</div>
            <div class="meta">${esc(p.colors || 'no colors')} · ${esc(p.materials || 'no materials')}</div>
            <div class="meta">${money(p.price)} · DB match: ${esc(p.match_method || 'none')}${p.title_duplicate_count>1?' · duplicate title':''}</div>
            ${flagsHtml(p.audit_flags)}
            <div class="reason">${esc(p.reason || '')}</div>
          </div>
        </article>`).join('')}
    </div>`;
}
function countsBox(title, counts){
  const vals = Object.entries(counts || {});
  const max = Math.max(1, ...vals.map(v=>v[1]));
  return `<div class="coverage-box"><h4>${esc(title)}</h4>${vals.length?vals.map(([k,v])=>`<div class="barrow"><div><div>${esc(k)}</div><div class="bar"><span style="width:${Math.min(100,v/max*100)}%"></span></div></div><b>${v}</b></div>`).join(''):'<div class="meta">No expected types</div>'}</div>`;
}
function attrCoverageBox(attributeCounts){
  const blocks = Object.entries(attributeCounts || {});
  if(!blocks.length) return '<div class="note">No expected color/material/pattern attributes in the golden rubric.</div>';
  return blocks.map(([attr, buckets]) => countsBox(`${attr} coverage`, Object.assign({}, ...(Object.entries(buckets).map(([bucket, values]) => Object.fromEntries(Object.entries(values).map(([k,v]) => [`${bucket}: ${k}`, v]))))))).join('');
}
function topTypesBox(topTypes){
  return countsBox('Top catalog product types', Object.fromEntries((topTypes || []).slice(0, 12)));
}
function renderTrace(r){
  const cov = r.coverage;
  const unmatched = (r.recommendations||[]).filter(p=>!p.matched_db_product).length;
  const duplicateMatches = (r.recommendations||[]).filter(p=>p.title_duplicate_count>1).length;
  const hasFullDbTrace = r.db_trace && Object.keys(r.db_trace).length;
  const dbTraceText = hasFullDbTrace
    ? JSON.stringify({
        timings: r.db_trace.timings,
        errors: r.db_trace.errors,
        retries: r.db_trace.retries,
        candidate_count: r.db_trace.product_count,
        candidate_sample: (r.db_trace.candidates || []).slice(0, 5),
        raw_llm_sql_response: r.db_trace.raw_llm_sql_response
      }, null, 2)
    : '';
  const llmTraceText = (r.recommendation_trace && Object.keys(r.recommendation_trace).length) || (r.scorer_trace && Object.keys(r.scorer_trace).length)
    ? JSON.stringify({recommendation_trace:r.recommendation_trace, scorer_trace:r.scorer_trace}, null, 2)
    : '';
  $('#trace').innerHTML = `
    <div class="trace-head">
      <div><h2>#${r.query_id} ${esc(r.brand.toUpperCase())}</h2><p>${esc(r.category)} · ${esc(r.workflow)} · ${esc(r.diagnosis.label)}</p></div>
      <div class="pill">NDCG ${fmt(r.ndcg5)}</div>
    </div>
    <nav class="trace-nav">
      <a href="#intent">Intent</a><a href="#catalog">Catalog</a><a href="#kg">KG</a><a href="#sql">SQL</a><a href="#score">Scorer</a>
    </nav>
    <div class="timeline">
      <section class="step"><h3>1. User Message</h3><pre>${esc(r.query)}</pre></section>
      <section class="step" id="intent"><h3>2. Intent Extraction</h3><pre>${esc(JSON.stringify(r.intents, null, 2))}</pre></section>
      <section class="step"><h3>3. Router</h3><div class="kv"><b>workflow</b><span>${esc(r.workflow)}</span><b>formality</b><span>${esc(r.formality || 'n/a')}</span><b>brand catalog</b><span>${cov.brand_total} products</span></div></section>
      <section class="step"><h3>4. Golden Rubric</h3><pre>${esc(JSON.stringify({expected:r.expected, rubric:r.rubric, cultural:r.cultural}, null, 2))}</pre></section>
      <section class="step" id="catalog"><h3>5. Catalog Fit</h3><div class="coverage">${countsBox('Ideal type coverage', cov.ideal_counts)}${countsBox('Acceptable type coverage', cov.acceptable_counts)}${topTypesBox(cov.top_types)}</div></section>
      <section class="step"><h3>6. Attribute Coverage</h3><div class="coverage">${attrCoverageBox(cov.attribute_counts)}</div></section>
      <section class="step"><h3>7. Product Join Quality</h3><div class="kv"><b>unmatched recs</b><span>${unmatched}</span><b>duplicate-title joins</b><span>${duplicateMatches}</span><b>image sources</b><span>${esc(JSON.stringify(DATA.summary.recommendation_image_sources))}</span></div></section>
      <section class="step" id="kg"><h3>8. Knowledge Graph Context</h3><pre>${esc(r.kg_context || 'No KG context')}</pre></section>
      <section class="step" id="sql"><h3>9. SQL / DB Candidate Pool</h3><div class="kv"><b>db candidates</b><span>${r.db_products}</span><b>sql</b><span>${esc(r.sql || 'not captured')}</span></div>${hasFullDbTrace?`<pre>${esc(dbTraceText)}</pre>`:`<div class="note">This saved eval file only persisted the SQL snippet and final scorer output. Future eval runs now persist full SQL, raw LLM responses, retries, timings, and candidate lineage.</div>`}</section>
      <section class="step" id="score"><h3>10. Scorer Output</h3><pre>${esc(JSON.stringify(r.recommendations.map(p=>({rank:p.rank,title:p.title,type:p.product_type,score:p.relevance_score,reason:p.reason,flags:p.audit_flags,match:p.match_method,duplicate_title_count:p.title_duplicate_count})), null, 2))}</pre></section>
      ${llmTraceText ? `<section class="step"><h3>11. Raw LLM Eval Trace</h3><pre>${esc(llmTraceText)}</pre></section>` : ''}
    </div>`;
}
function render(){
  renderTop(); renderStrip();
  const r = selected();
  if(!r) return;
  renderChat(r); renderTrace(r);
  document.querySelectorAll('.brand-mini').forEach(btn=>btn.onclick=()=>{state.selected=btn.dataset.key; render();});
}
function init(){
  setOptions();
  $('#search').oninput=e=>{state.q=e.target.value; state.selected=null; render();};
  $('#brand').onchange=e=>{state.brand=e.target.value; state.selected=null; render();};
  $('#category').onchange=e=>{state.category=e.target.value; state.selected=null; render();};
  $('#outcome').onchange=e=>{state.outcome=e.target.value; state.selected=null; render();};
  $('#sort').onchange=e=>{state.sort=e.target.value; state.selected=null; render();};
  window.addEventListener('keydown', e=>{
    const rows = filtered(); const idx = rows.findIndex(r=>r.row_key===state.selected);
    if(e.key==='ArrowDown'||e.key==='ArrowRight'){ state.selected = rows[Math.min(rows.length-1, idx+1)]?.row_key; render(); }
    if(e.key==='ArrowUp'||e.key==='ArrowLeft'){ state.selected = rows[Math.max(0, idx-1)]?.row_key; render(); }
  });
  render();
}
document.addEventListener('DOMContentLoaded', init);
"""
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SuperSearch Eval Audit</title>
  <style>{css}</style>
</head>
<body>
  <div class="app">
    <header class="top">
      <div class="brandline">
        <div><h1>SuperSearch Eval Audit</h1><p>Demo chat on the left. Pipeline stack trace and catalog fit on the right.</p></div>
        <div class="metrics" id="summary"></div>
      </div>
      <div class="controls">
        <input id="search" placeholder="Search query, brand, intent...">
        <select id="brand"><option value="all">All brands</option><option value="masaba">Masaba</option><option value="kalki">Kalki</option><option value="aza">Aza</option></select>
        <select id="category"></select>
        <select id="outcome"><option value="all">All outcomes</option><option value="fail">Failures / many zeroes</option><option value="pipeline">Pipeline/ranking likely</option><option value="weak">Weak NDCG</option><option value="catalog">Catalog gap likely</option><option value="healthy">Healthy</option></select>
        <select id="sort"><option value="worst">Worst first</option><option value="best">Best first</option><option value="id">Eval order</option></select>
      </div>
    </header>
    <main class="main">
      <aside class="query-rail">
        <div class="rail-head"><b>Runs</b><span>Use arrow keys</span></div>
        <div class="query-strip" id="queryStrip"></div>
      </aside>
      <section class="left">
        <div class="chat" id="chat"></div>
      </section>
      <aside class="right"><div class="right-inner" id="trace"></div></aside>
    </main>
  </div>
  <script>{js.replace('__DATA__', payload)}</script>
</body>
</html>"""
    with open(output_path, "w") as f:
        f.write(html_doc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", required=True, help="Path to a saved eval JSON file.")
    parser.add_argument("--golden", default=GOLDEN_EVAL_PATH)
    parser.add_argument("--out", default=os.path.join(EVAL_RESULTS_DIR, "eval_audit_visualizer.html"))
    args = parser.parse_args()

    dataset = build_dataset(args.eval, args.golden)
    write_html(dataset, args.out)
    print(f"Wrote {os.path.abspath(args.out)}")
    print(f"Queries: {len(dataset['results'])}, local image keys: {dataset['summary']['image_map_keys']}")
    print(f"Recommendation image sources: {dataset['summary']['recommendation_image_sources']}")


if __name__ == "__main__":
    main()
