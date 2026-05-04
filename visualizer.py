"""Generate an interactive HTML visualization of the Knowledge Graph pipeline.

Run: python3 visualizer.py
Opens the visualizer in your browser with live data from the pipeline.
"""

import json
import webbrowser
import os
import re
import time
from collections import defaultdict
from knowledge_graph import KnowledgeGraph, INTENT_ALIASES
from brand_adapters import load_catalog, NormalizedProduct
from product_matcher import score_product, _product_type_matches
from prompts import RECOMMENDATION_SYSTEM, RECOMMENDATION_USER, INTENT_EXTRACTION_SYSTEM, INTENT_EXTRACTION_USER
from llm_client import call_llm

KG_PATH = "Master_Graph.xlsx"
CATALOGS = {
    "masaba": "/Users/aant/repos/scraper-infra/data/house_of_masaba_products.json",
    "kalki": "/Users/aant/repos/scraper-infra/data/kalki_fashion_products.json",
    "aza": "/Users/aant/repos/scraper-infra/data/aza_fashions_products.json",
}

DEMO_QUERIES = [
    # Masaba queries
    {"query": "What to wear to a restaurant in cloudy weather?", "intents": {"place": "restaurant", "weather": "cloudy"}, "brand": "masaba"},
    {"query": "Outfit ideas for Ganesh Chaturthi in Delhi", "intents": {"event": "Ganesh Chaturthi", "location": "Delhi"}, "brand": "masaba"},
    {"query": "What to wear to garden if I practice Islam?", "intents": {"place": "garden", "religion": "Islam"}, "brand": "masaba"},
    {"query": "I'm petite — what works for bachelorette?", "intents": {"occasion": "bachelorette", "bodytype": "petite"}, "brand": "masaba"},
    # Kalki queries
    {"query": "Need a premium outfit for prom — I'm apple shaped", "intents": {"occasion": "prom", "bodytype": "apple shaped", "budget": "premium"}, "brand": "kalki"},
    {"query": "Lehenga for engagement in January", "intents": {"occasion": "engagement", "month": "January"}, "brand": "kalki"},
    {"query": "What to wear to a Hinduism sangeet?", "intents": {"occasion": "sangeet", "religion": "Hinduism"}, "brand": "kalki"},
    {"query": "Designer saree for my sister's Indian wedding", "intents": {"occasion": "Indian wedding", "relation": "sister"}, "brand": "kalki"},
    # Aza queries
    {"query": "Designer outfit for theater in windy weather", "intents": {"place": "theater", "weather": "windy"}, "brand": "aza"},
    {"query": "Heavy embroidered outfit for mehendi", "intents": {"occasion": "mehendi"}, "brand": "aza"},
    {"query": "What can an accountant wear to an Indian wedding?", "intents": {"profession": "accountant", "occasion": "Indian wedding"}, "brand": "aza"},
    {"query": "Suggest a high-end outfit for my mom's graduation", "intents": {"occasion": "graduation", "relation": "mom", "budget": "high-end"}, "brand": "aza"},
]


def build_trace_data(query, intents, brand="masaba", run_llm=False, kg=None, catalog_products=None):
    """Build structured trace data for visualization."""
    if kg is None:
        kg = KnowledgeGraph(KG_PATH)
    if catalog_products is None:
        catalog_path = CATALOGS.get(brand)
        max_products = 50000 if brand == "aza" else None
        adapter = load_catalog(brand, catalog_path, max_products=max_products)
        catalog_products = adapter.products

    products = catalog_products

    trace = {
        "query": query,
        "brand": brand,
        "intents": intents,
    }

    # Stage 1: LLM Intent Extraction
    trace["intent_extraction"] = {
        "query": query,
        "llm_intents": None,
        "preextracted_intents": intents,
        "llm_prompt": INTENT_EXTRACTION_USER.format(query=query),
        "llm_system_prompt_excerpt": "You are an intent extraction system for a fashion recommendation engine. Given a user query about clothing/fashion, extract structured intents as key-value pairs. Valid entity types: place, occasion, activity, event, weather, bodytype, health, profession, agegroup, relation, religion, complexion, budget, month, time, location...",
        "elapsed_ms": 0,
    }

    if run_llm:
        try:
            print(f"    Extracting intents via LLM...")
            start = time.time()
            raw = call_llm(
                INTENT_EXTRACTION_USER.format(query=query),
                system_prompt=INTENT_EXTRACTION_SYSTEM,
                timeout=180,
            )
            elapsed = (time.time() - start) * 1000
            trace["intent_extraction"]["elapsed_ms"] = round(elapsed)
            trace["intent_extraction"]["llm_raw_response"] = raw
            json_match = re.search(r'\{[^{}]*\}', raw)
            if json_match:
                trace["intent_extraction"]["llm_intents"] = json.loads(json_match.group())
        except Exception as e:
            trace["intent_extraction"]["llm_raw_response"] = f"ERROR: {e}"

    # Stage 2: Alias resolution
    resolved = {}
    alias_log = []
    skipped = {}
    for entity, value in intents.items():
        if entity in ("location", "month", "budget", "time"):
            skipped[entity] = value
            continue
        aliases = INTENT_ALIASES.get(entity, {})
        resolved_value = aliases.get(value, value)
        key = (entity, resolved_value)
        found = key in kg.graph
        entry_count = len(kg.graph.get(key, []))
        alias_log.append({
            "entity": entity,
            "original": value,
            "resolved": resolved_value,
            "aliased": resolved_value != value,
            "found": found,
            "entry_count": entry_count,
        })
        resolved[entity] = resolved_value

    trace["alias_resolution"] = alias_log
    trace["skipped_intents"] = skipped

    # Stage 3: Per-intent KG lookup
    per_intent = []
    for entity, value in resolved.items():
        key = (entity, value)
        entries = kg.graph.get(key, [])
        by_tag = defaultdict(lambda: {"recommended": [], "acceptable": [], "avoid": []})
        for e in entries:
            bucket = "recommended" if e["rank"] == 1 else "acceptable" if e["rank"] == 2 else "avoid"
            by_tag[e["tag"]][bucket].append({"name": e["name"], "gender": e["gender"] or "any"})
        per_intent.append({
            "entity": entity,
            "value": value,
            "entry_count": len(entries),
            "tags": {k: dict(v) for k, v in by_tag.items()},
        })
    trace["per_intent_lookup"] = per_intent

    # Stage 4: Merged context with conflict detection
    all_items = defaultdict(lambda: defaultdict(list))
    for item in per_intent:
        for tag, data in item["tags"].items():
            for rank_type, rank_val in [("recommended", 1), ("acceptable", 2), ("avoid", -1)]:
                for entry in data.get(rank_type, []):
                    all_items[tag][entry["name"]].append({
                        "rank": rank_val,
                        "source": f"{item['entity']}:{item['value']}",
                    })

    conflicts = []
    for tag, names in all_items.items():
        for name, sources in names.items():
            ranks = [s["rank"] for s in sources]
            if len(sources) > 1 and len(set(ranks)) > 1:
                conflicts.append({
                    "tag": tag,
                    "name": name,
                    "sources": sources,
                    "resolved_rank": -1 if -1 in ranks else max(ranks),
                })
    trace["conflicts"] = conflicts

    kg_result = kg.lookup(intents)
    trace["merged_context"] = {tag: data for tag, data in kg_result.items()}

    # Stage 5: Product scoring
    gender = None
    relation = intents.get("relation", "")
    if relation in ("mom", "sister", "niece", "aunt", "grandmother"):
        gender = "female"
    elif relation in ("dad", "brother", "nephew", "uncle", "grandfather"):
        gender = "male"

    trace["gender_filter"] = gender

    scored_products = []
    for p in products:
        if gender and p.gender not in (gender, "both"):
            continue
        s = score_product(p, kg_result)
        if s > 0:
            # Build score breakdown
            breakdown = []
            products_data = kg_result.get("product", {})
            for rp in products_data.get("recommended", []):
                if _product_type_matches(p.product_type, rp):
                    breakdown.append({"attr": "product_type", "value": p.product_type, "points": 10, "reason": f"recommended '{rp}'"})
                    break
            else:
                for ap in products_data.get("acceptable", []):
                    if _product_type_matches(p.product_type, ap):
                        breakdown.append({"attr": "product_type", "value": p.product_type, "points": 5, "reason": f"acceptable '{ap}'"})
                        break

            for tag_name, attr_list in [("colour", p.colors), ("pattern", p.patterns), ("material", p.materials)]:
                data = kg_result.get(tag_name, {})
                rec = data.get("recommended", [])
                avd = data.get("avoid", [])
                for val in attr_list:
                    if val in rec or "all" in rec:
                        breakdown.append({"attr": tag_name, "value": val, "points": 3, "reason": "recommended"})
                    if val in avd:
                        breakdown.append({"attr": tag_name, "value": val, "points": -5, "reason": "AVOID"})

            scored_products.append({
                "id": p.id,
                "title": p.title,
                "product_type": p.product_type,
                "colors": p.colors,
                "patterns": p.patterns,
                "materials": p.materials,
                "price": p.price,
                "score": s,
                "breakdown": breakdown,
            })

    scored_products.sort(key=lambda x: -x["score"])
    trace["scored_products"] = scored_products[:30]
    trace["total_scored"] = len(scored_products)
    trace["total_catalog"] = len(products)

    # Stage 6: KG context string + actual LLM call
    kg_context_str = kg.format_context(kg_result)
    trace["kg_context_string"] = kg_context_str

    # Stage 5: LLM generates SQL query
    trace["db_query"] = {
        "sql": None,
        "raw_llm_response": None,
        "sql_gen_ms": 0,
        "sql_exec_ms": 0,
        "product_count": 0,
        "products": [],
        "errors": [],
    }

    # Stage 6: LLM final recommendation
    trace["llm_system_prompt"] = ""
    trace["llm_user_prompt"] = ""
    trace["llm_recommendations"] = []
    trace["llm_raw_response"] = ""
    trace["llm_elapsed_ms"] = 0

    if run_llm:
        from db_query import query_products, DB_PATHS, get_available_types, SQL_GENERATION_SYSTEM, SQL_GENERATION_USER
        import os

        db_path = DB_PATHS.get(brand)
        if db_path and os.path.exists(db_path):
            # Stage 5: Generate + execute SQL
            print(f"    Generating SQL query via LLM...")
            db_result = query_products(query, intents, kg_context_str, brand)
            trace["db_query"]["sql"] = db_result.get("sql")
            trace["db_query"]["raw_llm_response"] = db_result.get("raw_llm_sql_response")
            trace["db_query"]["sql_gen_ms"] = db_result["timings"].get("sql_generation_ms", 0)
            trace["db_query"]["sql_exec_ms"] = db_result["timings"].get("sql_execution_ms", 0)
            trace["db_query"]["product_count"] = db_result["product_count"]
            trace["db_query"]["errors"] = db_result.get("errors", [])
            trace["db_query"]["available_types"] = db_result.get("available_types", [])

            # Simplify product data for the trace
            db_products = []
            for p in db_result["products"]:
                db_products.append({
                    "id": p["id"],
                    "title": p["title"],
                    "product_type": p["product_type"],
                    "colors": p.get("colors", ""),
                    "patterns": p.get("patterns", ""),
                    "materials": p.get("materials", ""),
                    "price": p.get("price", 0),
                })
            trace["db_query"]["products"] = db_products

            # Stage 6: LLM ranks the DB results
            if db_products:
                products_json = json.dumps(db_products, indent=2)
                intents_str = json.dumps(intents, indent=2)
                system_prompt = RECOMMENDATION_SYSTEM.format(brand_name=brand)
                user_prompt = RECOMMENDATION_USER.format(
                    query=query,
                    intents=intents_str,
                    kg_context=kg_context_str,
                    count=len(db_products),
                    brand_name=brand,
                    products_json=products_json,
                )
                trace["llm_system_prompt"] = system_prompt
                trace["llm_user_prompt"] = user_prompt

                try:
                    print(f"    LLM ranking products...")
                    start = time.time()
                    raw = call_llm(user_prompt, system_prompt=system_prompt, timeout=180)
                    elapsed = (time.time() - start) * 1000
                    trace["llm_elapsed_ms"] = round(elapsed)
                    trace["llm_raw_response"] = raw
                    json_match = re.search(r'\[.*\]', raw, re.DOTALL)
                    if json_match:
                        trace["llm_recommendations"] = json.loads(json_match.group())
                except Exception as e:
                    trace["llm_raw_response"] = f"ERROR: {e}"

    return trace


def generate_html(traces):
    """Generate the interactive HTML visualization."""
    traces_json = json.dumps(traces, indent=2, default=str)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Knowledge Graph Pipeline Visualizer</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Inter', -apple-system, sans-serif; background: #0a0e17; color: #e0e6ed; line-height: 1.6; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
h1 {{ text-align: center; font-size: 1.8em; margin: 20px 0; color: #60a5fa; }}
h2 {{ font-size: 1.3em; margin-bottom: 12px; color: #93c5fd; }}
h3 {{ font-size: 1.1em; margin-bottom: 8px; color: #bfdbfe; }}

.query-selector {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
.query-selector label {{ display: block; margin-bottom: 8px; color: #94a3b8; font-size: 0.9em; }}
.query-selector select {{ width: 100%; padding: 10px 14px; background: #0f172a; border: 1px solid #334155; color: #e2e8f0; border-radius: 8px; font-size: 1em; cursor: pointer; }}
.query-selector select:focus {{ outline: none; border-color: #60a5fa; }}

.pipeline {{ display: flex; flex-direction: column; gap: 0; }}
.stage {{ background: #1e293b; border-radius: 12px; padding: 20px; position: relative; }}
.stage-connector {{ display: flex; justify-content: center; padding: 8px 0; }}
.stage-connector .arrow {{ width: 2px; height: 30px; background: linear-gradient(to bottom, #334155, #60a5fa); position: relative; }}
.stage-connector .arrow::after {{ content: '▼'; position: absolute; bottom: -8px; left: -6px; color: #60a5fa; font-size: 12px; }}

.stage-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 16px; cursor: pointer; }}
.stage-number {{ background: #3b82f6; color: white; width: 28px; height: 28px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 0.85em; flex-shrink: 0; }}
.stage-title {{ font-size: 1.15em; font-weight: 600; color: #93c5fd; }}
.stage-toggle {{ margin-left: auto; color: #64748b; font-size: 1.2em; transition: transform 0.2s; }}
.stage-toggle.open {{ transform: rotate(180deg); }}
.stage-body {{ overflow: hidden; transition: max-height 0.3s ease; }}

.intent-badge {{ display: inline-flex; align-items: center; gap: 6px; background: #0f172a; border: 1px solid #334155; border-radius: 20px; padding: 4px 12px; margin: 3px; font-size: 0.85em; }}
.intent-key {{ color: #94a3b8; }}
.intent-value {{ color: #60a5fa; font-weight: 500; }}
.intent-skipped {{ opacity: 0.5; border-style: dashed; }}
.intent-aliased {{ border-color: #f59e0b; }}
.intent-aliased .intent-value {{ color: #f59e0b; }}
.alias-arrow {{ color: #f59e0b; }}

.tag-group {{ margin: 12px 0; }}
.tag-name {{ font-weight: 600; color: #e2e8f0; font-size: 0.9em; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }}
.rank-row {{ display: flex; align-items: center; gap: 8px; margin: 3px 0; padding: 4px 10px; border-radius: 6px; font-size: 0.85em; }}
.rank-recommended {{ background: rgba(34, 197, 94, 0.1); border-left: 3px solid #22c55e; }}
.rank-acceptable {{ background: rgba(234, 179, 8, 0.1); border-left: 3px solid #eab308; }}
.rank-avoid {{ background: rgba(239, 68, 68, 0.1); border-left: 3px solid #ef4444; }}
.rank-label {{ font-weight: 500; min-width: 90px; }}
.rank-label.rec {{ color: #22c55e; }}
.rank-label.acc {{ color: #eab308; }}
.rank-label.avd {{ color: #ef4444; }}

.intent-section {{ background: #0f172a; border-radius: 8px; padding: 14px; margin: 10px 0; border: 1px solid #1e293b; }}
.intent-section-header {{ font-weight: 600; color: #93c5fd; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }}
.intent-section-header .count {{ background: #334155; color: #94a3b8; padding: 1px 8px; border-radius: 10px; font-size: 0.8em; }}

.conflict-card {{ background: #1c1917; border: 1px solid #78350f; border-radius: 8px; padding: 12px; margin: 8px 0; }}
.conflict-name {{ color: #fbbf24; font-weight: 600; }}
.conflict-source {{ font-size: 0.85em; color: #a1a1aa; margin: 2px 0; }}
.conflict-resolved {{ color: #22c55e; font-weight: 500; margin-top: 4px; }}

.product-card {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 14px; margin: 8px 0; transition: border-color 0.2s; }}
.product-card:hover {{ border-color: #3b82f6; }}
.product-header {{ display: flex; justify-content: space-between; align-items: center; }}
.product-title {{ font-weight: 600; color: #e2e8f0; }}
.product-score {{ background: #3b82f6; color: white; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 0.9em; }}
.product-meta {{ display: flex; gap: 12px; margin-top: 6px; font-size: 0.82em; color: #64748b; }}
.product-meta span {{ display: flex; align-items: center; gap: 4px; }}
.breakdown {{ margin-top: 8px; }}
.breakdown-item {{ display: inline-flex; align-items: center; gap: 4px; margin: 2px; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; }}
.breakdown-positive {{ background: rgba(34, 197, 94, 0.15); color: #4ade80; }}
.breakdown-negative {{ background: rgba(239, 68, 68, 0.15); color: #f87171; }}

.context-block {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 14px; margin: 10px 0; font-family: 'JetBrains Mono', monospace; font-size: 0.85em; white-space: pre-wrap; color: #94a3b8; line-height: 1.8; }}
.context-line-rec {{ color: #4ade80; }}
.context-line-acc {{ color: #eab308; }}
.context-line-avd {{ color: #f87171; }}

.flow-diagram {{ display: flex; align-items: center; justify-content: center; gap: 6px; margin: 20px 0; flex-wrap: wrap; }}
.flow-box {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 8px 16px; font-size: 0.85em; text-align: center; }}
.flow-box.active {{ border-color: #3b82f6; background: rgba(59, 130, 246, 0.1); }}
.flow-arrow {{ color: #475569; font-size: 1.2em; }}

.no-conflicts {{ color: #22c55e; font-style: italic; padding: 10px; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 12px 0; }}
.stat {{ background: #0f172a; border-radius: 8px; padding: 12px; text-align: center; }}
.stat-value {{ font-size: 1.5em; font-weight: 700; color: #60a5fa; }}
.stat-label {{ font-size: 0.8em; color: #64748b; margin-top: 4px; }}

.rec-card {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 16px; margin: 10px 0; }}
.rec-card:hover {{ border-color: #22c55e; }}
.rec-rank {{ display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 50%; background: #22c55e; color: #0f172a; font-weight: 800; font-size: 0.85em; margin-right: 10px; flex-shrink: 0; }}
.rec-header {{ display: flex; align-items: center; }}
.rec-title {{ font-weight: 600; color: #e2e8f0; flex: 1; }}
.rec-llm-score {{ background: #22c55e; color: #0f172a; padding: 2px 10px; border-radius: 12px; font-weight: 700; font-size: 0.85em; }}
.rec-reasoning {{ margin-top: 8px; padding: 10px 12px; background: rgba(34, 197, 94, 0.05); border-left: 3px solid #22c55e; border-radius: 0 6px 6px 0; color: #94a3b8; font-size: 0.88em; line-height: 1.5; }}

.prompt-section {{ margin: 12px 0; }}
.prompt-label {{ color: #93c5fd; font-weight: 600; margin-bottom: 6px; cursor: pointer; display: flex; align-items: center; gap: 6px; }}
.prompt-label:hover {{ color: #bfdbfe; }}
.prompt-content {{ background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 14px; font-family: 'JetBrains Mono', monospace; font-size: 0.78em; white-space: pre-wrap; color: #64748b; max-height: 300px; overflow-y: auto; line-height: 1.6; }}
.prompt-content.collapsed {{ max-height: 0; padding: 0 14px; overflow: hidden; border: none; }}
.llm-timing {{ color: #64748b; font-size: 0.82em; margin-top: 6px; }}
.no-llm-msg {{ padding: 16px; text-align: center; color: #64748b; border: 1px dashed #334155; border-radius: 8px; margin: 12px 0; }}
</style>
</head>
<body>
<div class="container">
<h1>Knowledge Graph Pipeline Visualizer</h1>

<div class="flow-diagram">
  <div class="flow-box" id="fb1">User Query</div>
  <span class="flow-arrow">→</span>
  <div class="flow-box" id="fb2">Intent Extraction</div>
  <span class="flow-arrow">→</span>
  <div class="flow-box" id="fb3">Alias Resolution</div>
  <span class="flow-arrow">→</span>
  <div class="flow-box" id="fb4">KG Lookup</div>
  <span class="flow-arrow">→</span>
  <div class="flow-box" id="fb5">Merge & Conflicts</div>
  <span class="flow-arrow">→</span>
  <div class="flow-box" id="fb6">Product Scoring</div>
  <span class="flow-arrow">→</span>
  <div class="flow-box" id="fb7">LLM Recommendation</div>
</div>

<div class="query-selector">
  <label>Select a query to trace:</label>
  <select id="querySelect" onchange="renderTrace()"></select>
</div>

<div id="pipeline" class="pipeline"></div>
</div>

<script>
const TRACES = {traces_json};

const querySelect = document.getElementById('querySelect');
TRACES.forEach((t, i) => {{
  const opt = document.createElement('option');
  opt.value = i;
  opt.textContent = `[${{t.brand.toUpperCase()}}] ${{t.query}}`;
  querySelect.appendChild(opt);
}});

function renderTrace() {{
  const trace = TRACES[querySelect.value];
  const pipeline = document.getElementById('pipeline');

  // Highlight active flow box
  document.querySelectorAll('.flow-box').forEach((b, i) => {{
    b.classList.add('active');
  }});

  let html = '';

  // STAGE 1: User Query → LLM Intent Extraction
  const ie = trace.intent_extraction || {{}};
  let stage1Html = `
    <div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
      <div style="flex:1;min-width:300px">
        <h3>User enters a query on ${{trace.brand.charAt(0).toUpperCase() + trace.brand.slice(1)}}'s website</h3>
        <div style="font-size:1.2em;color:#e2e8f0;margin:12px 0;padding:14px;background:#0f172a;border-radius:8px;border:1px solid #334155">"${{trace.query}}"</div>
        <p style="color:#64748b;font-size:0.85em">This free-text query needs to be parsed into structured intents that can be looked up in the Knowledge Graph.</p>
      </div>
      <div style="display:flex;align-items:center;padding:0 16px;color:#475569;font-size:2em">→</div>
      <div style="flex:1;min-width:300px">
        <h3>LLM parses query into intents</h3>
        <p style="color:#64748b;font-size:0.85em;margin-bottom:8px">The LLM is given a system prompt with all valid entity types and values. It extracts structured key-value pairs:</p>
  `;

  // Show LLM-extracted intents if available
  if (ie.llm_intents) {{
    stage1Html += `<div style="margin:8px 0">
      <div style="color:#22c55e;font-size:0.8em;margin-bottom:4px">LLM extracted (${{(ie.elapsed_ms/1000).toFixed(1)}}s):</div>
      ${{Object.entries(ie.llm_intents).map(([k,v]) => `<span class="intent-badge"><span class="intent-key">${{k}}:</span><span class="intent-value">${{v}}</span></span>`).join('')}}
    </div>`;
  }}

  // Always show pre-extracted intents
  stage1Html += `<div style="margin:8px 0">
    <div style="color:#94a3b8;font-size:0.8em;margin-bottom:4px">${{ie.llm_intents ? 'Pre-extracted (ground truth):' : 'Extracted intents:'}}</div>
    ${{Object.entries(trace.intents).map(([k,v]) => `<span class="intent-badge"><span class="intent-key">${{k}}:</span><span class="intent-value">${{v}}</span></span>`).join('')}}
  </div>`;

  stage1Html += `</div></div>`;

  // Show what the LLM prompt looks like (expandable)
  const iePromptId = 'ie_prompt_' + querySelect.value;
  stage1Html += `<div class="prompt-section" style="margin-top:12px">
    <div class="prompt-label" onclick="document.getElementById('${{iePromptId}}').classList.toggle('collapsed')">&#9654; Intent extraction prompt sent to LLM (click to expand)</div>
    <div class="prompt-content collapsed" id="${{iePromptId}}">${{escapeHtml(ie.llm_system_prompt_excerpt || '')}}\n\nUser message:\n${{escapeHtml(ie.llm_prompt || '')}}</div>
  </div>`;

  html += stage(1, 'User Query → LLM Intent Extraction', stage1Html);

  // STAGE 2: Alias Resolution
  const aliasRows = trace.alias_resolution.map(a => {{
    let cls = a.aliased ? 'intent-aliased' : '';
    let badge = `<span class="intent-badge ${{cls}}">
      <span class="intent-key">${{a.entity}}:</span>
      <span class="intent-value">${{a.original}}</span>
      ${{a.aliased ? `<span class="alias-arrow">→ ${{a.resolved}}</span>` : ''}}
    </span>`;
    let status = a.found
      ? `<span style="color:#22c55e">✓ Found (${{a.entry_count}} entries)</span>`
      : `<span style="color:#ef4444">✗ Not found</span>`;
    return `<div style="display:flex;align-items:center;gap:12px;margin:6px 0">${{badge}} ${{status}}</div>`;
  }}).join('');

  const skippedHtml = Object.keys(trace.skipped_intents).length > 0
    ? `<div style="margin-top:10px;color:#64748b;font-size:0.85em">
        Skipped (not in KG): ${{Object.entries(trace.skipped_intents).map(([k,v]) =>
          `<span class="intent-badge intent-skipped"><span class="intent-key">${{k}}:</span><span class="intent-value">${{v}}</span></span>`
        ).join('')}}<br><em>These are used by the LLM but not for KG lookup</em>
      </div>` : '';

  html += stage(2, 'Alias Resolution & KG Validation', aliasRows + skippedHtml);

  // STAGE 3: Per-Intent KG Lookup — show graph traversal
  let lookupHtml = `<p style="color:#94a3b8;font-size:0.88em;margin-bottom:14px">
    The Knowledge Graph is a dictionary keyed by <code style="color:#60a5fa">(entity, entity_value)</code>.
    For each intent, we look up <code style="color:#60a5fa">graph[(entity, value)]</code> and get back a list of
    clothing attributes with ranks (1=recommended, 2=acceptable, -1=avoid) and gender tags.
  </p>`;

  trace.per_intent_lookup.forEach(item => {{
    lookupHtml += `<div class="intent-section">
      <div class="intent-section-header" style="font-size:1em">
        <span style="color:#60a5fa;font-family:monospace">graph[("${{item.entity}}", "${{item.value}}")]</span>
        <span class="count">→ ${{item.entry_count}} rows returned</span>
      </div>
      <p style="color:#64748b;font-size:0.82em;margin-bottom:8px">Each row in the graph says: "For this ${{item.entity}}, this clothing attribute has this rank."</p>`;

    Object.entries(item.tags).sort().forEach(([tag, data]) => {{
      lookupHtml += `<div class="tag-group"><div class="tag-name">${{tag}}</div>`;
      if (data.recommended?.length) {{
        lookupHtml += `<div class="rank-row rank-recommended"><span class="rank-label rec">rank = 1</span>${{data.recommended.map(e => e.name + ' (' + e.gender + ')').join(', ')}}</div>`;
      }}
      if (data.acceptable?.length) {{
        lookupHtml += `<div class="rank-row rank-acceptable"><span class="rank-label acc">rank = 2</span>${{data.acceptable.map(e => e.name + ' (' + e.gender + ')').join(', ')}}</div>`;
      }}
      if (data.avoid?.length) {{
        lookupHtml += `<div class="rank-row rank-avoid"><span class="rank-label avd">rank = -1</span>${{data.avoid.map(e => e.name + ' (' + e.gender + ')').join(', ')}}</div>`;
      }}
      lookupHtml += `</div>`;
    }});
    lookupHtml += `</div>`;
  }});
  html += stage(3, 'Knowledge Graph Traversal', lookupHtml);

  // STAGE 4: Merge & Conflicts
  let conflictHtml = '';
  if (trace.conflicts.length > 0) {{
    conflictHtml += `<p style="color:#fbbf24;margin-bottom:10px">Rule: If <strong>any</strong> intent says avoid (rank=-1), the item is avoided (veto). Otherwise take the <strong>best</strong> rank — if one intent recommends it (rank=1), it stays recommended even if another intent only finds it acceptable (rank=2).</p>`;
    trace.conflicts.forEach(c => {{
      conflictHtml += `<div class="conflict-card">
        <div class="conflict-name">[${{c.tag}}] "${{c.name}}"</div>
        ${{c.sources.map(s => `<div class="conflict-source">• ${{s.source}} → rank=${{s.rank}} (${{s.rank===1?'recommended':s.rank===2?'acceptable':'avoid'}})</div>`).join('')}}
        <div class="conflict-resolved">→ Resolved to rank=${{c.resolved_rank}} (${{c.resolved_rank===1?'recommended':c.resolved_rank===2?'acceptable':'avoid'}})</div>
      </div>`;
    }});
  }} else {{
    conflictHtml = `<div class="no-conflicts">✓ No conflicts — all intents agree on item rankings.</div>`;
  }}

  conflictHtml += `<h3 style="margin-top:16px">Final Merged Context</h3>`;
  Object.entries(trace.merged_context).sort().forEach(([tag, data]) => {{
    if (!data.recommended.length && !data.acceptable.length && !data.avoid.length) return;
    conflictHtml += `<div class="tag-group"><div class="tag-name">${{tag}}</div>`;
    if (data.recommended.length) conflictHtml += `<div class="rank-row rank-recommended"><span class="rank-label rec">Recommended</span>${{data.recommended.join(', ')}}</div>`;
    if (data.acceptable.length) conflictHtml += `<div class="rank-row rank-acceptable"><span class="rank-label acc">Acceptable</span>${{data.acceptable.join(', ')}}</div>`;
    if (data.avoid.length) conflictHtml += `<div class="rank-row rank-avoid"><span class="rank-label avd">Avoid</span>${{data.avoid.join(', ')}}</div>`;
    conflictHtml += `</div>`;
  }});

  html += stage(4, 'Merge & Conflict Resolution', conflictHtml);

  // STAGE 5: LLM generates SQL query against product DB
  const dbq = trace.db_query || {{}};
  let stage5Html = `
    <p style="color:#94a3b8;font-size:0.88em;margin-bottom:14px">
      The LLM takes the KG context (recommended types, colors, materials) and generates a <strong>SQL query</strong> to search the ${{trace.brand}} product database.
      Product type filtering goes in WHERE, color/pattern/material matching goes in ORDER BY for ranking.
    </p>
  `;

  // KG context that informed the SQL
  const contextLines = trace.kg_context_string.split('\\n').map(line => {{
    if (line.startsWith('Recommended')) return `<span class="context-line-rec">${{line}}</span>`;
    if (line.startsWith('Acceptable')) return `<span class="context-line-acc">${{line}}</span>`;
    if (line.startsWith('Avoid')) return `<span class="context-line-avd">${{line}}</span>`;
    return line;
  }}).join('\\n');
  stage5Html += `<h3>KG Context (input to SQL generation)</h3>`;
  stage5Html += `<div class="context-block">${{contextLines}}</div>`;

  if (dbq.sql) {{
    stage5Html += `<h3 style="margin-top:16px">Generated SQL Query</h3>`;
    stage5Html += `<div class="context-block" style="color:#60a5fa;border-color:#1e3a5f">${{escapeHtml(dbq.sql)}}</div>`;
    stage5Html += `<div class="stats">
      <div class="stat"><div class="stat-value">${{dbq.product_count}}</div><div class="stat-label">Products Returned</div></div>
      <div class="stat"><div class="stat-value">${{(dbq.sql_gen_ms / 1000).toFixed(1)}}s</div><div class="stat-label">SQL Generation</div></div>
      <div class="stat"><div class="stat-value">${{dbq.sql_exec_ms}}ms</div><div class="stat-label">SQL Execution</div></div>
    </div>`;

    // Show returned products
    if (dbq.products && dbq.products.length > 0) {{
      stage5Html += `<h3>Products from DB</h3>`;
      dbq.products.forEach((p, i) => {{
        stage5Html += `<div class="product-card">
          <div class="product-header">
            <span class="product-title">#${{i+1}} ${{p.title}}</span>
            <span class="product-score" style="background:#475569">${{p.product_type}}</span>
          </div>
          <div class="product-meta">
            ${{p.colors ? `<span>Colors: ${{p.colors}}</span>` : ''}}
            ${{p.materials ? `<span>Materials: ${{p.materials}}</span>` : ''}}
            ${{p.price ? `<span>Price: ${{p.price.toLocaleString()}}</span>` : ''}}
          </div>
        </div>`;
      }});
    }}
    if (dbq.errors && dbq.errors.length) {{
      stage5Html += `<div style="color:#f87171;margin-top:8px">Errors: ${{dbq.errors.join(', ')}}</div>`;
    }}
  }} else {{
    stage5Html += `<div class="no-llm-msg">Run with <code>--llm</code> to see the generated SQL query.</div>`;
  }}

  html += stage(5, 'LLM Generates SQL → DB Query', stage5Html);

  // STAGE 6: LLM ranks the DB results
  let stage6Html = '';

  if (trace.llm_recommendations && trace.llm_recommendations.length > 0) {{
    stage6Html += `<p style="color:#94a3b8;font-size:0.88em;margin-bottom:14px">
      The LLM receives the ${{dbq.product_count || 0}} products from the DB query + the KG context, and uses its fashion knowledge to select the top 5 with reasoning.
    </p>`;
    stage6Html += `<div class="llm-timing">Response time: ${{(trace.llm_elapsed_ms / 1000).toFixed(1)}}s</div>`;

    trace.llm_recommendations.forEach((rec, i) => {{
      stage6Html += `<div class="rec-card">
        <div class="rec-header">
          <span class="rec-rank">${{i + 1}}</span>
          <span class="rec-title">${{rec.title || 'Unknown'}}</span>
          <span class="rec-llm-score">${{rec.score || '?'}}/10</span>
        </div>
        <div class="rec-reasoning">${{rec.reasoning || 'No reasoning provided'}}</div>
      </div>`;
    }});

    // Expandable prompts
    const sysId = 'sys_' + querySelect.value;
    const usrId = 'usr_' + querySelect.value;
    const rawId = 'raw_' + querySelect.value;
    stage6Html += `
      <div class="prompt-section" style="margin-top:16px">
        <div class="prompt-label" onclick="document.getElementById('${{sysId}}').classList.toggle('collapsed')">&#9654; System Prompt (click to expand)</div>
        <div class="prompt-content collapsed" id="${{sysId}}">${{escapeHtml(trace.llm_system_prompt || '')}}</div>
      </div>
      <div class="prompt-section">
        <div class="prompt-label" onclick="document.getElementById('${{usrId}}').classList.toggle('collapsed')">&#9654; User Prompt with DB products (click to expand)</div>
        <div class="prompt-content collapsed" id="${{usrId}}">${{escapeHtml(trace.llm_user_prompt || '')}}</div>
      </div>
      <div class="prompt-section">
        <div class="prompt-label" onclick="document.getElementById('${{rawId}}').classList.toggle('collapsed')">&#9654; Raw LLM Response (click to expand)</div>
        <div class="prompt-content collapsed" id="${{rawId}}">${{escapeHtml(trace.llm_raw_response || '')}}</div>
      </div>
    `;
  }} else {{
    stage6Html += `<div class="no-llm-msg">Run with <code>--llm</code> to see LLM-ranked recommendations.</div>`;
  }}

  html += stage(6, 'LLM Ranks & Recommends', stage6Html);

  pipeline.innerHTML = html;
}}

function stage(num, title, body) {{
  const connector = num > 1 ? `<div class="stage-connector"><div class="arrow"></div></div>` : '';
  return `
    ${{connector}}
    <div class="stage" id="stage${{num}}">
      <div class="stage-header" onclick="toggleStage(${{num}})">
        <div class="stage-number">${{num}}</div>
        <div class="stage-title">${{title}}</div>
        <div class="stage-toggle open" id="toggle${{num}}">▼</div>
      </div>
      <div class="stage-body" id="body${{num}}">
        ${{body}}
      </div>
    </div>
  `;
}}

function toggleStage(num) {{
  const body = document.getElementById('body' + num);
  const toggle = document.getElementById('toggle' + num);
  if (body.style.maxHeight === '0px') {{
    body.style.maxHeight = body.scrollHeight + 'px';
    toggle.classList.add('open');
  }} else {{
    body.style.maxHeight = '0px';
    toggle.classList.remove('open');
  }}
}}

function escapeHtml(text) {{
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}}

renderTrace();
</script>
</body>
</html>"""
    return html


if __name__ == "__main__":
    import sys
    run_llm = "--llm" in sys.argv

    if run_llm:
        print("Building trace data WITH live LLM calls...")
        print("(This will take ~40-60s per query for intent extraction + recommendation)")
    else:
        print("Building trace data (no LLM calls)...")
        print("Tip: Run with --llm to include actual Codex intent extraction + recommendations")

    # Load KG once
    print("Loading Knowledge Graph...")
    kg = KnowledgeGraph(KG_PATH)

    # Pre-load catalogs per brand (avoid reloading for each query)
    brand_catalogs = {}
    brands_needed = set(q.get("brand", "masaba") for q in DEMO_QUERIES)
    for brand in brands_needed:
        catalog_path = CATALOGS.get(brand)
        if catalog_path:
            print(f"Loading {brand} catalog...")
            max_products = 50000 if brand == "aza" else None
            adapter = load_catalog(brand, catalog_path, max_products=max_products)
            brand_catalogs[brand] = adapter.products
            print(f"  → {len(brand_catalogs[brand])} products")

    traces = []
    for i, q in enumerate(DEMO_QUERIES):
        brand = q.get("brand", "masaba")
        print(f"  [{i+1}/{len(DEMO_QUERIES)}] [{brand.upper()}] {q['query']}")
        trace = build_trace_data(
            q["query"], q["intents"], brand=brand, run_llm=run_llm,
            kg=kg, catalog_products=brand_catalogs.get(brand),
        )
        traces.append(trace)

    html = generate_html(traces)
    output_path = os.path.join(os.path.dirname(__file__), "pipeline_visualizer.html")
    with open(output_path, "w") as f:
        f.write(html)

    print(f"\nVisualization saved to: {output_path}")
    print("Opening in browser...")
    webbrowser.open(f"file://{os.path.abspath(output_path)}")
