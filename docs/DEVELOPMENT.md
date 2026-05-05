# Development Guide

## Prerequisites

- Python 3.11+ (tested on 3.14)
- Codex CLI installed and authenticated (`codex` command available)
- Product catalog JSON files at `/Users/aant/repos/scraper-infra/data/`

## Setup

```bash
# 1. Install dependencies
pip3 install --break-system-packages openpyxl tqdm python-docx

# 2. Build product databases from catalog JSONs
python3 scripts/data/build_db.py

# 3. Verify everything works
python3 main.py -q "outfit for Diwali" -b masaba --trace
```

## Running

### Interactive Mode
```bash
python3 main.py -b aza          # Aza catalog
python3 main.py -b kalki         # Kalki catalog
python3 main.py -b masaba        # Masaba catalog
python3 main.py -b aza --debug   # Show intents/workflow after each turn
```

### Single Query
```bash
python3 main.py -q "What to wear to a sangeet?" -b kalki --trace
```

### Eval
```bash
# Quick: 55 queries, 1 brand each
python3 scripts/eval/run_golden_eval_parallel.py

# Full: 55 queries × 3 brands
python3 scripts/eval/run_golden_eval_all_brands.py

# Runtime conversation flow: clarifications + follow-up turns
python3 scripts/eval/run_conversation_eval.py --skip-response-llm

# Results saved to eval_results/golden_eval_{timestamp}.json
```

See `docs/SEARCH_EVAL_METHODOLOGY.md` for the full search-quality methodology, including deterministic checks, LLM judges, catalog-gap classification, and the Aza 500 conversation eval.

### Visualizer
```bash
# Generate the saved-eval audit UI
python3 scripts/eval/eval_audit_visualizer.py --eval eval_results/golden_eval_YYYYMMDD_HHMMSS.json

# Generate the Aza conversation eval visualizer
python3 scripts/eval/conversation_eval_visualizer.py --eval eval_results/aza_conversation_500_deterministic_final.json --out eval_results/aza_conversation_500_visualizer.html

# Legacy pipeline visualizer lives under legacy/ for archaeology only.
```

## Adding a New Brand

1. Get the product catalog JSON (from scraper-infra or manual export)

2. Add an adapter in `brand_adapters.py`:
```python
class NewBrandAdapter:
    def __init__(self, json_path):
        # Load and normalize products to NormalizedProduct format
```

3. Register in `brand_adapters.py:load_catalog()` and `config.py:CATALOG_PATHS`

4. Build the database:
```python
python3 scripts/data/build_db.py
```

5. Test:
```python
python3 main.py -q "test query" -b newbrand --trace
```

## Adding a New Workflow

1. Create `workflows/newflow.py`:
```python
from workflows.base import run_standard_pipeline

def run(query, intents, brand, session=None):
    # Custom logic here
    return run_standard_pipeline(query, intents, brand)
```

2. Add routing rule in `router.py:classify()`

3. Add import in `router.py:get_workflow()`

## Updating the Knowledge Graph

Edit `data/graph/Master_Graph.xlsx` (sheet: "graph"). Columns:
- entity, entity_value, category, tag, name, rank, gender

After editing, no rebuild needed — the graph loads fresh each time from Excel.

To add aliases (e.g., "griha pravesh" → "housewarming"), edit `INTENT_ALIASES` in `taxonomy.py`.

## Updating Complementary Graphs

Files are in `assistant/all_graph_components/`. Edit the Excel/CSV files directly. The `ComplementaryGraphs` class loads them fresh each time.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `codex` command not found | CLI not installed | Install/authenticate Codex CLI |
| 0 products returned | SQL too restrictive | Check db_query.py self-correction is working. Run with --trace |
| Wrong product types | KG alias missing | Add alias to `INTENT_ALIASES` in taxonomy.py |
| Slow eval (~2+ hours) | Sequential processing | Use `scripts/eval/run_golden_eval_parallel.py` (8 workers) |
| Import errors | Circular imports | Workflows use lazy imports via router.get_workflow() |
| SQLite locked | Concurrent writes | Each thread gets its own connection (read-only) |
| Cultural color wrong | Missing KG festival/occasion row | Add or correct rows in `data/graph/Master_Graph.xlsx`; keep code-level notes limited to role-specific constraints |
