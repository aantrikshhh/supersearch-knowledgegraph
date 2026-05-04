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
python3 scripts/eval/run_golden_eval_all_brands.py

# Quick eval (55 queries, 1 brand each; supports --category)
python3 scripts/eval/run_golden_eval_parallel.py

# Conversational eval (clarifications + follow-up turns)
python3 scripts/eval/run_conversation_eval.py --skip-response-llm

# Generate HTML visualizer
python3 scripts/eval/eval_audit_visualizer.py --eval eval_results/golden_eval_YYYYMMDD_HHMMSS.json

# Build product databases (run after catalog updates)
python3 scripts/data/build_db.py
```

## Pipeline Flow
```
User Query
  → LLM₁ (Codex via llm_client.py): extract intents {occasion: sangeet, bodytype: petite}
  → Router: classify → OCCASION workflow (deterministic, no LLM)
  → KG Lookup: graph[(occasion, sangeet)] → recommended products, colors, patterns
  → LLM₂ (Codex via llm_client.py): generate SQL from intents + KG context
  → SQLite: execute query → 20 products in ~3ms
  → Outfit Builder: score + diversify + add accessories via complementary graphs
  → LLM₃ (Codex via llm_client.py): format natural language response
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
- `data/graph/Master_Graph.xlsx` — 3012-row knowledge graph
- `*.db` — SQLite product databases (Masaba 971, Kalki 9664, Aza 50000)
- `assistant/all_graph_components/` — Complementary graphs (colors, products, features)
- `data/eval/golden_eval_set.json` — 55 annotated eval queries with scoring rubrics
- `data/eval/conversation_eval_set.json` — multi-turn runtime scenarios for clarifications and follow-ups
- `docs/specs/Workflows.docx` — Production workflow spec

## LLM Calls
All core calls use the centralized `llm_client.py` Codex CLI subprocess wrapper. Override the model with `KG_LLM_MODEL` when needed. Parallelize with ThreadPoolExecutor (8 workers) for eval runs.

## Latest Eval (2026-05-04)
- Overall NDCG@5: 0.848 | MRR: 0.667 | Hit Rate: 0.764
- 165 runs (55 queries × 3 brands) in 67.9 minutes with 8 workers.
- Per-brand NDCG@5: Masaba 0.885, Kalki 0.811, Aza 0.849.

## Key Conventions

- All core LLM calls go through `llm_client.py`, NOT the Anthropic SDK.
- KG conflict resolution: avoid (-1) vetoes everything, otherwise best rank (max) wins. See knowledge_graph.py:lookup().
- Cultural color/style rules should live in the KG; `db_query.py` only adds dynamic role-specific guardrails the KG cannot express.
- Product type aliases map catalog values to KG names. See taxonomy.py:PRODUCT_TYPE_ALIASES.
- Intent aliases map user terms to KG entity values. See taxonomy.py:INTENT_ALIASES.
- Complementary graphs and KG are loaded as singletons in workflows/base.py (get_kg(), get_comp_graphs()).
- Router uses deterministic rules, NOT LLM. See router.py:classify().
- Eval uses ThreadPoolExecutor with 8 workers for parallelism. See `scripts/eval/run_golden_eval_parallel.py`.
- Conversational evals use the production `ConversationManager` runtime path. See `scripts/eval/run_conversation_eval.py`.
- Product catalog JSONs live at /Users/aant/repos/scraper-infra/data/ — paths are in config.py:CATALOG_PATHS.
- The assistant/ directory was cloned from github.com/crystals-ai/assistant and contains complementary graph data.

## Warnings

- Do NOT use the Anthropic SDK directly — no API key is configured. Use the `llm_client.py` Codex CLI wrapper.
- `legacy/eval_pipeline.py` and `legacy/product_matcher.py` are legacy — use `scripts/eval/run_golden_eval_parallel.py` and `db_query.py` instead.
- `legacy/visualizer.py` and `legacy/trace.py` are historical debugging tools; use `scripts/eval/eval_audit_visualizer.py` for current eval review.
- Aza catalog has 224K products total but we cap at 50K in the DB (see `scripts/data/build_db.py`).
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
