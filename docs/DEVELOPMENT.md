# Development Guide

## Prerequisites

- Python 3.11+ (tested on 3.14)
- Claude Code CLI installed and authenticated (`claude` command available)
- Product catalog JSON files at `/Users/aant/repos/scraper-infra/data/`

## Setup

```bash
# 1. Install dependencies
pip3 install --break-system-packages openpyxl tqdm python-docx

# 2. Build product databases from catalog JSONs
python3 build_db.py

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
# Quick: 50 queries, 1 brand each (~13 min)
python3 run_golden_eval_parallel.py

# Full: 50 queries × 3 brands (~37 min)
python3 run_golden_eval_all_brands.py

# Results saved to eval_results/golden_eval_{timestamp}.json
```

### Visualizer
```bash
# Without LLM calls (fast, stages 1-5 only)
python3 visualizer.py

# With live LLM calls (full pipeline, ~15 min)
python3 visualizer.py --llm

# Opens pipeline_visualizer.html in browser
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
python3 build_db.py
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

Edit `Master_Graph.xlsx` (sheet: "graph"). Columns:
- entity, entity_value, category, tag, name, rank, gender

After editing, no rebuild needed — the graph loads fresh each time from Excel.

To add aliases (e.g., "griha pravesh" → "housewarming"), edit `INTENT_ALIASES` in `knowledge_graph.py`.

## Updating Complementary Graphs

Files are in `assistant/all_graph_components/`. Edit the Excel/CSV files directly. The `ComplementaryGraphs` class loads them fresh each time.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `claude` command not found | CLI not installed | Install Claude Code CLI |
| 0 products returned | SQL too restrictive | Check db_query.py self-correction is working. Run with --trace |
| Wrong product types | KG alias missing | Add alias to `INTENT_ALIASES` in knowledge_graph.py |
| Slow eval (~2+ hours) | Sequential processing | Use `run_golden_eval_parallel.py` (8 workers) |
| Import errors | Circular imports | Workflows use lazy imports via router.get_workflow() |
| SQLite locked | Concurrent writes | Each thread gets its own connection (read-only) |
| Cultural color wrong | Missing cultural note | Add to `CULTURAL_NOTES` in db_query.py |
