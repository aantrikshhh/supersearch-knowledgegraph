"""Legacy spreadsheet-based eval pipeline for the first KG prototype.

The maintained benchmark uses JSON golden cases plus SQL retrieval in
`scripts/eval/`. This module remains as historical reference for the earlier
flow that read `data/raw/Brand_Queries.xlsx` and scored raw catalog products
with `legacy/product_matcher.py`.
"""

import json
import time
import os
import re
import sys
import openpyxl
from pathlib import Path
from datetime import datetime
from tqdm import tqdm

ROOT_DIR = Path(__file__).resolve().parents[1]
LEGACY_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(LEGACY_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_DIR))

from knowledge_graph import KnowledgeGraph
from brand_adapters import load_catalog
from product_matcher import match_products
from prompts import (
    INTENT_EXTRACTION_SYSTEM, INTENT_EXTRACTION_USER,
    RECOMMENDATION_SYSTEM, RECOMMENDATION_USER,
)
from config import KG_PATH
from llm_client import call_llm


CATALOG_PATHS = {
    "masaba": "/Users/aant/repos/scraper-infra/data/house_of_masaba_products.json",
    "kalki": "/Users/aant/repos/scraper-infra/data/kalki_fashion_products.json",
    "aza": "/Users/aant/repos/scraper-infra/data/aza_fashions_products.json",
}

BRAND_SHEET_MAP = {
    "masaba": "Masaba",
    "kalki": "Kalki",
    "aza": "Aza",
}


def parse_preextracted_intents(intent_str):
    """Parse pre-extracted intent string like '{ place: cafe, weather: cloudy }' into dict."""
    intent_str = intent_str.strip()
    if intent_str.startswith("{"):
        intent_str = intent_str[1:]
    if intent_str.endswith("}"):
        intent_str = intent_str[:-1]

    intents = {}
    for pair in intent_str.split(","):
        pair = pair.strip()
        if ":" in pair:
            key, value = pair.split(":", 1)
            intents[key.strip()] = value.strip()
    return intents


def call_legacy_llm(prompt, system_prompt=None):
    """Call the shared Codex CLI wrapper used by the maintained pipeline."""
    start = time.time()
    text = call_llm(prompt, system_prompt=system_prompt, timeout=180)
    elapsed = (time.time() - start) * 1000
    return text.strip(), elapsed


def extract_intents_llm(query):
    """Use the shared LLM wrapper to extract intents from a query."""
    prompt = INTENT_EXTRACTION_USER.format(query=query)
    text, elapsed = call_legacy_llm(prompt, system_prompt=INTENT_EXTRACTION_SYSTEM)

    json_match = re.search(r'\{[^{}]+\}', text)
    if json_match:
        try:
            return json.loads(json_match.group()), elapsed
        except json.JSONDecodeError:
            pass
    return {}, elapsed


def generate_recommendation(query, intents, kg_context_str, candidates, brand_name):
    """Use the shared LLM wrapper to generate final product recommendations."""
    products_for_llm = []
    for product, score in candidates[:20]:
        products_for_llm.append({
            "product_id": product.id,
            "title": product.title,
            "product_type": product.product_type,
            "colors": product.colors,
            "patterns": product.patterns,
            "materials": product.materials,
            "price": product.price,
            "match_score": round(score, 1),
            "description": product.description[:200] if product.description else "",
        })

    intents_str = json.dumps(intents, indent=2)
    products_json = json.dumps(products_for_llm, indent=2)

    system = RECOMMENDATION_SYSTEM.format(brand_name=brand_name)
    prompt = RECOMMENDATION_USER.format(
        query=query,
        intents=intents_str,
        kg_context=kg_context_str,
        count=len(products_for_llm),
        brand_name=brand_name,
        products_json=products_json,
    )

    text, elapsed = call_legacy_llm(prompt, system_prompt=system)

    json_match = re.search(r'\[.*\]', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group()), elapsed
        except json.JSONDecodeError:
            pass
    return [], elapsed


def run_single_query(query, intents, kg, catalog_products, brand_name,
                     use_llm_intents=False, use_llm_recommendation=True):
    """Run the full pipeline for a single query.

    Returns a result dict with all intermediate and final outputs.
    """
    result = {
        "query": query,
        "brand": brand_name,
        "preextracted_intents": intents,
        "timings": {},
        "errors": [],
    }

    # Step 1: Intent extraction
    if use_llm_intents:
        llm_intents, t = extract_intents_llm(query)
        result["llm_intents"] = llm_intents
        result["timings"]["intent_extraction_ms"] = round(t)
        active_intents = llm_intents
    else:
        active_intents = intents

    result["active_intents"] = active_intents

    # Infer gender from intents
    gender = None
    relation = active_intents.get("relation", "")
    if relation in ("mom", "sister", "niece", "aunt", "grandmother"):
        gender = "female"
    elif relation in ("dad", "brother", "nephew", "uncle", "grandfather"):
        gender = "male"

    # Step 2: Knowledge graph lookup
    start = time.time()
    kg_result = kg.lookup(active_intents, gender=gender)
    kg_context_str = kg.format_context(kg_result)
    result["timings"]["kg_lookup_ms"] = round((time.time() - start) * 1000)
    result["kg_context"] = {tag: data for tag, data in kg_result.items()}
    result["kg_context_formatted"] = kg_context_str

    # Step 3: Product matching
    start = time.time()
    candidates = match_products(catalog_products, kg_result, gender=gender, top_n=30)
    result["timings"]["product_matching_ms"] = round((time.time() - start) * 1000)
    result["candidate_count"] = len(candidates)
    result["top_candidates"] = [
        {"id": p.id, "title": p.title, "type": p.product_type, "score": round(s, 1)}
        for p, s in candidates[:10]
    ]

    # Step 4: LLM recommendation
    if use_llm_recommendation and candidates:
        try:
            recs, t = generate_recommendation(
                query, active_intents, kg_context_str, candidates, brand_name
            )
            result["recommendations"] = recs
            result["timings"]["recommendation_ms"] = round(t)
        except TimeoutError:
            result["errors"].append("Recommendation LLM timed out")
            result["recommendations"] = [
                {"product_id": p.id, "title": p.title, "score": round(s, 1),
                 "reasoning": f"Matched by KG: {p.product_type}"}
                for p, s in candidates[:5]
            ]
        except Exception as e:
            result["errors"].append(f"Recommendation LLM error: {str(e)}")
            result["recommendations"] = [
                {"product_id": p.id, "title": p.title, "score": round(s, 1),
                 "reasoning": f"Matched by KG: {p.product_type}"}
                for p, s in candidates[:5]
            ]
    else:
        result["recommendations"] = result["top_candidates"][:5]

    return result


def load_queries(xlsx_path, brand):
    """Load queries and pre-extracted intents from Brand_Queries.xlsx."""
    sheet_name = BRAND_SHEET_MAP.get(brand.lower(), brand)
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb[sheet_name]

    queries = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[1]:
            continue
        query = row[1]
        intent_str = row[2] if len(row) > 2 and row[2] else "{}"
        intents = parse_preextracted_intents(intent_str)
        queries.append({"query": query, "intents": intents})

    wb.close()
    return queries


def run_eval(brand, kg_path=KG_PATH,
             queries_path=str(ROOT_DIR / "data" / "raw" / "Brand_Queries.xlsx"),
             catalog_path=None, max_queries=None, use_llm_intents=False,
             use_llm_recommendation=True):
    """Run the full eval for a brand.

    Args:
        brand: "masaba", "kalki", or "aza"
        kg_path: path to data/graph/Master_Graph.xlsx
        queries_path: path to data/raw/Brand_Queries.xlsx
        catalog_path: path to product catalog JSON (auto-detected if None)
        max_queries: limit number of queries to process
        use_llm_intents: whether to use LLM for intent extraction
        use_llm_recommendation: whether to use LLM for final recommendation
    """
    brand_lower = brand.lower()
    if not catalog_path:
        catalog_path = CATALOG_PATHS.get(brand_lower)
        if not catalog_path:
            raise ValueError(f"No catalog path for brand: {brand}")

    print(f"\n{'='*60}")
    print(f"  EVAL: {brand.upper()}")
    print(f"{'='*60}")

    # Load knowledge graph
    print("Loading knowledge graph...")
    kg = KnowledgeGraph(kg_path)
    print(f"  Loaded {sum(len(v) for v in kg.graph.values())} graph entries")

    # Load catalog
    print(f"Loading {brand} catalog...")
    max_products = 50000 if brand_lower == "aza" else None
    adapter = load_catalog(brand_lower, catalog_path, max_products=max_products)
    products = adapter.products
    print(f"  Loaded {len(products)} products")

    # Load queries
    print("Loading queries...")
    queries = load_queries(queries_path, brand)
    if max_queries:
        queries = queries[:max_queries]
    print(f"  Loaded {len(queries)} queries")

    # Run pipeline
    results = []
    for i, q in enumerate(tqdm(queries, desc=f"Processing {brand}")):
        result = run_single_query(
            query=q["query"],
            intents=q["intents"],
            kg=kg,
            catalog_products=products,
            brand_name=brand,
            use_llm_intents=use_llm_intents,
            use_llm_recommendation=use_llm_recommendation,
        )
        result["query_id"] = i + 1
        results.append(result)

    # Save results
    os.makedirs(ROOT_DIR / "eval_results", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = str(ROOT_DIR / "eval_results" / f"{brand_lower}_{timestamp}.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Print summary
    total_candidates = sum(r["candidate_count"] for r in results)
    zero_candidates = sum(1 for r in results if r["candidate_count"] == 0)
    avg_candidates = total_candidates / len(results) if results else 0

    print(f"\n--- {brand.upper()} EVAL SUMMARY ---")
    print(f"  Queries processed: {len(results)}")
    print(f"  Avg candidates per query: {avg_candidates:.1f}")
    print(f"  Queries with 0 candidates: {zero_candidates}")
    print(f"  Results saved to: {output_path}")

    if results:
        print(f"\n--- Sample Result ---")
        sample = results[0]
        print(f"  Query: {sample['query']}")
        print(f"  Intents: {sample['active_intents']}")
        print(f"  KG Context: {sample['kg_context_formatted'][:200]}...")
        print(f"  Candidates: {sample['candidate_count']}")
        if sample.get("recommendations"):
            print(f"  Top recommendation: {sample['recommendations'][0]}")

    return results


if __name__ == "__main__":
    import sys

    brand = sys.argv[1] if len(sys.argv) > 1 else "masaba"
    max_q = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    use_llm = "--llm" in sys.argv
    llm_intents_only = "--llm-intents" in sys.argv

    results = run_eval(
        brand=brand,
        max_queries=max_q,
        use_llm_intents=llm_intents_only,
        use_llm_recommendation=use_llm,
    )
