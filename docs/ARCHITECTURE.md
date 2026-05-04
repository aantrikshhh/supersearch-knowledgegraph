# Architecture

## System Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      USER QUERY                              │
│  "What should I wear to my sister's sangeet? I'm plus size" │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  intent_extractor   │  LLM Call #1
              │  Parse → JSON       │  → {occasion: sangeet,
              │  + gender inference  │     bodytype: plus size,
              │  + gift detection    │     relation: sister,
              │  + duration extract  │     gender: female}
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │     router.py       │  Deterministic rules
              │  Check entity keys  │  occasion present →
              │  → WorkflowType     │  OCCASION workflow
              └──────────┬──────────┘  (no LLM call)
                         │
          ┌──────────────┼──────────────────┐
          ▼              ▼                  ▼
    ┌──────────┐  ┌──────────┐      ┌──────────┐
    │ vacation │  │ occasion │ ...  │ gifting  │
    └────┬─────┘  └────┬─────┘      └────┬─────┘
         │              │                  │
         └──────────────┼──────────────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │  knowledge_graph.py │  In-memory lookup
              │                     │  graph[("occasion",
              │ data/graph/         │    "sangeet")] →
              │ Master_Graph.xlsx   │
              │  3012 rows          │  18 entries:
              │                     │  product, colour,
              │  Alias resolution:  │  pattern, material,
              │  Indian wedding →   │  fit, sleeve, neck
              │  hindu wedding      │  with ranks 1/2/-1
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │    db_query.py      │  LLM Call #2
              │                     │
              │  Intents + KG       │  → SQL query with:
              │  context + cultural │    WHERE product_type
              │  notes + budget     │    ORDER BY color,
              │  + few-shot         │    pattern match
              │  examples (10)      │    LIMIT 20
              │                     │
              │  Self-correction:   │  If 0 results →
              │  retry 2x then      │  relax + retry
              │  deterministic      │
              │  fallback           │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │     SQLite DB       │  ~3ms execution
              │  masaba: 971 rows   │
              │  kalki: 9,664 rows  │  → 20 products
              │  aza: 50,000 rows   │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  outfit_builder.py  │  In-memory, <10ms
              │                     │
              │  Score candidates   │  +10 recommended type
              │  vs KG context      │  +3 color match
              │                     │  -20 avoid type
              │  Diversify types    │
              │                     │
              │  ┌───────────────┐  │
              │  │complementary_ │  │  Color harmony:
              │  │graphs.py      │  │  red → gold (metallic)
              │  │               │  │
              │  │ 453 colors    │  │  Outfit combos:
              │  │ 482 products  │  │  sangeet → lehenga +
              │  │ 140 features  │  │  chandbali + potli +
              │  │               │  │  block heel
              │  │ + accessories │  │
              │  │ bags/shoes/   │  │
              │  │ jewellery     │  │
              │  └───────────────┘  │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │  conversation.py    │  LLM Call #3
              │  Format response    │  → Natural language
              │  + styling notes    │    with product picks,
              │  + session update   │    accessories, colors,
              │                     │    and styling tips
              └─────────────────────┘
```

## Data Flow

### Knowledge Graph (`data/graph/Master_Graph.xlsx`)

```
Schema: entity | entity_value | category | tag | name | rank | gender

Example rows for (occasion, sangeet):
  occasion | sangeet | clothing | product  | lehenga  | 1  | female
  occasion | sangeet | clothing | product  | saree    | 1  | female
  occasion | sangeet | clothing | colour   | pink     | 1  | NULL
  occasion | sangeet | clothing | colour   | magenta  | 1  | NULL
  occasion | sangeet | clothing | pattern  | sequin   | 1  | NULL
  occasion | sangeet | clothing | product  | shorts   | -1 | both

12 entity types: place, occasion, activity, event, weather, bodytype,
                  profession, health, agegroup, religion, complexion, relation

24 tag types: product, colour, pattern, material, fit, sleeve, neck,
              length, silhouette, coverage, waist, breathability, seam,
              cushioning, sole, heel height, and more

Ranks: 1 = recommended, 2 = acceptable, -1 = avoid
```

### Conflict Resolution (multiple intents)

```
If ANY intent says avoid (-1) → item is AVOIDED (veto)
Otherwise → take the BEST rank (max)

Example: place:restaurant says dress=1, weather:stormy says dress=-1
  → dress is AVOIDED (stormy vetoes it)

Example: place:restaurant says kurta=1, weather:cloudy says kurta=2
  → kurta is RECOMMENDED (best rank = 1)
```

### Complementary Graphs (assistant/all_graph_components/)

```
complimentary_colours_graph.xlsx (453 rows):
  colour_1 | colour_2 | rank | context | harmony_type
  red      | gold     | 1    | ethnic  | metallic
  black    | white    | 1    | all     | neutral

complimentary_product_graph.xlsx (482 rows):
  combo_id | combo_name              | occasion_tag | gender | category  | tag          | name
  1        | Casual Jeans Look       | casual       | female | clothing  | product      | pant
  1        | Casual Jeans Look       | casual       | female | clothing  | product      | top
  1        | Casual Jeans Look       | casual       | female | shoes     | product      | sneaker
  1        | Casual Jeans Look       | casual       | female | bag       | product_type | crossbody

complimentary_features_graph.xlsx (140 rows):
  category | feature_name | feature_value | category | feature_name | feature_value | rank
  clothing | fit          | baggy         | clothing | fit          | fitted        | 1
```

### SQL Generation

```
Input to LLM:
  - User query + extracted intents
  - KG context string (recommended/acceptable/avoid per tag)
  - Cultural notes (e.g., "Sikh wedding: avoid white/black")
  - Budget signal (e.g., "LUXURY — sort by price DESC")
  - Available product_type values in the DB
  - 10 few-shot examples covering common patterns

Output: SQLite SELECT query
  - WHERE: product_type filter (hard), gender filter (when implied)
  - WHERE: cultural color exclusions (e.g., NOT white for Sikh wedding)
  - ORDER BY: CASE statements ranking recommended types, colors, patterns
  - LIMIT 20

Self-correction: if 0 results → LLM relaxes constraints → retry (2x max)
Final fallback: deterministic query using KG product types
```

### Formality Hierarchy (config.py)

```
formal:      hindu wedding, muslim wedding, engagement, gala, corporate event
             → saree, lehenga, sherwani | silk, velvet | embroidered, sequin

semi_formal: mehendi, haldi, anniversary, graduation, date night
             → kurta, dress, coord | georgette, chiffon | ethnic, floral

casual:      birthday party, reunion, picnic, concert
             → dress, coord, kurta, top | cotton, linen | printed, casual

festive:     festival, bachelorette, Diwali, Navratri
             → saree, lehenga, kurta | silk, georgette | ethnic, embellished

mourning:    funeral
             → saree, salwar, kurta | cotton | white/off-white ONLY
```

## Module Dependency Graph

```
main.py / api.py
  └→ conversation.py
       ├→ intent_extractor.py ──→ prompts.py
       ├→ router.py
       │    └→ workflows/*.py
       │         ├→ workflows/base.py
       │         │    ├→ knowledge_graph.py ──→ data/graph/Master_Graph.xlsx
       │         │    ├→ db_query.py ──→ *.db (SQLite)
       │         │    │    └→ prompts.py
       │         │    └→ outfit_builder.py
       │         │         ├→ complementary_graphs.py ──→ assistant/all_graph_components/
       │         │         ├→ color_coordinator.py
       │         │         └→ config.py (formality)
       │         └→ weather_inference.py ──→ config.py (weather table)
       └→ session.py
```

## Eval Architecture

```
data/eval/golden_eval_set.json (55 queries with rubrics)
         │
         ▼
scripts/eval/run_golden_eval_parallel.py (8 ThreadPoolExecutor workers)
  Per query:
    1. KG lookup (in-memory, thread-safe)
    2. SQL generation (LLM subprocess)
    3. DB query (SQLite, separate connection per thread)
    4. LLM recommendation (LLM subprocess)
    5. Batch scoring (1 LLM call scores all 5 products)
         │
         ▼
  NDCG@5, MRR, Hit Rate per query
         │
         ▼
  eval_results/golden_eval_{timestamp}.json
```

## Key Design Decisions

| Decision | Choice | Why |
|---|---|---|
| LLM provider | Codex CLI via `llm_client.py` | Centralized wrapper, configurable model via `KG_LLM_MODEL` |
| Product storage | SQLite | Simple, embedded, fast (~3ms queries), no server needed |
| KG storage | In-memory dicts from Excel | Small enough (3012 rows), sub-ms lookups |
| Routing | Deterministic rules, not LLM | Saves a call, faster, predictable, no hallucination |
| SQL generation | LLM with few-shot examples | Flexible queries, handles arbitrary intent combinations |
| Color enforcement | SQL WHERE clause | Cultural constraints are hard rules, not suggestions |
| Conflict resolution | Avoid vetoes, otherwise best rank | Safety-first: if ANY context says avoid, we avoid |
| Eval parallelism | 8 ThreadPoolExecutor workers | Keeps exhaustive 55 × 3 catalog evals practical despite CLI startup overhead |
| Outfit composition | Complementary graphs, not LLM | Deterministic, fast, consistent outfit combos |
