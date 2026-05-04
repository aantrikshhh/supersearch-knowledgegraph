# Knowledge Graph Pipeline — Clothing Recommendation System

## What This Is
A fashion recommendation engine that uses a Knowledge Graph + LLM + SQL to recommend outfits from Indian fashion brand catalogs (Masaba, Kalki, Aza). Built as an MVP for SuperSearch.

## Quick Start
```bash
# Single query
python3 main.py -q "What to wear to a sangeet?" -b masaba --trace

# Interactive conversation
python3 main.py -b aza

# Run eval (55 queries × 3 brands; runtime depends on LLM model)
python3 run_golden_eval_all_brands.py

# Quick eval (55 queries, 1 brand each; supports --category)
python3 run_golden_eval_parallel.py

# Generate HTML visualizer
python3 visualizer.py --llm

# Build product databases (run after catalog updates)
python3 build_db.py
```

## Pipeline Flow
```
User Query
  → LLM₁ (Haiku): extract intents {occasion: sangeet, bodytype: petite}
  → Router: classify → OCCASION workflow (deterministic, no LLM)
  → KG Lookup: graph[(occasion, sangeet)] → recommended products, colors, patterns
  → LLM₂ (Haiku): generate SQL from intents + KG context
  → SQLite: execute query → 20 products in ~3ms
  → Outfit Builder: score + diversify + add accessories via complementary graphs
  → LLM₃ (Haiku): format natural language response
```

## 7 Workflow Types
Queries route to: `vacation`, `place_profession`, `activity`, `occasion`, `health`, `general`, `gifting`

## Key Files
- `config.py` — All paths, weather table, formality hierarchy
- `knowledge_graph.py` — KG loader + lookup engine
- `complementary_graphs.py` — Color/product/feature complementary data
- `db_query.py` — LLM SQL generation with few-shot examples + self-correction
- `outfit_builder.py` — Complete outfit assembly with accessories
- `router.py` — Intent → workflow routing
- `main.py` — CLI entry point
- `workflows/` — 7 workflow modules

## Data
- `Master_Graph.xlsx` — 3012-row knowledge graph
- `*.db` — SQLite product databases (Masaba 971, Kalki 9664, Aza 50000)
- `assistant/all_graph_components/` — Complementary graphs (colors, products, features)
- `golden_eval_set.json` — 55 annotated eval queries with scoring rubrics
- `Workflows.docx` — Production workflow spec

## LLM Calls
All core calls use the centralized `llm_client.py` Codex CLI subprocess wrapper. Override the model with `KG_LLM_MODEL` when needed. Parallelize with ThreadPoolExecutor (8 workers) for eval runs.

## Latest Eval (2026-05-04)
- NDCG@5: 0.885 | MRR: 0.747 | Hit Rate: 0.927
- Previous baseline: 150 runs (50 queries × 3 brands) in 37 minutes. Current eval set has 55 queries after gifting additions.
- Score distribution: 58% good/perfect, 87% acceptable+

## Key Conventions

- All core LLM calls go through `llm_client.py`, NOT the Anthropic SDK.
- KG conflict resolution: avoid (-1) vetoes everything, otherwise best rank (max) wins. See knowledge_graph.py:lookup().
- SQL generation uses cultural color enforcement (Sikh wedding excludes white/black, Onam forces white/cream). See db_query.py:CULTURAL_NOTES.
- Product type aliases map catalog values to KG names. See knowledge_graph.py:PRODUCT_TYPE_ALIASES.
- Intent aliases map user terms to KG entity values. See knowledge_graph.py:INTENT_ALIASES.
- Complementary graphs and KG are loaded as singletons in workflows/base.py (get_kg(), get_comp_graphs()).
- Router uses deterministic rules, NOT LLM. See router.py:classify().
- Eval uses ThreadPoolExecutor with 8 workers for parallelism. See run_golden_eval_parallel.py.
- Product catalog JSONs live at /Users/aant/repos/scraper-infra/data/ — paths are in config.py:CATALOG_PATHS.
- The assistant/ directory was cloned from github.com/crystals-ai/assistant and contains complementary graph data.

## Warnings

- Do NOT use the Anthropic SDK directly — no API key is configured. Use `Codex` CLI subprocess.
- eval_pipeline.py and product_matcher.py are legacy — use run_golden_eval_parallel.py and db_query.py instead.
- visualizer.py and trace.py still have some hardcoded paths — they work but should use config.py.
- Aza catalog has 224K products total but we cap at 50K in the DB (see build_db.py).
- The Codex CLI subprocess has startup overhead per call. Parallelize eval work; keep deterministic fallbacks for local smoke tests.

## Not Yet Implemented

- api.py (FastAPI HTTP endpoints)
- Vector embeddings for semantic search (subjective queries)
- Full integration test of all 7 workflows
- Pack-a-bag needs more testing

## Dependencies
```
pip3 install --break-system-packages openpyxl tqdm python-docx
```
