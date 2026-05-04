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
python3 build_db.py

# Single query with trace
python3 main.py -q "What to wear to a sangeet?" -b kalki --trace

# Interactive conversation
python3 main.py -b aza

# Run eval (55 queries × 3 brands; runtime depends on LLM model)
python3 run_golden_eval_all_brands.py

# Generate pipeline visualizer
python3 visualizer.py --llm
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
├── main.py                    # CLI entry point
├── config.py                  # Paths, weather data, formality levels
├── knowledge_graph.py         # KG loader + lookup
├── complementary_graphs.py    # Color/product/feature graphs
├── db_query.py                # LLM SQL generation + execution
├── outfit_builder.py          # Complete outfit assembly
├── router.py                  # Intent → workflow routing
├── intent_extractor.py        # LLM intent extraction
├── conversation.py            # Multi-turn orchestrator
├── workflows/                 # 7 workflow modules
│   ├── occasion.py            # Weddings, parties, festivals
│   ├── vacation.py            # Multi-day trip packing
│   ├── gifting.py             # Gift recommendations
│   └── ...                    # place, activity, health, general
├── eval_results/              # NDCG scoring outputs
├── docs/                      # Architecture + development docs
└── assistant/                 # Complementary graph data
```

## Current Eval Scores (2026-05-04)

| Metric | Overall | Masaba | Kalki | Aza |
|---|---|---|---|---|
| NDCG@5 | 0.885 | 0.840 | 0.902 | 0.913 |
| MRR | 0.747 | 0.640 | 0.800 | 0.800 |
| Hit Rate | 0.927 | 0.880 | 0.940 | 0.960 |

## Environment Variables

None required. LLM calls use the Codex CLI. Set `KG_LLM_MODEL` to override the default model from `config.py`.

## Testing

```bash
# Quick smoke test
python3 main.py -q "outfit for Diwali" -b masaba --trace

# Full eval
python3 run_golden_eval_parallel.py

# Cross-brand eval
python3 run_golden_eval_all_brands.py
```
