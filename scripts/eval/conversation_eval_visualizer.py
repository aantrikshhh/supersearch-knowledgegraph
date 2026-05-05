"""Build a static HTML visualizer for conversation eval artifacts.

The conversation eval artifact is trace-heavy: every turn can include runtime
decisions, KG provenance, SQL/debug data, products, catalog status, and optional
LLM judge outputs. This script compacts that data into a browsable single-file
HTML report for mining workflow/config/KG/prompt insights.
"""

import argparse
import html
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import EVAL_RESULTS_DIR


DEFAULT_EVAL = Path(EVAL_RESULTS_DIR) / "aza_conversation_500_deterministic_final.json"
DEFAULT_OUT = Path(EVAL_RESULTS_DIR) / "aza_conversation_500_visualizer.html"


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


def compact_product(product):
    return {
        "id": product.get("id"),
        "title": product.get("title", ""),
        "product_type": product.get("product_type", ""),
        "colors": product.get("colors", ""),
        "materials": product.get("materials", ""),
        "patterns": product.get("patterns", ""),
        "price": product.get("price", ""),
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


def compact_db_trace(turn):
    trace = turn.get("db_trace", {}) or {}
    return {
        "timings": trace.get("timings", {}),
        "retries": trace.get("retries", [])[:5],
        "candidate_count": len(trace.get("candidates", [])),
        "candidates": [compact_product(p) for p in trace.get("candidates", [])[:10]],
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
    scenarios = []
    all_turns = []
    for scenario in result.get("results", []):
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
                "primary_products": [compact_product(p) for p in turn.get("primary_products", [])],
                "db_product_count": turn.get("db_product_count", 0),
                "db_errors": turn.get("db_errors", []),
                "sql": turn.get("sql", ""),
                "db_trace": compact_db_trace(turn),
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


def render_html(payload, title):
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
