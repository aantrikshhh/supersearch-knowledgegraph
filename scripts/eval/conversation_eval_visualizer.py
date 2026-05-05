"""Build a static HTML visualizer for conversation eval artifacts.

The conversation eval artifact is trace-heavy: every turn can include runtime
decisions, KG provenance, SQL/debug data, products, catalog status, and optional
LLM judge outputs. This script compacts that data into a browsable single-file
HTML report for mining workflow/config/KG/prompt insights.
"""

import argparse
import html
import json
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import BRAND_DB_PATHS, CATALOG_PATHS, EVAL_RESULTS_DIR


DEFAULT_EVAL = Path(EVAL_RESULTS_DIR) / "aza_conversation_500_deterministic_final.json"
DEFAULT_OUT = Path(EVAL_RESULTS_DIR) / "aza_conversation_500_visualizer.html"

SCRAPER_DATA_DIR = Path(CATALOG_PATHS.get("aza", "/Users/aant/repos/scraper-infra/data/aza_fashions_products.json")).parent
LOCAL_IMAGE_CATALOG_DIR = SCRAPER_DATA_DIR / "pdp_catalogs_with_local_images"
LOCAL_IMAGE_CATALOG_PREFIXES = {
    "kalki": "kalki_fashion",
    "masaba": "house_of_masaba",
}


def load_json(path):
    with open(path) as f:
        return json.load(f)


def count_by(items, key_fn):
    counts = Counter()
    for item in items:
        counts[key_fn(item)] += 1
    return dict(counts.most_common())


def percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round((len(ordered) - 1) * pct))
    return ordered[idx]


BRAND_BASE_URLS = {
    "aza": "https://www.azafashions.com",
    "kalki": "https://www.kalkifashion.com",
    "masaba": "https://www.houseofmasaba.com",
}


def normalize_image_src(value):
    if not value:
        return ""
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if item), "")
        if not value:
            return ""
    text = str(value)
    if text.startswith(("http://", "https://", "data:", "file:")):
        return text
    path = Path(text)
    if path.is_absolute() and path.exists():
        return path.as_uri()
    return text


def normalize_product_url(url, brand):
    if not url:
        return ""
    text = str(url)
    if text.startswith(("http://", "https://")):
        return text
    base = BRAND_BASE_URLS.get((brand or "").lower(), "")
    if base and text.startswith("/"):
        return base + text
    return text


def resolve_local_image_path(local_path):
    if not local_path:
        return None
    path = Path(str(local_path))
    if not path.is_absolute():
        path = SCRAPER_DATA_DIR / path
    return path if path.exists() else None


def iter_catalog_items(path):
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("products") or data.get("items") or []
    return []


def load_local_image_media(brand, product_ids):
    """Load checked-out PDP image files for brands that have local downloads.

    Aza is intentionally URL-backed: the current checked-out scraper data has
    catalog URLs but no local PDP image bundle. Kalki and Masaba have local image
    manifests, so the visualizer should use the actual files on disk.
    """
    prefix = LOCAL_IMAGE_CATALOG_PREFIXES.get(brand)
    if not prefix or not product_ids or not LOCAL_IMAGE_CATALOG_DIR.exists():
        return {}

    remaining = set(str(product_id) for product_id in product_ids)
    media = {}
    paths = sorted(LOCAL_IMAGE_CATALOG_DIR.glob(f"{prefix}_products_with_local_images__products_*.json"))
    for path in paths:
        if not remaining:
            break
        for item in iter_catalog_items(path):
            product_id = str(item.get("id") or "")
            if product_id not in remaining:
                continue
            for local_image in item.get("local_images") or []:
                local_path = resolve_local_image_path(local_image.get("local_path"))
                if not local_path:
                    continue
                media[(brand, product_id)] = {
                    "image_url": local_path.as_uri(),
                    "remote_image_url": normalize_image_src(local_image.get("remote_url")),
                    "url": normalize_product_url(
                        local_image.get("product_url") or item.get("product_url") or item.get("url"),
                        brand,
                    ),
                    "image_source": str(local_path),
                    "image_source_type": "local_file",
                }
                remaining.discard(product_id)
                break
    return media


def collect_product_ids_by_brand(result):
    ids_by_brand = {}
    for scenario in result.get("results", []):
        brand = (scenario.get("brand") or "").lower()
        if not brand:
            continue
        ids = ids_by_brand.setdefault(brand, set())
        for turn in scenario.get("turns", []):
            product_groups = [
                turn.get("primary_products", []),
                (turn.get("db_trace", {}) or {}).get("candidates", []),
                (turn.get("outfit_debug", {}) or {}).get("scored_candidates", []),
            ]
            for products in product_groups:
                for product in products or []:
                    product_id = product.get("id")
                    if product_id is not None:
                        ids.add(str(product_id))
    return ids_by_brand


def load_product_media(result):
    ids_by_brand = collect_product_ids_by_brand(result)
    media = {}
    for brand, product_ids in ids_by_brand.items():
        db_path = BRAND_DB_PATHS.get(brand)
        if not db_path or not Path(db_path).exists() or not product_ids:
            continue
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            ids = sorted(product_ids)
            for start in range(0, len(ids), 800):
                chunk = ids[start:start + 800]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT id, image_url, url FROM products WHERE id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    image_url = normalize_image_src(row["image_url"])
                    key = (brand, str(row["id"]))
                    media[key] = {
                        "image_url": image_url,
                        "url": normalize_product_url(row["url"], brand),
                        "image_source": image_url or CATALOG_PATHS.get(brand, db_path),
                        "image_source_type": "remote_url" if image_url.startswith(("http://", "https://")) else "catalog",
                    }
        finally:
            conn.close()

        for key, local_media in load_local_image_media(brand, product_ids).items():
            media[key] = {**media.get(key, {}), **local_media}
    return media


def compact_product(product, media=None, brand=""):
    product_id = str(product.get("id")) if product.get("id") is not None else ""
    product_media = (media or {}).get(((brand or "").lower(), product_id), {})
    image_url = product.get("image_url") or product_media.get("image_url") or ""
    url = product.get("url") or product_media.get("url") or ""
    return {
        "id": product.get("id"),
        "title": product.get("title", ""),
        "product_type": product.get("product_type", ""),
        "colors": product.get("colors", ""),
        "materials": product.get("materials", ""),
        "patterns": product.get("patterns", ""),
        "price": product.get("price", ""),
        "image_url": normalize_image_src(image_url),
        "url": normalize_product_url(url, brand),
        "image_source": product_media.get("image_source", ""),
        "image_source_type": product_media.get("image_source_type", ""),
        "remote_image_url": product_media.get("remote_image_url", ""),
    }


def compact_kg_trace(trace):
    if not isinstance(trace, dict):
        return {}
    if "days" in trace:
        return {
            "days": [
                {
                    "day": day.get("day"),
                    "activity": day.get("activity"),
                    "kg_trace": compact_kg_trace(day.get("kg_trace", {})),
                }
                for day in trace.get("days", [])[:8]
            ]
        }
    return {
        "lookup_keys": trace.get("lookup_keys", [])[:20],
        "missing_keys": trace.get("missing_keys", [])[:20],
        "conflicts": trace.get("conflicts", [])[:20],
        "result_summary": trace.get("result_summary", {}),
        "matched_entries_count": len(trace.get("matched_entries", [])),
        "skipped_entries_count": len(trace.get("skipped_entries", [])),
        "gender_filter": trace.get("gender_filter"),
    }


def compact_runtime_trace(trace):
    if not isinstance(trace, dict):
        return {}
    extraction = trace.get("extraction", {})
    response = trace.get("response", {})
    return {
        "prior_turn_count": trace.get("prior_turn_count"),
        "prior_workflow": trace.get("prior_workflow"),
        "is_followup": trace.get("is_followup"),
        "followup_reason": trace.get("followup_reason"),
        "merge_mode": trace.get("merge_mode"),
        "prior_intents": trace.get("prior_intents", {}),
        "new_intents": trace.get("new_intents", {}),
        "final_intents": trace.get("final_intents", {}),
        "intent_diff": trace.get("intent_diff", {}),
        "router": trace.get("router", {}),
        "clarification": trace.get("clarification", {}),
        "extraction": {
            "parsed_intents": extraction.get("parsed_intents", {}),
            "final_intents": extraction.get("final_intents", {}),
            "used_fallback": extraction.get("used_fallback"),
            "error": extraction.get("error"),
            "elapsed_ms": extraction.get("elapsed_ms"),
        },
        "response": {
            "used_fallback": response.get("used_fallback"),
            "error": response.get("error"),
        },
    }


def compact_outfit_debug(debug):
    if not isinstance(debug, dict):
        return {}
    return {
        "candidate_count": debug.get("candidate_count"),
        "requested_types": debug.get("requested_types", []),
        "recommended_types": debug.get("recommended_types", []),
        "acceptable_types": debug.get("acceptable_types", []),
        "kg_avoided_types": debug.get("kg_avoided_types", []),
        "user_avoided_types": debug.get("user_avoided_types", []),
        "selected_product_ids": debug.get("selected_product_ids", []),
        "scored_candidates": debug.get("scored_candidates", [])[:10],
        "rejected_candidates": debug.get("rejected_candidates", [])[:10],
    }


def compact_db_trace(turn, media=None, brand=""):
    trace = turn.get("db_trace", {}) or {}
    return {
        "timings": trace.get("timings", {}),
        "retries": trace.get("retries", [])[:5],
        "candidate_count": len(trace.get("candidates", [])),
        "candidates": [compact_product(p, media=media, brand=brand) for p in trace.get("candidates", [])[:10]],
        "available_type_count": len(trace.get("available_types", [])),
        "available_types_sample": trace.get("available_types", [])[:50],
        "raw_llm_sql_response": trace.get("raw_llm_sql_response"),
    }


def attach_judges(main_result, judge_results):
    """Merge optional judge artifacts into the main result by scenario/turn id."""
    judge_by_case = {}
    for result in judge_results:
        for scenario in result.get("results", []):
            for idx, turn in enumerate(scenario.get("turns", []), 1):
                if turn.get("judges"):
                    judge_by_case[f"{scenario['id']}::turn_{idx}"] = turn["judges"]

    if not judge_by_case:
        return

    for scenario in main_result.get("results", []):
        for idx, turn in enumerate(scenario.get("turns", []), 1):
            case_id = f"{scenario['id']}::turn_{idx}"
            if case_id in judge_by_case:
                turn["judges"] = judge_by_case[case_id]


def build_payload(result, source_paths):
    media = load_product_media(result)
    scenarios = []
    all_turns = []
    for scenario in result.get("results", []):
        brand = scenario.get("brand", "")
        compact_turns = []
        for idx, turn in enumerate(scenario.get("turns", []), 1):
            case_id = f"{scenario['id']}::turn_{idx}"
            compact = {
                "case_id": case_id,
                "turn_index": idx,
                "user": turn.get("user", ""),
                "passed": turn.get("passed", False),
                "workflow": turn.get("workflow", ""),
                "is_followup": turn.get("is_followup", False),
                "needs_clarification": turn.get("needs_clarification", False),
                "clarifying_questions": turn.get("clarifying_questions", []),
                "suggested_followups": turn.get("suggested_followups", []),
                "catalog_status": turn.get("catalog_status", {}),
                "intents": turn.get("intents", {}),
                "primary_product_count": turn.get("primary_product_count", 0),
                "unique_primary_product_count": turn.get("unique_primary_product_count", 0),
                "primary_products": [compact_product(p, media=media, brand=brand) for p in turn.get("primary_products", [])],
                "db_product_count": turn.get("db_product_count", 0),
                "db_errors": turn.get("db_errors", []),
                "sql": turn.get("sql", ""),
                "db_trace": compact_db_trace(turn, media=media, brand=brand),
                "kg_context": turn.get("kg_context", {}),
                "kg_trace": compact_kg_trace(turn.get("kg_trace", {})),
                "outfit_debug": compact_outfit_debug(turn.get("outfit_debug", {})),
                "runtime_trace": compact_runtime_trace(turn.get("runtime_trace", {})),
                "styling_notes": turn.get("styling_notes", []),
                "response_text": turn.get("response_text", ""),
                "elapsed_ms": turn.get("elapsed_ms", 0),
                "failures": turn.get("failures", []),
                "non_blocking_findings": turn.get("non_blocking_findings", []),
                "judges": turn.get("judges", {}),
            }
            compact_turns.append(compact)
            all_turns.append({
                **compact,
                "scenario_id": scenario.get("id", ""),
                "category": scenario.get("category", ""),
                "brand": scenario.get("brand", ""),
            })
        scenarios.append({
            "id": scenario.get("id", ""),
            "brand": scenario.get("brand", ""),
            "category": scenario.get("category", ""),
            "description": scenario.get("description", ""),
            "source_tags": scenario.get("source_tags", []),
            "passed": scenario.get("passed", False),
            "failures": scenario.get("failures", []),
            "turns": compact_turns,
        })

    elapsed = [t.get("elapsed_ms", 0) for t in all_turns if isinstance(t.get("elapsed_ms"), (int, float))]
    db_counts = [t.get("db_product_count", 0) for t in all_turns]
    primary_counts = [t.get("primary_product_count", 0) for t in all_turns]
    judge_counts = Counter()
    judge_classifications = Counter()
    for turn in all_turns:
        for judge_name, judge in (turn.get("judges") or {}).items():
            judge_counts[judge_name] += 1
            judge_classifications[f"{judge_name}:{judge.get('classification', 'unknown')}"] += 1

    summary = {
        **result.get("summary", {}),
        "source_paths": source_paths,
        "generated_at": result.get("generated_at"),
        "visualized_at": datetime.now().isoformat(timespec="seconds"),
        "category_counts": count_by(all_turns, lambda t: t.get("category") or "unknown"),
        "workflow_counts": count_by(all_turns, lambda t: t.get("workflow") or "unknown"),
        "catalog_status_counts": count_by(all_turns, lambda t: (t.get("catalog_status") or {}).get("label", "unknown")),
        "followup_counts": count_by(all_turns, lambda t: "followup" if t.get("is_followup") else "new_query"),
        "clarification_counts": count_by(all_turns, lambda t: "clarification" if t.get("needs_clarification") else "result"),
        "passed_turn_counts": count_by(all_turns, lambda t: "passed" if t.get("passed") else "failed"),
        "judge_counts": dict(judge_counts),
        "judge_classifications": dict(judge_classifications),
        "timings": {
            "avg_elapsed_ms": round(statistics.mean(elapsed), 1) if elapsed else 0,
            "p95_elapsed_ms": percentile(elapsed, 0.95),
            "max_elapsed_ms": max(elapsed) if elapsed else 0,
        },
        "retrieval": {
            "avg_db_products": round(statistics.mean(db_counts), 1) if db_counts else 0,
            "avg_primary_products": round(statistics.mean(primary_counts), 1) if primary_counts else 0,
            "zero_db_turns": sum(1 for c in db_counts if c == 0),
            "zero_primary_turns": sum(1 for c in primary_counts if c == 0),
        },
    }
    return {"summary": summary, "scenarios": scenarios, "turns": all_turns}


def esc_json(data):
    # Keep the script payload as valid JSON text while making it safe to embed
    # inside an HTML script element.
    return (
        json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_legacy_html(payload, title):
    data_json = esc_json(payload)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{
  --bg:#f7f8fb; --panel:#ffffff; --ink:#18202f; --muted:#647084; --line:#dce1ea;
  --accent:#1d6b63; --accent2:#994c00; --bad:#b3261e; --warn:#996a00; --ok:#146c2e;
  --chip:#eef2f7; --shadow:0 8px 24px rgba(24,32,47,.08);
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}}
header{{position:sticky;top:0;z-index:5;background:rgba(247,248,251,.96);border-bottom:1px solid var(--line);backdrop-filter:blur(10px)}}
.header-inner{{display:flex;align-items:center;gap:16px;justify-content:space-between;padding:14px 18px}}
h1{{font-size:18px;margin:0;font-weight:750;letter-spacing:0}}
.sub{{font-size:12px;color:var(--muted);margin-top:2px}}
.layout{{display:grid;grid-template-columns:320px minmax(0,1fr);gap:16px;padding:16px}}
.side{{position:sticky;top:76px;height:calc(100vh - 92px);overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;box-shadow:var(--shadow)}}
.main{{min-width:0}}
.cards{{display:grid;grid-template-columns:repeat(6,minmax(130px,1fr));gap:10px;margin-bottom:14px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px;box-shadow:var(--shadow)}}
.metric{{font-size:24px;font-weight:800}}
.label{{font-size:12px;color:var(--muted);margin-top:2px}}
.controls{{display:grid;gap:10px}}
label{{display:grid;gap:5px;font-size:12px;color:var(--muted);font-weight:650}}
input,select{{width:100%;padding:8px 9px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink);font:inherit}}
button{{border:1px solid var(--line);background:#fff;border-radius:6px;padding:8px 10px;cursor:pointer;color:var(--ink);font-weight:650}}
button:hover{{border-color:var(--accent)}}
.tabs{{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}}
.tab{{background:#fff;border:1px solid var(--line);border-radius:999px;padding:7px 11px;cursor:pointer;font-weight:700;color:var(--muted)}}
.tab.active{{color:#fff;background:var(--accent);border-color:var(--accent)}}
.scenario-list{{display:grid;gap:10px}}
.scenario{{background:var(--panel);border:1px solid var(--line);border-radius:8px;box-shadow:var(--shadow);overflow:hidden}}
.scenario-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:12px 14px;cursor:pointer}}
.scenario-title{{font-weight:800}}
.scenario-meta{{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}}
.turns{{display:none;border-top:1px solid var(--line)}}
.scenario.open .turns{{display:block}}
.turn{{border-top:1px solid var(--line);padding:12px 14px;display:grid;gap:10px}}
.turn:first-child{{border-top:0}}
.turn-top{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:start}}
.query{{font-weight:750;font-size:15px}}
.pill{{display:inline-flex;align-items:center;gap:4px;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:750;background:var(--chip);color:var(--muted);white-space:nowrap}}
.pill.ok{{background:#e8f5ec;color:var(--ok)}}
.pill.bad{{background:#fdecea;color:var(--bad)}}
.pill.warn{{background:#fff4d6;color:var(--warn)}}
.pill.accent{{background:#e7f3f1;color:var(--accent)}}
.pill.brown{{background:#fff0df;color:var(--accent2)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.grid3{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.box{{border:1px solid var(--line);border-radius:7px;background:#fbfcfe;padding:10px;min-width:0}}
.box h3{{font-size:12px;text-transform:uppercase;color:var(--muted);margin:0 0 7px;letter-spacing:.04em}}
pre{{margin:0;white-space:pre-wrap;word-break:break-word;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#273248}}
.products{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px}}
.product{{border:1px solid var(--line);border-radius:7px;padding:9px;background:#fff}}
.product-title{{font-weight:750;margin-bottom:5px}}
.kv{{display:grid;grid-template-columns:120px minmax(0,1fr);gap:5px 8px;font-size:12px}}
.kv b{{color:var(--muted)}}
.bar-wrap{{display:grid;gap:7px;margin-top:6px}}
.bar-row{{display:grid;grid-template-columns:120px 1fr 42px;gap:8px;align-items:center;font-size:12px}}
.bar{{height:8px;border-radius:999px;background:#edf1f5;overflow:hidden}}
.bar span{{display:block;height:100%;background:var(--accent)}}
.hidden{{display:none!important}}
.empty{{padding:34px;text-align:center;color:var(--muted);background:var(--panel);border:1px dashed var(--line);border-radius:8px}}
.judge{{border-left:3px solid var(--accent);padding:8px 10px;background:#f7fbfa;border-radius:6px;margin:6px 0}}
.judge.bad{{border-color:var(--bad);background:#fff7f6}}
.judge.warn{{border-color:var(--warn);background:#fffaf0}}
details{{border:1px solid var(--line);border-radius:7px;background:#fff;padding:8px 10px}}
summary{{cursor:pointer;font-weight:750;color:var(--ink)}}
@media (max-width:1100px){{.layout{{grid-template-columns:1fr}}.side{{position:relative;top:0;height:auto}}.cards{{grid-template-columns:repeat(3,1fr)}}}}
@media (max-width:700px){{.cards,.grid2,.grid3{{grid-template-columns:1fr}}.turn-top{{grid-template-columns:1fr}}.layout{{padding:10px}}}}
</style>
</head>
<body>
<header>
  <div class="header-inner">
    <div>
      <h1>{html.escape(title)}</h1>
      <div class="sub" id="sourceLine"></div>
    </div>
    <button id="expandAll">Expand Filtered</button>
  </div>
</header>
<div class="layout">
  <aside class="side">
    <div class="controls">
      <label>Search<input id="search" placeholder="query, scenario, product, intent, SQL"></label>
      <label>Category<select id="category"><option value="">All</option></select></label>
      <label>Workflow<select id="workflow"><option value="">All</option></select></label>
      <label>Catalog Status<select id="catalog"><option value="">All</option></select></label>
      <label>Turn Type<select id="turnType"><option value="">All</option><option value="followup">Follow-up</option><option value="new">New query</option><option value="clarification">Clarification</option><option value="result">Result</option></select></label>
      <label>Pass Status<select id="pass"><option value="">All</option><option value="passed">Passed</option><option value="failed">Failed</option></select></label>
      <label>Judge<select id="judge"><option value="">All</option><option value="has">Has judge output</option><option value="non_ok">Non-ok judge output</option></select></label>
      <button id="reset">Reset Filters</button>
    </div>
    <div style="height:14px"></div>
    <div class="box"><h3>Flow Mix</h3><div id="categoryBars" class="bar-wrap"></div></div>
    <div style="height:10px"></div>
    <div class="box"><h3>Workflow Mix</h3><div id="workflowBars" class="bar-wrap"></div></div>
    <div style="height:10px"></div>
    <div class="box"><h3>Catalog Status</h3><div id="catalogBars" class="bar-wrap"></div></div>
  </aside>
  <main class="main">
    <section class="cards" id="cards"></section>
    <div class="tabs">
      <button class="tab active" data-view="scenarios">Scenarios</button>
      <button class="tab" data-view="failures">Failures & Gaps</button>
      <button class="tab" data-view="judges">Judges</button>
      <button class="tab" data-view="sql">SQL / KG</button>
    </div>
    <section id="content"></section>
  </main>
</div>
<script id="payload" type="application/json">{data_json}</script>
<script>
const payload = JSON.parse(document.getElementById('payload').textContent);
const state = {{view:'scenarios', expanded:false}};
const $ = sel => document.querySelector(sel);
const $$ = sel => [...document.querySelectorAll(sel)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const lower = v => String(v ?? '').toLowerCase();
const countObj = obj => Object.entries(obj || {{}}).sort((a,b)=>b[1]-a[1]);
function pill(text, cls='') {{ return `<span class="pill ${{cls}}">${{esc(text)}}</span>`; }}
function jsonBlock(obj) {{ return `<pre>${{esc(JSON.stringify(obj ?? {{}}, null, 2))}}</pre>`; }}
function sourceLine() {{
  const s = payload.summary;
  $('#sourceLine').textContent = `${{s.scenarios}} scenarios · ${{s.turns}} turns · visualized ${{s.visualized_at || ''}}`;
}}
function cards() {{
  const s = payload.summary;
  const items = [
    ['Scenarios', `${{s.passed_scenarios}}/${{s.scenarios}}`, 'passed'],
    ['Turns', `${{s.turns}}`, `${{s.failed_turns || 0}} failed`],
    ['Catalog Covered', `${{(s.catalog_status_counts||{{}}).covered || 0}}`, 'turns'],
    ['Retrieval Gaps', `${{(s.catalog_status_counts||{{}}).retrieval_gap || 0}}`, 'turns'],
    ['Avg DB Products', `${{s.retrieval?.avg_db_products ?? 0}}`, 'per turn'],
    ['P95 Elapsed', `${{s.timings?.p95_elapsed_ms ?? 0}}ms`, 'runtime'],
  ];
  $('#cards').innerHTML = items.map(([label, metric, sub]) => `<div class="card"><div class="metric">${{esc(metric)}}</div><div class="label">${{esc(label)}} · ${{esc(sub)}}</div></div>`).join('');
}}
function fillSelect(id, counts) {{
  const el = $(id);
  countObj(counts).forEach(([k,v]) => {{
    const opt = document.createElement('option');
    opt.value = k; opt.textContent = `${{k}} (${{v}})`;
    el.appendChild(opt);
  }});
}}
function bars(id, counts) {{
  const entries = countObj(counts);
  const max = Math.max(1, ...entries.map(e=>e[1]));
  $(id).innerHTML = entries.map(([k,v]) => `
    <div class="bar-row"><span title="${{esc(k)}}">${{esc(k)}}</span><div class="bar"><span style="width:${{Math.round(v/max*100)}}%"></span></div><b>${{v}}</b></div>
  `).join('');
}}
function initFilters() {{
  fillSelect('#category', payload.summary.category_counts);
  fillSelect('#workflow', payload.summary.workflow_counts);
  fillSelect('#catalog', payload.summary.catalog_status_counts);
  bars('#categoryBars', payload.summary.category_counts);
  bars('#workflowBars', payload.summary.workflow_counts);
  bars('#catalogBars', payload.summary.catalog_status_counts);
  ['#search','#category','#workflow','#catalog','#turnType','#pass','#judge'].forEach(id => $(id).addEventListener('input', render));
  $('#reset').addEventListener('click', () => {{
    ['#search','#category','#workflow','#catalog','#turnType','#pass','#judge'].forEach(id => $(id).value='');
    render();
  }});
  $('#expandAll').addEventListener('click', () => {{ state.expanded = !state.expanded; render(); }});
  $$('.tab').forEach(t => t.addEventListener('click', () => {{
    $$('.tab').forEach(x=>x.classList.remove('active'));
    t.classList.add('active'); state.view = t.dataset.view; render();
  }}));
}}
function judgeState(turn) {{
  const judges = Object.values(turn.judges || {{}});
  if (!judges.length) return 'none';
  return judges.some(j => j.classification && j.classification !== 'ok') ? 'non_ok' : 'ok';
}}
function turnText(turn, scenario) {{
  return lower([
    scenario.id, scenario.category, scenario.brand, scenario.description,
    turn.user, turn.workflow, JSON.stringify(turn.intents), turn.sql,
    JSON.stringify(turn.primary_products), JSON.stringify(turn.kg_context),
    JSON.stringify(turn.catalog_status), JSON.stringify(turn.judges)
  ].join(' '));
}}
function filteredScenarios() {{
  const q = lower($('#search').value);
  const cat = $('#category').value, wf = $('#workflow').value, cs = $('#catalog').value;
  const tt = $('#turnType').value, pass = $('#pass').value, judge = $('#judge').value;
  return payload.scenarios.map(s => {{
    const turns = s.turns.filter(t => {{
      if (cat && s.category !== cat) return false;
      if (wf && t.workflow !== wf) return false;
      if (cs && (t.catalog_status?.label || 'unknown') !== cs) return false;
      if (pass === 'passed' && !t.passed) return false;
      if (pass === 'failed' && t.passed) return false;
      if (tt === 'followup' && !t.is_followup) return false;
      if (tt === 'new' && t.is_followup) return false;
      if (tt === 'clarification' && !t.needs_clarification) return false;
      if (tt === 'result' && t.needs_clarification) return false;
      const js = judgeState(t);
      if (judge === 'has' && js === 'none') return false;
      if (judge === 'non_ok' && js !== 'non_ok') return false;
      if (q && !turnText(t,s).includes(q)) return false;
      return true;
    }});
    return {{...s, turns}};
  }}).filter(s => s.turns.length);
}}
function statusPills(t) {{
  const cat = t.catalog_status?.label || 'unknown';
  const catCls = cat === 'covered' ? 'ok' : (cat.includes('gap') ? 'warn' : 'accent');
  return [
    pill(t.passed ? 'passed' : 'failed', t.passed ? 'ok' : 'bad'),
    pill(t.workflow || 'workflow?', 'accent'),
    pill(cat, catCls),
    t.is_followup ? pill('follow-up','brown') : pill('new query'),
    t.needs_clarification ? pill('clarification','warn') : pill('result','ok'),
    Object.keys(t.judges||{{}}).length ? pill(`${{Object.keys(t.judges).length}} judges`, judgeState(t)==='non_ok'?'warn':'ok') : ''
  ].join(' ');
}}
function productCards(products) {{
  if (!products?.length) return '<div class="empty">No primary products logged.</div>';
  return `<div class="products">${{products.map(p => `
    <div class="product">
      <div class="product-title">${{esc(p.title || p.id || 'Untitled')}}</div>
      <div class="kv">
        <b>type</b><span>${{esc(p.product_type)}}</span>
        <b>colors</b><span>${{esc(p.colors)}}</span>
        <b>materials</b><span>${{esc(p.materials)}}</span>
        <b>price</b><span>${{esc(p.price)}}</span>
      </div>
    </div>`).join('')}}</div>`;
}}
function judgeBlocks(turn) {{
  const entries = Object.entries(turn.judges || {{}});
  if (!entries.length) return '<div class="empty">No judge output for this turn.</div>';
  return entries.map(([name,j]) => {{
    const cls = j.classification === 'ok' ? '' : (Number(j.score) <= 1 ? 'bad' : 'warn');
    return `<div class="judge ${{cls}}"><b>${{esc(name)}}</b> ${{pill(j.classification || 'unknown', j.classification === 'ok' ? 'ok' : 'warn')}} ${{pill('score '+(j.score ?? '?'))}}<div>${{esc(j.rationale || '')}}</div><div class="sub">${{esc((j.labels||[]).join(', '))}}</div></div>`;
  }}).join('');
}}
function turnHtml(t) {{
  const rt = t.runtime_trace || {{}};
  return `<div class="turn">
    <div class="turn-top">
      <div><div class="query">${{esc(t.user)}}</div><div class="scenario-meta">${{statusPills(t)}}</div></div>
      <div class="sub">${{esc(t.elapsed_ms)}}ms · DB ${{esc(t.db_product_count)}} · primary ${{esc(t.primary_product_count)}}/${{esc(t.unique_primary_product_count)}}</div>
    </div>
    ${{t.failures?.length ? `<div class="box"><h3>Failures</h3>${{jsonBlock(t.failures)}}</div>` : ''}}
    <div class="grid2">
      <div class="box"><h3>Intents</h3>${{jsonBlock(t.intents)}}</div>
      <div class="box"><h3>Runtime Decision</h3>${{jsonBlock({{
        followup_reason: rt.followup_reason,
        merge_mode: rt.merge_mode,
        router: rt.router,
        clarification: rt.clarification,
        intent_diff: rt.intent_diff
      }})}}</div>
    </div>
    <div class="grid2">
      <div class="box"><h3>KG Summary</h3>${{jsonBlock(t.kg_trace?.result_summary || t.kg_context || t.kg_trace)}}</div>
      <div class="box"><h3>KG Lookup</h3>${{jsonBlock({{
        lookup_keys: t.kg_trace?.lookup_keys || t.kg_trace?.days,
        missing_keys: t.kg_trace?.missing_keys,
        conflicts: t.kg_trace?.conflicts,
        matched_entries_count: t.kg_trace?.matched_entries_count
      }})}}</div>
    </div>
    <details><summary>SQL / DB Trace</summary><div style="height:8px"></div><div class="grid2"><div class="box"><h3>SQL</h3><pre>${{esc(t.sql || '')}}</pre></div><div class="box"><h3>DB Trace</h3>${{jsonBlock(t.db_trace)}}</div></div></details>
    <details><summary>Outfit Scoring</summary><div style="height:8px"></div>${{jsonBlock(t.outfit_debug)}}</details>
    <div class="box"><h3>Primary Products</h3>${{productCards(t.primary_products)}}</div>
    <div class="grid2">
      <div class="box"><h3>Response / Notes</h3><pre>${{esc((t.response_text || '') + '\\n\\n' + (t.styling_notes||[]).join('\\n'))}}</pre></div>
      <div class="box"><h3>Judges</h3>${{judgeBlocks(t)}}</div>
    </div>
  </div>`;
}}
function scenarioHtml(s) {{
  const failed = s.turns.some(t=>!t.passed);
  const judgeNonOk = s.turns.some(t=>judgeState(t)==='non_ok');
  const gaps = s.turns.filter(t=>(t.catalog_status?.label||'').includes('gap')).length;
  return `<article class="scenario ${{state.expanded ? 'open' : ''}}">
    <div class="scenario-head" onclick="this.parentElement.classList.toggle('open')">
      <div>
        <div class="scenario-title">${{esc(s.id)}} · ${{esc(s.category)}}</div>
        <div class="sub">${{esc(s.description || '')}}</div>
        <div class="scenario-meta">${{pill(s.brand || 'brand')}} ${{pill(`${{s.turns.length}} turns`)}} ${{failed?pill('has failures','bad'):pill('passed','ok')}} ${{gaps?pill(`${{gaps}} gaps`,'warn'):''}} ${{judgeNonOk?pill('judge findings','warn'):''}}</div>
      </div>
      <div class="sub">${{esc(s.source_tags?.join(', ') || '')}}</div>
    </div>
    <div class="turns">${{s.turns.map(turnHtml).join('')}}</div>
  </article>`;
}}
function renderScenarios() {{
  const list = filteredScenarios();
  $('#content').innerHTML = list.length ? `<div class="scenario-list">${{list.map(scenarioHtml).join('')}}</div>` : '<div class="empty">No scenarios match the filters.</div>';
}}
function renderFailures() {{
  const turns = filteredScenarios().flatMap(s => s.turns.map(t => ({{...t, scenario_id:s.id, category:s.category}})));
  const interesting = turns.filter(t => !t.passed || (t.catalog_status?.label||'').includes('gap') || t.non_blocking_findings?.length);
  $('#content').innerHTML = interesting.length ? `<div class="scenario-list">${{interesting.map(t => `<article class="scenario open"><div class="scenario-head"><div><div class="scenario-title">${{esc(t.scenario_id)}} · turn ${{t.turn_index}} · ${{esc(t.category)}}</div><div class="query">${{esc(t.user)}}</div><div class="scenario-meta">${{statusPills(t)}}</div></div></div><div class="turns">${{turnHtml(t)}}</div></article>`).join('')}}</div>` : '<div class="empty">No deterministic failures or catalog gaps in the filtered set.</div>';
}}
function renderJudges() {{
  const turns = filteredScenarios().flatMap(s => s.turns.map(t => ({{...t, scenario_id:s.id, category:s.category}}))).filter(t => Object.keys(t.judges||{{}}).length);
  $('#content').innerHTML = turns.length ? `<div class="scenario-list">${{turns.map(t => `<article class="scenario open"><div class="scenario-head"><div><div class="scenario-title">${{esc(t.scenario_id)}} · ${{esc(t.category)}}</div><div class="query">${{esc(t.user)}}</div><div class="scenario-meta">${{statusPills(t)}}</div></div></div><div class="turns"><div class="turn"><div class="grid2"><div class="box"><h3>Judges</h3>${{judgeBlocks(t)}}</div><div class="box"><h3>Evidence</h3>${{jsonBlock({{intents:t.intents, workflow:t.workflow, catalog_status:t.catalog_status, products:t.primary_products}})}}</div></div></div></div></article>`).join('')}}</div>` : '<div class="empty">No judge outputs attached to this artifact/filter.</div>';
}}
function renderSqlKg() {{
  const turns = filteredScenarios().flatMap(s => s.turns.map(t => ({{...t, scenario_id:s.id, category:s.category}}))).filter(t => !t.needs_clarification);
  $('#content').innerHTML = turns.length ? `<div class="scenario-list">${{turns.map(t => `<article class="scenario"><div class="scenario-head" onclick="this.parentElement.classList.toggle('open')"><div><div class="scenario-title">${{esc(t.scenario_id)}} · ${{esc(t.workflow)}} · DB ${{t.db_product_count}}</div><div class="query">${{esc(t.user)}}</div><div class="scenario-meta">${{statusPills(t)}}</div></div></div><div class="turns">${{turnHtml(t)}}</div></article>`).join('')}}</div>` : '<div class="empty">No result turns match the filters.</div>';
}}
function render() {{
  $('#expandAll').textContent = state.expanded ? 'Collapse Filtered' : 'Expand Filtered';
  if (state.view === 'failures') renderFailures();
  else if (state.view === 'judges') renderJudges();
  else if (state.view === 'sql') renderSqlKg();
  else renderScenarios();
}}
sourceLine(); cards(); initFilters(); render();
</script>
</body>
</html>"""


def render_html(payload, title):
    data_json = esc_json(payload)
    title_html = html.escape(title)
    template = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  --bg:#f4f5f2;
  --surface:#ffffff;
  --surface-2:#fafaf8;
  --ink:#171a1f;
  --muted:#667085;
  --line:#d9ded6;
  --line-strong:#c5cdc2;
  --accent:#0f6b5f;
  --accent-soft:#e4f1ee;
  --warn:#986500;
  --warn-soft:#fff3d4;
  --bad:#b42318;
  --bad-soft:#fff0ee;
  --ok:#16703a;
  --ok-soft:#e7f5ec;
}
* { box-sizing:border-box }
html,body { height:100% }
body {
  margin:0;
  background:var(--bg);
  color:var(--ink);
  font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;
}
button,input,select { font:inherit }
button { color:inherit }
a { color:var(--accent); text-decoration:none }
a:hover { text-decoration:underline }
.app {
  height:100vh;
  display:grid;
  grid-template-rows:auto minmax(0,1fr);
}
.topbar {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:12px;
  padding:9px 12px;
  border-bottom:1px solid var(--line);
  background:rgba(244,245,242,.96);
  backdrop-filter:blur(10px);
}
h1 { margin:0; font-size:16px; line-height:1.2; letter-spacing:0; font-weight:760 }
.source { margin-top:2px; color:var(--muted); font-size:11px }
.summary-strip { display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end }
.metric {
  min-width:70px;
  padding:6px 8px;
  border:1px solid var(--line);
  border-radius:7px;
  background:var(--surface);
}
.metric b { display:block; font-size:15px; line-height:1.1 }
.metric span { color:var(--muted); font-size:10px }
.workspace {
  min-height:0;
  display:grid;
  grid-template-columns:minmax(0,.92fr) minmax(0,1.08fr);
  grid-template-rows:168px minmax(0,1fr);
  gap:0;
  overflow:hidden;
}
.query-rail,.chat-pane,.trace-pane {
  min-height:0;
  overflow:auto;
  border-right:1px solid var(--line);
}
.query-rail {
  grid-column:1 / -1;
  grid-row:1;
  background:var(--surface);
  display:grid;
  grid-template-columns:220px minmax(0,1fr);
  border-right:0;
  border-bottom:1px solid var(--line);
}
.rail-filters {
  min-height:0;
  overflow:auto;
  z-index:2;
  display:grid;
  gap:6px;
  padding:9px;
  border-right:1px solid var(--line);
  background:var(--surface);
}
.filter-row { display:grid; grid-template-columns:1fr 1fr; gap:8px }
label { display:grid; gap:4px; color:var(--muted); font-size:11px; font-weight:700 }
input,select {
  width:100%;
  border:1px solid var(--line);
  border-radius:6px;
  background:#fff;
  color:var(--ink);
  padding:6px 8px;
  min-width:0;
}
.rail-count {
  display:flex;
  justify-content:space-between;
  align-items:center;
  color:var(--muted);
  font-size:11px;
}
.reset-btn {
  border:1px solid var(--line);
  background:var(--surface-2);
  border-radius:6px;
  padding:5px 8px;
  cursor:pointer;
  font-weight:700;
}
.query-list {
  min-height:0;
  overflow:auto;
  padding:8px;
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
  gap:8px;
  align-content:start;
}
.query-item {
  width:100%;
  display:grid;
  gap:6px;
  text-align:left;
  border:1px solid transparent;
  border-radius:7px;
  background:transparent;
  padding:8px;
  cursor:pointer;
}
.query-item:hover { background:var(--surface-2); border-color:var(--line) }
.query-item.active {
  background:var(--accent-soft);
  border-color:#9ecac3;
}
.query-title { font-weight:760; line-height:1.25; font-size:13px }
.query-meta,.pill-row { display:flex; gap:5px; flex-wrap:wrap; align-items:center }
.pill {
  display:inline-flex;
  align-items:center;
  max-width:100%;
  border-radius:999px;
  padding:2px 7px;
  background:#edf0ec;
  color:#4d5967;
  font-size:11px;
  font-weight:760;
  white-space:nowrap;
}
.pill.ok { background:var(--ok-soft); color:var(--ok) }
.pill.warn { background:var(--warn-soft); color:var(--warn) }
.pill.bad { background:var(--bad-soft); color:var(--bad) }
.pill.accent { background:var(--accent-soft); color:var(--accent) }
.chat-pane {
  grid-column:1;
  grid-row:2;
  background:var(--surface-2);
  display:grid;
  grid-template-rows:auto minmax(0,1fr);
}
.pane-head {
  position:sticky;
  top:0;
  z-index:2;
  padding:10px 12px;
  border-bottom:1px solid var(--line);
  background:rgba(250,250,248,.97);
  backdrop-filter:blur(10px);
}
.pane-title { font-weight:800; font-size:14px }
.pane-sub { color:var(--muted); font-size:11px; margin-top:2px }
.chat-thread {
  min-height:0;
  overflow:auto;
  padding:12px;
  display:grid;
  gap:14px;
  align-content:start;
}
.turn-block {
  display:grid;
  gap:8px;
  padding:8px;
  border:1px solid transparent;
  border-radius:8px;
  cursor:pointer;
}
.turn-block:hover { border-color:var(--line); background:#fff }
.turn-block.active { border-color:#9ecac3; background:#fff }
.bubble {
  max-width:96%;
  border:1px solid var(--line);
  border-radius:8px;
  padding:9px 10px;
  background:#fff;
}
.bubble.user {
  margin-left:auto;
  background:#15201f;
  color:#fff;
  border-color:#15201f;
}
.bubble.assistant { margin-right:auto }
.bubble-label {
  margin-bottom:5px;
  color:var(--muted);
  font-size:10px;
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.04em;
}
.bubble.user .bubble-label { color:#c9d6d3 }
.bubble-text { white-space:pre-wrap; overflow-wrap:anywhere }
.product-strip {
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(96px,1fr));
  gap:7px;
  margin-top:9px;
}
.mini-product {
  display:grid;
  gap:5px;
  border:1px solid var(--line);
  border-radius:6px;
  padding:5px;
  background:#fff;
  font-size:11px;
  min-width:0;
}
.mini-product img {
  width:100%;
  aspect-ratio:3/4;
  object-fit:cover;
  border-radius:5px;
  background:#eef0ea;
  display:block;
}
.image-placeholder {
  width:100%;
  aspect-ratio:3/4;
  border-radius:5px;
  border:1px dashed var(--line);
  background:#f3f4ef;
  color:var(--muted);
  display:grid;
  place-items:center;
  text-align:center;
  font-size:10px;
}
.mini-product b {
  display:block;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.mini-product span { color:var(--muted) }
.trace-pane {
  grid-column:2;
  grid-row:2;
  border-right:0;
  border-left:1px solid var(--line);
  background:var(--surface);
  display:grid;
  grid-template-rows:auto minmax(0,1fr);
}
.trace-scroll {
  min-height:0;
  overflow:auto;
  padding:10px 12px 16px;
  display:grid;
  gap:9px;
  align-content:start;
}
.section {
  border:1px solid var(--line);
  border-radius:8px;
  background:#fff;
  overflow:hidden;
}
.section h2 {
  margin:0;
  padding:8px 10px;
  border-bottom:1px solid var(--line);
  font-size:11px;
  text-transform:uppercase;
  letter-spacing:.04em;
  color:#536071;
}
.section-body { padding:9px 10px; display:grid; gap:9px }
.kv-grid {
  display:grid;
  grid-template-columns:116px minmax(0,1fr);
  gap:7px 10px;
  font-size:11px;
}
.kv-grid b { color:var(--muted); font-weight:730 }
.kv-grid span { min-width:0; overflow-wrap:anywhere }
.intent-grid {
  display:flex;
  gap:6px;
  flex-wrap:wrap;
}
.intent-chip {
  border:1px solid var(--line);
  border-radius:999px;
  padding:4px 7px;
  background:var(--surface-2);
  font-size:11px;
}
.intent-chip b { color:#4f5b68 }
.kg-row {
  display:grid;
  grid-template-columns:72px minmax(0,1fr);
  gap:8px;
  align-items:start;
  padding:7px 0;
  border-bottom:1px solid #edf0ec;
}
.kg-row:last-child { border-bottom:0 }
.kg-row b { color:var(--muted); font-size:12px }
.chip-line { display:flex; gap:5px; flex-wrap:wrap }
.code {
  margin:0;
  white-space:pre-wrap;
  overflow-wrap:anywhere;
  font:11px/1.42 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  color:#222a35;
}
details {
  border:1px solid var(--line);
  border-radius:7px;
  padding:8px 10px;
  background:var(--surface-2);
}
summary { cursor:pointer; font-weight:760 }
.product-table { display:grid; gap:6px }
.product-row {
  display:grid;
  grid-template-columns:62px minmax(0,1fr);
  gap:8px;
  align-items:start;
  padding:7px 0;
  border-bottom:1px solid #edf0ec;
  font-size:11px;
}
.product-row:last-child { border-bottom:0 }
.product-row b { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.product-thumb {
  width:62px;
  aspect-ratio:3/4;
  border-radius:6px;
  object-fit:cover;
  background:#eef0ea;
  border:1px solid var(--line);
}
.product-thumb.missing {
  display:grid;
  place-items:center;
  color:var(--muted);
  font-size:10px;
  text-align:center;
}
.product-meta {
  display:flex;
  gap:5px;
  flex-wrap:wrap;
  margin-top:5px;
}
.judge {
  border-left:3px solid var(--accent);
  background:var(--accent-soft);
  border-radius:6px;
  padding:9px 10px;
}
.judge.warn { border-color:var(--warn); background:var(--warn-soft) }
.judge.bad { border-color:var(--bad); background:var(--bad-soft) }
.empty {
  padding:24px;
  color:var(--muted);
  border:1px dashed var(--line);
  border-radius:8px;
  text-align:center;
}
@media (max-width:1220px) {
  .workspace { grid-template-columns:minmax(0,.92fr) minmax(0,1.08fr) }
}
@media (max-width:760px) {
  .topbar { padding:7px 10px }
  .summary-strip { display:none }
  .workspace {
    grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);
    grid-template-rows:150px minmax(0,1fr);
  }
  .query-rail { grid-template-columns:188px minmax(0,1fr) }
  .filter-row,.kv-grid { grid-template-columns:1fr }
  .query-list { grid-template-columns:repeat(auto-fill,minmax(190px,1fr)) }
  .product-row { grid-template-columns:54px minmax(0,1fr) }
  .product-thumb { width:54px }
  .pill { font-size:10px; padding:2px 6px }
}
</style>
</head>
<body>
<div class="app">
  <header class="topbar">
    <div>
      <h1>__TITLE__</h1>
      <div class="source" id="sourceLine"></div>
    </div>
    <div class="summary-strip" id="summaryStrip"></div>
  </header>
  <div class="workspace">
    <aside class="query-rail">
      <div class="rail-filters">
        <label>Search
          <input id="search" placeholder="query, product, SQL, intent">
        </label>
        <div class="filter-row">
          <label>Category<select id="category"><option value="">All</option></select></label>
          <label>Workflow<select id="workflow"><option value="">All</option></select></label>
        </div>
        <div class="filter-row">
          <label>Status<select id="catalog"><option value="">All</option></select></label>
          <label>Turn<select id="turnType"><option value="">All</option><option value="followup">Follow-up</option><option value="new">New query</option><option value="clarification">Clarification</option><option value="result">Result</option></select></label>
        </div>
        <div class="filter-row">
          <label>Pass<select id="pass"><option value="">All</option><option value="passed">Passed</option><option value="failed">Failed</option></select></label>
          <label>Judge<select id="judge"><option value="">All</option><option value="has">Has judge</option><option value="non_ok">Non-ok judge</option></select></label>
        </div>
        <div class="rail-count"><span id="queryCount"></span><button class="reset-btn" id="reset">Reset</button></div>
      </div>
      <div class="query-list" id="queryList"></div>
    </aside>
    <main class="chat-pane">
      <div class="pane-head">
        <div class="pane-title" id="chatTitle">Conversation</div>
        <div class="pane-sub" id="chatMeta"></div>
      </div>
      <div class="chat-thread" id="chatPane"></div>
    </main>
    <aside class="trace-pane">
      <div class="pane-head">
        <div class="pane-title" id="traceTitle">Trace</div>
        <div class="pane-sub" id="traceMeta"></div>
      </div>
      <div class="trace-scroll" id="tracePane"></div>
    </aside>
  </div>
</div>
<script id="payload" type="application/json">__DATA_JSON__</script>
<script>
const payload = JSON.parse(document.getElementById('payload').textContent);
const scenarios = payload.scenarios || [];
const state = { selectedScenarioId: null, selectedTurnIndex: 1 };
const $ = selector => document.querySelector(selector);
const $$ = selector => Array.from(document.querySelectorAll(selector));
const lower = value => String(value ?? '').toLowerCase();
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const countEntries = obj => Object.entries(obj || {}).sort((a,b) => b[1] - a[1]);

function pill(text, cls='') {
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}

function jsonBlock(obj) {
  return `<pre class="code">${esc(JSON.stringify(obj ?? {}, null, 2))}</pre>`;
}

function productImage(product, cls='') {
  if (!product?.image_url) {
    return `<div class="${cls ? `${cls} missing` : 'image-placeholder'}">No image</div>`;
  }
  return `<img class="${cls}" src="${esc(product.image_url)}" alt="${esc(product.title || 'Product image')}" loading="lazy">`;
}

function shortSource(path) {
  const text = String(path || '');
  const marker = '/scraper-infra/';
  const idx = text.indexOf(marker);
  return idx >= 0 ? `~${text.slice(idx)}` : text;
}

function optionList(id, counts) {
  const select = $(id);
  countEntries(counts).forEach(([name, count]) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = `${name} (${count})`;
    select.appendChild(option);
  });
}

function judgeState(turn) {
  const judges = Object.values(turn.judges || {});
  if (!judges.length) return 'none';
  return judges.some(judge => judge.classification && judge.classification !== 'ok') ? 'non_ok' : 'ok';
}

function turnMatches(turn, scenario) {
  const q = lower($('#search').value);
  const category = $('#category').value;
  const workflow = $('#workflow').value;
  const catalog = $('#catalog').value;
  const turnType = $('#turnType').value;
  const pass = $('#pass').value;
  const judge = $('#judge').value;
  if (category && scenario.category !== category) return false;
  if (workflow && turn.workflow !== workflow) return false;
  if (catalog && (turn.catalog_status?.label || 'unknown') !== catalog) return false;
  if (turnType === 'followup' && !turn.is_followup) return false;
  if (turnType === 'new' && turn.is_followup) return false;
  if (turnType === 'clarification' && !turn.needs_clarification) return false;
  if (turnType === 'result' && turn.needs_clarification) return false;
  if (pass === 'passed' && !turn.passed) return false;
  if (pass === 'failed' && turn.passed) return false;
  const js = judgeState(turn);
  if (judge === 'has' && js === 'none') return false;
  if (judge === 'non_ok' && js !== 'non_ok') return false;
  if (!q) return true;
  const haystack = [
    scenario.id, scenario.category, scenario.brand, scenario.description,
    turn.user, turn.workflow, turn.sql, turn.response_text,
    JSON.stringify(turn.intents), JSON.stringify(turn.catalog_status),
    JSON.stringify(turn.primary_products), JSON.stringify(turn.kg_context),
    JSON.stringify(turn.kg_trace), JSON.stringify(turn.judges),
  ].join(' ');
  return lower(haystack).includes(q);
}

function filteredScenarios() {
  return scenarios.map(scenario => {
    const matchedTurns = (scenario.turns || []).filter(turn => turnMatches(turn, scenario));
    return {...scenario, matchedTurns};
  }).filter(scenario => scenario.matchedTurns.length);
}

function selectedScenario() {
  return scenarios.find(scenario => scenario.id === state.selectedScenarioId) || scenarios[0] || null;
}

function selectedTurn(scenario) {
  if (!scenario) return null;
  return (scenario.turns || []).find(turn => turn.turn_index === state.selectedTurnIndex) || (scenario.turns || [])[0] || null;
}

function scenarioSummary(scenario) {
  const turns = scenario.turns || [];
  const failed = turns.some(turn => !turn.passed);
  const gapCount = turns.filter(turn => (turn.catalog_status?.label || '').includes('gap')).length;
  const judgeFindings = turns.some(turn => judgeState(turn) === 'non_ok');
  return [
    pill(scenario.category || 'category'),
    pill(`${turns.length} turn${turns.length === 1 ? '' : 's'}`),
    failed ? pill('failed', 'bad') : pill('passed', 'ok'),
    gapCount ? pill(`${gapCount} gap${gapCount === 1 ? '' : 's'}`, 'warn') : '',
    judgeFindings ? pill('judge finding', 'warn') : '',
  ].join('');
}

function turnPills(turn) {
  const catalog = turn.catalog_status?.label || 'unknown';
  const catalogCls = catalog === 'covered' ? 'ok' : (catalog.includes('gap') ? 'warn' : 'accent');
  return [
    pill(turn.workflow || 'workflow?', 'accent'),
    pill(catalog, catalogCls),
    turn.is_followup ? pill('follow-up') : pill('new query'),
    turn.needs_clarification ? pill('clarification', 'warn') : pill('result', 'ok'),
    turn.passed ? pill('passed', 'ok') : pill('failed', 'bad'),
  ].join('');
}

function renderShell() {
  const summary = payload.summary || {};
  $('#sourceLine').textContent = `${summary.scenarios || scenarios.length} scenarios · ${summary.turns || 0} turns · ${summary.visualized_at || ''}`;
  const metrics = [
    [`${summary.passed_scenarios ?? 0}/${summary.scenarios ?? scenarios.length}`, 'scenarios'],
    [`${summary.turns ?? 0}`, 'turns'],
    [`${(summary.catalog_status_counts || {}).covered || 0}`, 'covered'],
    [`${(summary.catalog_status_counts || {}).retrieval_gap || 0}`, 'retrieval gaps'],
  ];
  $('#summaryStrip').innerHTML = metrics.map(([value, label]) => `<div class="metric"><b>${esc(value)}</b><span>${esc(label)}</span></div>`).join('');
  optionList('#category', summary.category_counts);
  optionList('#workflow', summary.workflow_counts);
  optionList('#catalog', summary.catalog_status_counts);
}

function renderQueryList() {
  const list = filteredScenarios();
  if (!list.some(scenario => scenario.id === state.selectedScenarioId)) {
    state.selectedScenarioId = list[0]?.id || scenarios[0]?.id || null;
    state.selectedTurnIndex = list[0]?.matchedTurns?.[0]?.turn_index || 1;
  }
  $('#queryCount').textContent = `${list.length} matching flows`;
  $('#queryList').innerHTML = list.length ? list.map(scenario => {
    const firstTurn = scenario.matchedTurns[0] || scenario.turns[0] || {};
    const active = scenario.id === state.selectedScenarioId ? 'active' : '';
    return `<button class="query-item ${active}" data-scenario="${esc(scenario.id)}" data-turn="${esc(firstTurn.turn_index || 1)}">
      <div class="query-title">${esc(firstTurn.user || scenario.id)}</div>
      <div class="query-meta">${scenarioSummary(scenario)}</div>
      <div class="query-meta">${esc(scenario.id)}</div>
    </button>`;
  }).join('') : '<div class="empty">No flows match the filters.</div>';
  $$('.query-item').forEach(item => {
    item.addEventListener('click', () => {
      state.selectedScenarioId = item.dataset.scenario;
      state.selectedTurnIndex = Number(item.dataset.turn || 1);
      render();
    });
  });
}

function productMini(products) {
  if (!products?.length) return '';
  return `<div class="product-strip">${products.slice(0, 5).map(product => `
    <div class="mini-product">
      ${productImage(product)}
      <b title="${esc(product.title || product.id)}">${esc(product.title || product.id)}</b>
      <span>${esc(product.product_type || '')}${product.price ? ` · ${esc(product.price)}` : ''}</span>
    </div>
  `).join('')}</div>`;
}

function renderChat() {
  const scenario = selectedScenario();
  if (!scenario) {
    $('#chatPane').innerHTML = '<div class="empty">No conversation selected.</div>';
    return;
  }
  $('#chatTitle').textContent = scenario.id;
  $('#chatMeta').innerHTML = `${esc(scenario.description || '')} ${scenarioSummary(scenario)}`;
  $('#chatPane').innerHTML = (scenario.turns || []).map(turn => {
    const active = turn.turn_index === state.selectedTurnIndex ? 'active' : '';
    const assistantText = turn.needs_clarification
      ? (turn.clarifying_questions || []).join('\\n')
      : (turn.response_text || 'No response text logged.');
    return `<section class="turn-block ${active}" data-turn="${esc(turn.turn_index)}">
      <div class="bubble user">
        <div class="bubble-label">User · turn ${esc(turn.turn_index)}</div>
        <div class="bubble-text">${esc(turn.user)}</div>
      </div>
      <div class="bubble assistant">
        <div class="bubble-label">${turn.needs_clarification ? 'Clarifying question' : 'Assistant response'}</div>
        <div class="bubble-text">${esc(assistantText)}</div>
        ${productMini(turn.primary_products)}
      </div>
      <div class="pill-row">${turnPills(turn)}</div>
    </section>`;
  }).join('');
  $$('.turn-block').forEach(block => {
    block.addEventListener('click', () => {
      state.selectedTurnIndex = Number(block.dataset.turn || 1);
      render();
    });
  });
}

function intentChips(intents) {
  const entries = Object.entries(intents || {});
  if (!entries.length) return '<div class="empty">No intents logged.</div>';
  return `<div class="intent-grid">${entries.map(([key,value]) => `<span class="intent-chip"><b>${esc(key)}</b>: ${esc(Array.isArray(value) ? value.join(', ') : value)}</span>`).join('')}</div>`;
}

function kgRows(turn) {
  const summary = turn.kg_trace?.result_summary || turn.kg_context || {};
  const rows = Object.entries(summary).filter(([, value]) => value && typeof value === 'object');
  if (!rows.length) return '<div class="empty">No KG summary logged.</div>';
  return rows.map(([tag, groups]) => {
    const chips = ['recommended', 'acceptable', 'avoid'].map(group => {
      const values = groups[group] || [];
      if (!values.length) return '';
      const cls = group === 'avoid' ? 'bad' : (group === 'acceptable' ? '' : 'ok');
      return `<div class="chip-line">${pill(group, cls)}${values.slice(0, 12).map(value => pill(value, cls)).join('')}</div>`;
    }).join('');
    return `<div class="kg-row"><b>${esc(tag)}</b><div>${chips}</div></div>`;
  }).join('');
}

function lookupRows(turn) {
  const keys = turn.kg_trace?.lookup_keys || [];
  if (!keys.length) return '<div class="empty">No KG lookup keys logged.</div>';
  return `<div class="kv-grid">${keys.slice(0, 12).map(key => `
    <b>${esc(key.entity || 'entity')}</b>
    <span>${esc(key.value)} → ${esc(key.lookup_value || key.resolved_value || '')} · ${esc(key.matched_count ?? 0)} matches${key.expanded ? ' · expanded' : ''}</span>
  `).join('')}</div>`;
}

function productRows(products) {
  if (!products?.length) return '<div class="empty">No primary products logged.</div>';
  return `<div class="product-table">${products.slice(0, 8).map(product => `
    <div class="product-row">
      ${productImage(product, 'product-thumb')}
      <div>
        ${product.url ? `<a href="${esc(product.url)}" target="_blank" rel="noreferrer"><b>${esc(product.title || product.id)}</b></a>` : `<b>${esc(product.title || product.id)}</b>`}
        <div class="product-meta">
          ${pill(product.product_type || 'type')}
          ${product.colors ? pill(product.colors) : ''}
          ${product.price ? pill(product.price) : ''}
        </div>
        ${product.image_source ? `<div class="pane-sub">image source: ${esc(shortSource(product.image_source))}</div>` : ''}
      </div>
    </div>
  `).join('')}</div>`;
}

function judgeBlocks(turn) {
  const entries = Object.entries(turn.judges || {});
  if (!entries.length) return '<div class="empty">No judge output attached to this turn.</div>';
  return entries.map(([name, judge]) => {
    const cls = judge.classification === 'ok' ? '' : (Number(judge.score) <= 1 ? 'bad' : 'warn');
    return `<div class="judge ${cls}">
      <div class="pill-row">${pill(name, 'accent')}${pill(judge.classification || 'unknown', cls || 'ok')}${pill(`score ${judge.score ?? '?'}`)}</div>
      <div style="margin-top:7px">${esc(judge.rationale || '')}</div>
      ${(judge.labels || []).length ? `<div class="pane-sub">${esc(judge.labels.join(', '))}</div>` : ''}
    </div>`;
  }).join('');
}

function renderTrace() {
  const scenario = selectedScenario();
  const turn = selectedTurn(scenario);
  if (!scenario || !turn) {
    $('#tracePane').innerHTML = '<div class="empty">No trace selected.</div>';
    return;
  }
  const rt = turn.runtime_trace || {};
  $('#traceTitle').textContent = `${scenario.id} · turn ${turn.turn_index}`;
  $('#traceMeta').innerHTML = turnPills(turn);
  $('#tracePane').innerHTML = `
    <section class="section">
      <h2>Decision</h2>
      <div class="section-body">
        <div class="kv-grid">
          <b>query</b><span>${esc(turn.user)}</span>
          <b>workflow</b><span>${esc(turn.workflow)} · ${esc(rt.router?.reason || '')}</span>
          <b>follow-up</b><span>${esc(turn.is_followup)} · ${esc(rt.followup_reason || '')}</span>
          <b>merge mode</b><span>${esc(rt.merge_mode || '')}</span>
          <b>clarification</b><span>${esc(turn.needs_clarification)} ${(turn.clarifying_questions || []).map(q => pill(q, 'warn')).join('')}</span>
          <b>catalog status</b><span>${esc(turn.catalog_status?.label || 'unknown')} · ${esc(turn.catalog_status?.reason || '')}</span>
          <b>products</b><span>DB ${esc(turn.db_product_count)} · primary ${esc(turn.primary_product_count)}/${esc(turn.unique_primary_product_count)}</span>
          <b>elapsed</b><span>${esc(turn.elapsed_ms)}ms</span>
        </div>
      </div>
    </section>
    <section class="section">
      <h2>Final Intents</h2>
      <div class="section-body">${intentChips(turn.intents)}<details><summary>Intent diff</summary>${jsonBlock(rt.intent_diff || {})}</details></div>
    </section>
    <section class="section">
      <h2>KG Grounding</h2>
      <div class="section-body">${kgRows(turn)}<details open><summary>Lookup provenance</summary>${lookupRows(turn)}</details><details><summary>KG conflicts and misses</summary>${jsonBlock({missing_keys: turn.kg_trace?.missing_keys || [], conflicts: turn.kg_trace?.conflicts || []})}</details></div>
    </section>
    <section class="section">
      <h2>SQL / DB</h2>
      <div class="section-body">
        ${(turn.db_errors || []).length ? `<div class="pill-row">${turn.db_errors.map(error => pill(error, 'warn')).join('')}</div>` : ''}
        <pre class="code">${esc(turn.sql || 'No SQL logged.')}</pre>
        <details><summary>DB trace</summary>${jsonBlock(turn.db_trace || {})}</details>
      </div>
    </section>
    <section class="section">
      <h2>Products</h2>
      <div class="section-body">${productRows(turn.primary_products)}<details><summary>Outfit scoring</summary>${jsonBlock(turn.outfit_debug || {})}</details></div>
    </section>
    <section class="section">
      <h2>Judges</h2>
      <div class="section-body">${judgeBlocks(turn)}</div>
    </section>
  `;
}

function render() {
  renderQueryList();
  renderChat();
  renderTrace();
}

function bindControls() {
  ['#search','#category','#workflow','#catalog','#turnType','#pass','#judge'].forEach(id => {
    $(id).addEventListener('input', () => {
      state.selectedScenarioId = null;
      state.selectedTurnIndex = 1;
      render();
    });
  });
  $('#reset').addEventListener('click', () => {
    ['#search','#category','#workflow','#catalog','#turnType','#pass','#judge'].forEach(id => { $(id).value = ''; });
    state.selectedScenarioId = null;
    state.selectedTurnIndex = 1;
    render();
  });
}

renderShell();
bindControls();
render();
</script>
</body>
</html>"""
    return (
        template.replace("__TITLE__", title_html)
        .replace("__DATA_JSON__", data_json)
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", default=str(DEFAULT_EVAL), help="Main conversation eval JSON artifact.")
    parser.add_argument("--judge", action="append", default=[], help="Optional judge eval artifact to merge by case id. Repeatable.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output HTML path.")
    parser.add_argument("--title", default="Aza 500 Conversation Eval Visualizer")
    args = parser.parse_args()

    main_path = Path(args.eval)
    result = load_json(main_path)
    judge_results = [load_json(Path(path)) for path in args.judge]
    attach_judges(result, judge_results)

    source_paths = [str(main_path)] + [str(Path(path)) for path in args.judge]
    payload = build_payload(result, source_paths)
    html_text = render_html(payload, args.title)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text)
    print(f"Wrote {out.resolve()}")
    print(
        f"Visualizer data: {payload['summary'].get('scenarios', len(payload['scenarios']))} scenarios, "
        f"{payload['summary'].get('turns', len(payload['turns']))} turns"
    )


if __name__ == "__main__":
    main()
