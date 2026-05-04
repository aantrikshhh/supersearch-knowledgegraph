# SuperSearch — Fashion Knowledge Graph Recommendation Engine

A clothing recommendation system that combines a Knowledge Graph with LLM-powered SQL generation to recommend outfits from Indian fashion brand catalogs.

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.14 |
| LLM | Codex CLI via `llm_client.py` |
| Database | SQLite |
| Knowledge Graph | Excel/CSV → in-memory Python dicts |
| Eval Framework | Custom NDCG/MRR/HitRate scorer |

## Supported Brands

| Brand | Products | Catalog Source |
|---|---|---|
| House of Masaba | 971 | Shopify |
| Kalki Fashion | 9,664 | Shopify |
| Aza Fashions | 50,000 | NextJS/Unbxd |

## Quick Start

```bash
# Install dependencies
pip3 install --break-system-packages openpyxl tqdm python-docx

# Build product databases (one-time)
python3 scripts/data/build_db.py

# Single query with trace
python3 main.py -q "What to wear to a sangeet?" -b kalki --trace

# Interactive conversation
python3 main.py -b aza

# Run eval (55 queries × 3 brands; runtime depends on LLM model)
python3 scripts/eval/run_golden_eval_all_brands.py

# Generate eval audit visualizer from a saved eval result
python3 scripts/eval/eval_audit_visualizer.py --eval eval_results/golden_eval_YYYYMMDD_HHMMSS.json
```

## Pipeline Overview

```
User Query → Intent Extraction (LLM + deterministic enrichment)
  → Knowledge Graph Lookup → SQL Generation (LLM or deterministic fallback)
  → SQLite Query → Outfit Builder → Complementary Accessories
  → Response (LLM or fallback)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for full details.

## Project Structure

```
knowledge-graph/
├── main.py                    # CLI entry point for local chat/query runs
├── config.py                  # Shared paths, model settings, weather data
├── conversation.py            # Top-level request orchestration
├── intent_extractor.py        # Free-form query → structured intents
├── prompts.py                 # System/user prompt templates for LLM calls
├── llm_client.py              # Central Codex CLI wrapper
├── router.py                  # Intent → workflow selection
├── workflows/                 # Domain workflows: occasion, vacation, health, etc.
├── knowledge_graph.py         # KG loader + semantic lookup
├── complementary_graphs.py    # Accessory/color/feature compatibility graphs
├── brand_adapters.py          # Scraper catalog normalization
├── color_coordinator.py       # Palette and accessory color coordination
├── weather_inference.py       # Rule/LLM weather inference for travel flows
├── session.py                 # Multi-turn conversation state
├── db_query.py                # SQL generation/execution against product DBs
├── outfit_builder.py          # Product scoring, outfits, accessories
├── data/
│   ├── graph/                 # Master knowledge graph workbook
│   ├── eval/                  # Golden eval set and rubrics
│   └── raw/                   # Raw/reference spreadsheets
├── scripts/
│   ├── data/                  # DB/query data generation scripts
│   └── eval/                  # Eval runners + audit visualizer generator
├── docs/
│   ├── specs/                 # Workflow/product specs
│   └── assets/                # Diagrams/images used by docs
├── legacy/                    # Older pre-SQL matcher/eval/visualizer tools
└── assistant/                 # Complementary graph data submodule
```

## Current Eval Scores (2026-05-04)

| Metric | Overall | Masaba | Kalki | Aza |
|---|---|---|---|---|
| NDCG@5 | 0.848 | 0.885 | 0.811 | 0.849 |
| MRR | 0.667 | 0.673 | 0.636 | 0.692 |
| Hit Rate | 0.764 | 0.727 | 0.746 | 0.818 |

Latest exhaustive eval: 165 runs (`55 queries × 3 brands`) in 67.9 minutes with 8 workers.

## Environment Variables

None required. LLM calls use the Codex CLI. Set `KG_LLM_MODEL` to override the default model from `config.py`.

## Testing

```bash
# Quick smoke test
python3 main.py -q "outfit for Diwali" -b masaba --trace

# Full eval
python3 scripts/eval/run_golden_eval_parallel.py

# Cross-brand eval
python3 scripts/eval/run_golden_eval_all_brands.py
```

See [docs/REPO_STRUCTURE.md](docs/REPO_STRUCTURE.md) for a module-by-module overview and request handling guide.
