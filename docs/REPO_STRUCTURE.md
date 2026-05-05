# Repository Structure

This repo is organized around one request path: turn a fashion shopping query into structured intents, enrich it with a knowledge graph, retrieve catalog products, assemble outfits, and evaluate whether the result is good.

## Top-Level Runtime Modules

| Path | Role |
|---|---|
| `main.py` | CLI entry point for one-shot and interactive local runs. |
| `conversation.py` | Top-level request orchestrator. Handles follow-up detection, session state, workflow routing, and final response formatting. |
| `intent_extractor.py` | Converts free-form user text into structured intents with LLM parsing plus deterministic enrichment. |
| `prompts.py` | Shared prompt templates for intent extraction, SQL generation, recommendation selection, and response formatting. |
| `session.py` | Tracks multi-turn intent state, sticky constraints, and previous conversation turns. |
| `router.py` | Chooses one workflow type from extracted intents and preserves secondary context for compound queries. |
| `workflows/` | Domain-specific request handlers for occasion, vacation, gifting, health, activity, place/profession, and general queries. |
| `knowledge_graph.py` | Loads the Excel knowledge graph, resolves aliases, and merges recommended/acceptable/avoid rules. |
| `brand_adapters.py` | Normalizes raw scraper catalog JSON into a stable product shape before database build. |
| `db_query.py` | Converts query + intents + KG context into SQL and retrieves candidates from brand SQLite DBs. |
| `outfit_builder.py` | Scores/diversifies retrieved products, adds accessories, and returns structured outfit results. |
| `complementary_graphs.py` | Loads accessory, color, product-combination, and feature graph data from the assistant submodule. |
| `color_coordinator.py` | Checks and suggests coordinated outfit color palettes. |
| `weather_inference.py` | Adds climate/weather context from location/month/region signals. |
| `llm_client.py` | Centralized Codex CLI wrapper used by all LLM stages. |

## Data Layout

| Path | Contents |
|---|---|
| `data/graph/Master_Graph.xlsx` | Main fashion knowledge graph used at runtime. |
| `data/eval/golden_eval_set.json` | Golden eval queries, expected product types, cultural constraints, and scoring rubrics. |
| `data/raw/` | Raw/reference spreadsheets used during earlier query and KG design. |
| `assistant/` | Submodule containing complementary graph data from `crystals-ai/assistant`. |

Generated product databases (`*.db`) are intentionally ignored because they are rebuilt from external scraper catalogs via `scripts/data/build_db.py`.

## Scripts

| Path | Purpose |
|---|---|
| `scripts/data/build_db.py` | Builds `masaba_products.db`, `kalki_products.db`, and `aza_products.db` from scraper catalog JSONs. |
| `scripts/data/generate_queries.py` | Generates exploratory spreadsheet query sets into `data/raw/`. |
| `scripts/eval/run_golden_eval_parallel.py` | Faster rotating-brand eval: one brand per golden query. |
| `scripts/eval/run_golden_eval_all_brands.py` | Exhaustive eval: every golden query against every brand catalog. |
| `scripts/eval/eval_audit_visualizer.py` | Builds the static HTML audit UI from saved eval output. |

## Legacy

`legacy/` contains older pre-SQL matcher, trace, eval, and visualizer code. These files are kept for reference while the SQL/KG pipeline stabilizes, but new development should use `db_query.py` and `scripts/eval/`.

## Request Handling Flow

1. A user query enters through `main.py` or `ConversationManager.process()`.
2. `intent_extractor.py` extracts and enriches intents such as occasion, event, product type, budget, weather, relation, health needs, and style goals.
3. `router.py` chooses the best workflow and captures secondary signals for compound requests.
4. The selected module in `workflows/` may add weather, gifting, vacation, health, or formality context.
5. `knowledge_graph.py` looks up product/color/material/pattern/fit guidance for the intents.
6. `db_query.py` uses the KG context and intents to generate SQL, executes against a brand DB, and falls back deterministically if needed.
7. `outfit_builder.py` scores candidates, diversifies recommendations, adds accessory ideas, and returns an `OutfitResult`.
8. `conversation.py` formats the user-facing response and suggests follow-up refinements.

## Eval Flow

The golden eval does not test live intent extraction. It uses annotated intents from `data/eval/golden_eval_set.json`.

For each query-brand job:

1. KG lookup from golden intents.
2. SQL generation + DB candidate retrieval.
3. LLM selection of top 5 products from the candidate pool.
4. LLM evaluator scores each product from 0 to 3 against the rubric.
5. Metrics are computed: NDCG@5, MRR, and hit rate.

The exhaustive eval uses 8 parallel workers and currently runs 165 jobs (`55 queries × 3 brands`).

Runtime conversation evals are separate from golden ranking evals. They run scenarios through `ConversationManager` so clarifications, follow-ups, topic switches, KG trace, SQL trace, catalog-gap handling, and response behavior can be inspected. See `docs/SEARCH_EVAL_METHODOLOGY.md` for the full methodology and commands.
