"""Legacy terminal tracer for the pre-SQL SuperSearch KG prototype.

The maintained audit path is `scripts/eval/eval_audit_visualizer.py`. This
older tool is kept to explain and debug the original KG scoring path that used
`legacy/product_matcher.py` against raw catalog JSONs.
"""

import sys
import json
from pathlib import Path
from collections import defaultdict

ROOT_DIR = Path(__file__).resolve().parents[1]
LEGACY_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(LEGACY_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_DIR))

from config import KG_PATH
from taxonomy import INTENT_ALIASES, PRODUCT_TYPE_ALIASES, RELATION_GENDERS
from knowledge_graph import KnowledgeGraph
from brand_adapters import load_catalog
from product_matcher import score_product, _product_type_matches

CATALOG_PATH = "/Users/aant/repos/scraper-infra/data/house_of_masaba_products.json"
BRAND = "masaba"


def trace_query(query, intents, brand=BRAND, catalog_path=CATALOG_PATH):
    """Print a full trace of how a query is processed."""

    print("\n" + "=" * 80)
    print("  KNOWLEDGE GRAPH PIPELINE TRACE")
    print("=" * 80)

    # =========================================================================
    # STAGE 1: INPUT
    # =========================================================================
    print(f"\n{'─' * 80}")
    print("  STAGE 1: INPUT")
    print(f"{'─' * 80}")
    print(f"\n  User Query: \"{query}\"")
    print(f"  Brand:      {brand}")
    print(f"  Raw Intents: {json.dumps(intents, indent=2, default=str)}")

    # =========================================================================
    # STAGE 2: INTENT RESOLUTION & ALIASING
    # =========================================================================
    print(f"\n{'─' * 80}")
    print("  STAGE 2: INTENT RESOLUTION & ALIASING")
    print(f"{'─' * 80}")

    kg = KnowledgeGraph(KG_PATH)

    resolved_intents = {}
    skipped_intents = {}
    for entity, value in intents.items():
        if entity in ("location", "month", "budget", "time"):
            skipped_intents[entity] = value
            continue

        aliases = INTENT_ALIASES.get(entity, {})
        resolved_value = aliases.get(value, value)

        if resolved_value != value:
            print(f"\n  [{entity}] \"{value}\" → aliased to \"{resolved_value}\"")
        else:
            print(f"\n  [{entity}] \"{value}\" → no alias needed")

        key = (entity, resolved_value)
        if key in kg.graph:
            print(f"    ✓ Found in Knowledge Graph ({len(kg.graph[key])} entries)")
            resolved_intents[entity] = resolved_value
        else:
            print(f"    ✗ NOT found in Knowledge Graph")
            print(f"    Available values for '{entity}': {sorted(kg.entity_values.get(entity, set()))}")
            resolved_intents[entity] = resolved_value

    if skipped_intents:
        print(f"\n  Skipped intents (not in KG): {skipped_intents}")
        print("  (location, month, budget, time are used by the LLM but not for KG lookup)")

    # =========================================================================
    # STAGE 3: KNOWLEDGE GRAPH LOOKUP (per intent)
    # =========================================================================
    print(f"\n{'─' * 80}")
    print("  STAGE 3: KNOWLEDGE GRAPH LOOKUP (per intent)")
    print(f"{'─' * 80}")

    per_intent_results = {}
    for entity, value in resolved_intents.items():
        key = (entity, value)
        entries = kg.graph.get(key, [])
        if not entries:
            print(f"\n  [{entity}: {value}] → No entries in graph")
            continue

        print(f"\n  [{entity}: {value}] → {len(entries)} graph entries:")

        by_tag = defaultdict(list)
        for e in entries:
            by_tag[e["tag"]].append(e)

        per_intent_results[(entity, value)] = by_tag

        for tag in sorted(by_tag.keys()):
            items = by_tag[tag]
            rank1 = [i for i in items if i["rank"] == 1]
            rank2 = [i for i in items if i["rank"] == 2]
            rank_neg = [i for i in items if i["rank"] == -1]

            parts = []
            if rank1:
                names = [f"{i['name']}({i['gender'] or 'any'})" for i in rank1]
                parts.append(f"      ✓ Recommended (rank=1): {', '.join(names)}")
            if rank2:
                names = [f"{i['name']}({i['gender'] or 'any'})" for i in rank2]
                parts.append(f"      ~ Acceptable  (rank=2): {', '.join(names)}")
            if rank_neg:
                names = [f"{i['name']}({i['gender'] or 'any'})" for i in rank_neg]
                parts.append(f"      ✗ Avoid       (rank=-1): {', '.join(names)}")

            print(f"    [{tag}]")
            for p in parts:
                print(p)

    # =========================================================================
    # STAGE 4: MERGE & CONFLICT RESOLUTION
    # =========================================================================
    print(f"\n{'─' * 80}")
    print("  STAGE 4: MERGE & CONFLICT RESOLUTION")
    print(f"{'─' * 80}")
    print("\n  Rule: When multiple intents give different ranks for the same item,")
    print("        take the MINIMUM (most restrictive) rank.")
    print("        e.g., if 'place:beach' says dress=rank2 and 'weather:summer' says dress=rank1,")
    print("        the merged rank = min(2,1) = 1 (recommended)")

    # Merge all items across intents
    all_items = defaultdict(lambda: defaultdict(list))
    for (entity, value), by_tag in per_intent_results.items():
        for tag, items in by_tag.items():
            for item in items:
                all_items[tag][item["name"]].append({
                    "rank": item["rank"],
                    "source": f"{entity}:{value}",
                    "gender": item["gender"],
                })

    conflicts_found = False
    for tag in sorted(all_items.keys()):
        for name, sources in all_items[tag].items():
            if len(sources) > 1:
                ranks = [s["rank"] for s in sources]
                if len(set(ranks)) > 1:
                    conflicts_found = True
                    final = min(ranks)
                    source_strs = [f"{s['source']}→rank={s['rank']}" for s in sources]
                    print(f"\n  CONFLICT on [{tag}] \"{name}\":")
                    for s in source_strs:
                        print(f"    - {s}")
                    print(f"    → Resolved to rank={final} (most restrictive)")

    if not conflicts_found:
        print("\n  No conflicts found — all intents agree on item rankings.")

    # Build final merged context
    kg_result = kg.lookup(intents)
    print(f"\n  Final merged Knowledge Graph context:")
    for tag in sorted(kg_result.keys()):
        data = kg_result[tag]
        if data["recommended"] or data["acceptable"] or data["avoid"]:
            print(f"    [{tag}]")
            if data["recommended"]:
                print(f"      ✓ Recommended: {data['recommended']}")
            if data["acceptable"]:
                print(f"      ~ Acceptable:  {data['acceptable']}")
            if data["avoid"]:
                print(f"      ✗ Avoid:       {data['avoid']}")

    # =========================================================================
    # STAGE 5: PRODUCT MATCHING
    # =========================================================================
    print(f"\n{'─' * 80}")
    print("  STAGE 5: PRODUCT MATCHING against {brand} catalog")
    print(f"{'─' * 80}")

    adapter = load_catalog(brand, catalog_path)
    products = adapter.products
    print(f"\n  Loaded {len(products)} products from {brand}")

    # Infer gender
    gender = None
    relation = intents.get("relation", "")
    gender = RELATION_GENDERS.get(relation)
    print(f"  Gender filter: {gender or 'none'}")

    # Show scoring for a few products
    print(f"\n  Scoring breakdown (showing first 5 scored products):")
    print(f"  {'─' * 70}")

    scored = []
    for p in products:
        if gender and p.gender not in (gender, "both"):
            continue
        s = score_product(p, kg_result)
        scored.append((p, s))

    scored.sort(key=lambda x: -x[1])

    shown = 0
    for p, s in scored:
        if shown >= 5:
            break
        if s <= 0:
            continue
        shown += 1

        print(f"\n  Product: \"{p.title}\"")
        print(f"  Type: {p.product_type} | Colors: {p.colors} | Materials: {p.materials} | Patterns: {p.patterns}")

        # Detail the scoring
        products_data = kg_result.get("product", {})
        type_score = 0
        type_reason = "no match"
        for rp in products_data.get("recommended", []):
            if _product_type_matches(p.product_type, rp):
                type_score = 10
                type_reason = f"matches recommended '{rp}'"
                break
        if type_score == 0:
            for ap in products_data.get("acceptable", []):
                if _product_type_matches(p.product_type, ap):
                    type_score = 5
                    type_reason = f"matches acceptable '{ap}'"
                    break
        if type_score == 0:
            for av in products_data.get("avoid", []):
                if _product_type_matches(p.product_type, av):
                    type_score = -20
                    type_reason = f"matches AVOID '{av}'"
                    break

        attr_scores = []
        for tag_name, attr_list in [("colour", p.colors), ("pattern", p.patterns), ("material", p.materials)]:
            data = kg_result.get(tag_name, {})
            rec = data.get("recommended", [])
            avd = data.get("avoid", [])
            for val in attr_list:
                if val in rec or "all" in rec:
                    attr_scores.append((tag_name, val, "+3", "recommended"))
                if val in avd:
                    attr_scores.append((tag_name, val, "-5", "AVOID"))

        print(f"    Product type: {type_score:+d} ({type_reason})")
        for tag_name, val, points, reason in attr_scores:
            print(f"    {tag_name} '{val}': {points} ({reason})")
        print(f"    ─────────────────")
        print(f"    TOTAL SCORE: {s}")

    # Show summary
    positive = [(p, s) for p, s in scored if s > 0]
    zero = [(p, s) for p, s in scored if s == 0]
    negative = [(p, s) for p, s in scored if s < 0]

    print(f"\n  Scoring summary:")
    print(f"    Products with score > 0: {len(positive)}")
    print(f"    Products with score = 0: {len(zero)}")
    print(f"    Products with score < 0: {len(negative)} (filtered out)")
    print(f"    Top 30 sent to LLM for final recommendation")

    # =========================================================================
    # STAGE 6: LLM RECOMMENDATION
    # =========================================================================
    print(f"\n{'─' * 80}")
    print("  STAGE 6: LLM RECOMMENDATION (what gets sent to Codex)")
    print(f"{'─' * 80}")

    kg_context_str = kg.format_context(kg_result)
    print(f"\n  System prompt tells the LLM:")
    print(f"    - Brand: {brand}")
    print(f"    - How to use knowledge graph context")
    print(f"    - To return top 5 with scores and reasoning")

    print(f"\n  User prompt includes:")
    print(f"    1. Original query: \"{query}\"")
    print(f"    2. Extracted intents: {json.dumps(intents)}")
    print(f"    3. Knowledge Graph context:")
    for line in kg_context_str.split("\n"):
        print(f"       {line}")
    print(f"    4. Top {min(20, len(positive))} candidate products with scores")

    print(f"\n  The LLM then uses its fashion knowledge + the KG context to:")
    print(f"    - Re-rank candidates based on holistic fit")
    print(f"    - Consider cultural/practical factors")
    print(f"    - Generate human-readable reasoning for each pick")

    print(f"\n{'=' * 80}")
    print("  END OF TRACE")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        query = sys.argv[1]
        # Try to parse intents from the query
        intents_map = {
            "restaurant": {"place": "restaurant", "weather": "cloudy"},
        }
        # Default intents for demo
        intents = {"place": "restaurant", "weather": "cloudy"}
    else:
        # Demo queries
        demos = [
            {
                "query": "What to wear to a restaurant in cloudy weather?",
                "intents": {"place": "restaurant", "weather": "cloudy"},
            },
            {
                "query": "Outfit ideas for Ganesh Chaturthi in Delhi",
                "intents": {"event": "Ganesh Chaturthi", "location": "Delhi"},
            },
            {
                "query": "What to wear to garden if I practice Islam?",
                "intents": {"place": "garden", "religion": "Islam"},
            },
        ]
        demo_idx = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
        if demo_idx >= len(demos):
            demo_idx = 0
        query = demos[demo_idx]["query"]
        intents = demos[demo_idx]["intents"]

    trace_query(query, intents)
