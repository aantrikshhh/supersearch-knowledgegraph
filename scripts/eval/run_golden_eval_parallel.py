"""Rotating-brand golden evaluator for quick SuperSearch regression checks.

Runs each golden query against one selected brand in round-robin order, using
the same SQL, recommendation, and evaluator path as the exhaustive benchmark.
Use this for faster iteration before running the all-brand eval.
"""

import json
import os
import re
import sys
import time
import math
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from knowledge_graph import KnowledgeGraph
from db_query import query_products
from intent_extractor import normalize_intents
from prompts import RECOMMENDATION_SYSTEM, RECOMMENDATION_USER
from router import classify
from config import (
    KG_PATH,
    LLM_TIMEOUT,
    GOLDEN_EVAL_PATH,
    EVAL_RESULTS_DIR,
    BRAND_DB_PATHS,
)
from llm_client import call_llm

BRANDS = ["masaba", "kalki", "aza"]
DB_CHECK = {b: BRAND_DB_PATHS[b] for b in BRANDS}
WORKERS = 8

BATCH_SCORER_SYSTEM = """You are a fashion recommendation evaluator. Score ALL products at once.

For each product, assign a relevance score 0-3:
- 3 = Perfect match — exactly what the user should wear
- 2 = Good match — appropriate and would work well
- 1 = Acceptable — not ideal but not wrong
- 0 = Irrelevant — wrong product type, wrong context, or culturally inappropriate

Consider: product type match, color/material/pattern alignment, cultural constraints.

Return ONLY a JSON array: [{"product_index": 1, "score": <0-3>, "reason": "<one sentence>"}, ...]"""


def call_llm_or_none(prompt, system):
    try:
        return call_llm(prompt, system_prompt=system, timeout=max(180, LLM_TIMEOUT))
    except Exception:
        return None


def dcg(scores):
    return sum(s / math.log2(i + 2) for i, s in enumerate(scores))

def ndcg_at_k(scores, k=5):
    scores = scores[:k]
    actual = dcg(scores)
    ideal = dcg(sorted(scores, reverse=True))
    return actual / ideal if ideal > 0 else 0.0

def mrr(scores):
    for i, s in enumerate(scores):
        if s >= 2:
            return 1.0 / (i + 1)
    return 0.0

def hit_rate(scores, threshold=2):
    return 1.0 if any(s >= threshold for s in scores) else 0.0


def process_single_query(qi, entry, kg, brand):
    """Process one query end-to-end. Thread-safe (no shared mutable state)."""
    query = entry["query"]
    intents = normalize_intents(query, entry["intents"], preserve_existing=True)
    if entry.get("gender_hint") and "gender" not in intents:
        intents["gender"] = entry["gender_hint"]
    rubric = entry.get("scoring_rubric", {})
    expected = entry.get("expected_product_types", {})
    cultural = entry.get("cultural_constraints", "None")

    result = {
        "query_id": qi + 1, "query": query, "brand": brand,
        "intents": intents, "db_products": 0, "recommendations": [],
        "scores": [], "ndcg5": 0.0, "mrr": 0.0, "hit_rate": 0.0,
        "workflow": classify(intents, query).value,
    }

    # Step 1: KG lookup (in-memory, thread-safe reads)
    kg_result = kg.lookup(intents)
    kg_context = kg.format_context(kg_result)

    # Step 2: SQL generation + DB query
    try:
        db_result = query_products(query, intents, kg_context, brand)
        db_products = db_result.get("products", [])
        result["db_products"] = len(db_products)
        result["sql"] = db_result.get("sql", "")
        result["db_trace"] = {
            "brand": db_result.get("brand"),
            "db_path": db_result.get("db_path"),
            "available_types": db_result.get("available_types", []),
            "sql": db_result.get("sql", ""),
            "raw_llm_sql_response": db_result.get("raw_llm_sql_response"),
            "product_count": db_result.get("product_count", 0),
            "timings": db_result.get("timings", {}),
            "errors": db_result.get("errors", []),
            "retries": db_result.get("retries", []),
            "candidates": [
                {
                    "db_rank": i + 1,
                    "product_id": p.get("id"),
                    "title": p.get("title"),
                    "product_type": p.get("product_type"),
                    "colors": p.get("colors", ""),
                    "patterns": p.get("patterns", ""),
                    "materials": p.get("materials", ""),
                    "price": p.get("price", 0),
                }
                for i, p in enumerate(db_products[:20])
            ],
        }
    except Exception as e:
        result["errors"] = [str(e)]
        return result

    if not db_products:
        return result

    # Step 3: LLM recommendation
    products_for_llm = [{
        "db_rank": i + 1,
        "product_id": p["id"], "title": p["title"],
        "product_type": p["product_type"],
        "colors": p.get("colors", ""), "patterns": p.get("patterns", ""),
        "materials": p.get("materials", ""), "price": p.get("price", 0),
    } for i, p in enumerate(db_products[:20])]

    rec_system = RECOMMENDATION_SYSTEM.format(brand_name=brand)
    rec_prompt = RECOMMENDATION_USER.format(
        query=query, intents=json.dumps(intents),
        kg_context=kg_context, count=len(products_for_llm),
        brand_name=brand, products_json=json.dumps(products_for_llm),
    )

    raw = call_llm_or_none(rec_prompt, rec_system)
    recs = []
    if raw:
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            try:
                recs = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    if not recs:
        recs = [{"title": p["title"], "product_id": p["product_id"],
                 "product_type": p["product_type"], "db_rank": p["db_rank"]} for p in products_for_llm[:5]]

    result["recommendation_trace"] = {
        "raw_llm_response": raw,
        "parsed_recommendations": recs,
    }

    # Step 4: Batch score all recommendations
    rec_products = []
    for rec in recs[:5]:
        title = rec.get("title", "Unknown")
        prod = next((p for p in products_for_llm
                     if p.get("product_id") == rec.get("product_id")
                     or p.get("title") == title), {})
        if prod:
            rec_products.append({**prod, "_llm_rec": rec})
        else:
            rec_products.append({"title": title, "_llm_rec": rec})

    products_text = ""
    for i, p in enumerate(rec_products):
        products_text += f"\n{i+1}. [{p.get('product_type','')}] {p.get('title','')} | colors: {p.get('colors','')} | materials: {p.get('materials','')} | price: {p.get('price','')}"

    scorer_prompt = f"""## User Query
{query}

## Scoring Rubric
- Perfect (3): {rubric.get("3_perfect", "")}
- Good (2): {rubric.get("2_good", "")}
- Acceptable (1): {rubric.get("1_acceptable", "")}
- Irrelevant (0): {rubric.get("0_irrelevant", "")}

## Cultural Constraints
{cultural}

## Expected Product Types
- Ideal: {", ".join(expected.get("ideal", []))}
- Avoid: {", ".join(expected.get("avoid", []))}

## Products to Score
{products_text}

Score every product. Return a JSON array."""

    raw_scores = call_llm_or_none(scorer_prompt, BATCH_SCORER_SYSTEM)
    batch_scores = []
    if raw_scores:
        match = re.search(r'\[.*\]', raw_scores, re.DOTALL)
        if match:
            try:
                batch_scores = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    result["scorer_trace"] = {
        "raw_response": raw_scores,
        "parsed_scores": batch_scores,
    }

    scores = []
    scored_recs = []
    for ri, rec in enumerate(recs[:5]):
        prod = rec_products[ri] if ri < len(rec_products) else {}
        score_entry = batch_scores[ri] if ri < len(batch_scores) else {"score": 0, "reason": "missing"}
        s = int(score_entry.get("score", 0))
        scores.append(s)
        scored_recs.append({
            "rank": ri + 1,
            "db_rank": prod.get("db_rank"),
            "product_id": prod.get("product_id") or rec.get("product_id", ""),
            "title": prod.get("title") or rec.get("title", ""),
            "product_type": prod.get("product_type") or rec.get("product_type", ""),
            "colors": prod.get("colors", ""),
            "patterns": prod.get("patterns", ""),
            "materials": prod.get("materials", ""),
            "price": prod.get("price", ""),
            "matched_candidate": bool(prod.get("product_id")),
            "relevance_score": s,
            "reason": score_entry.get("reason", ""),
        })

    result["recommendations"] = scored_recs
    result["scores"] = scores
    result["ndcg5"] = round(ndcg_at_k(scores), 4)
    result["mrr"] = round(mrr(scores), 4)
    result["hit_rate"] = round(hit_rate(scores), 4)
    result["kg_context"] = kg_context

    return result


def run_eval(category_filter=None):
    start_time = time.time()
    print("=" * 70)
    print(f"  PARALLEL GOLDEN EVAL ({WORKERS} workers)")
    print("=" * 70)

    with open(GOLDEN_EVAL_PATH) as f:
        golden = json.load(f)

    if category_filter:
        golden = [q for q in golden if q.get("category") == category_filter]
        print(f"Filtered to category '{category_filter}': {len(golden)} queries")
        if not golden:
            raise ValueError(f"No queries found for category: {category_filter}")
    else:
        print(f"Loaded {len(golden)} queries")

    kg = KnowledgeGraph(KG_PATH)
    available_brands = [b for b in BRANDS if os.path.exists(DB_CHECK[b])]
    if not available_brands:
        raise FileNotFoundError("No brand DBs found. Run scripts/data/build_db.py first.")
    print(f"Brands: {available_brands}")
    print(f"Workers: {WORKERS}")
    print()

    # Prepare jobs
    jobs = []
    for qi, entry in enumerate(golden):
        brand = available_brands[qi % len(available_brands)]
        jobs.append((qi, entry, kg, brand))

    # Run in parallel
    all_results = [None] * len(jobs)
    completed = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(process_single_query, qi, entry, kg, brand): qi
            for qi, entry, kg, brand in jobs
        }

        for future in as_completed(futures):
            qi = futures[future]
            try:
                result = future.result()
                all_results[qi] = result
                completed += 1
                elapsed = time.time() - start_time
                eta = (elapsed / completed) * (len(jobs) - completed) if completed else 0

                scores_str = str(result["scores"])
                print(f"[{completed:2d}/{len(jobs)}] [{result['brand'].upper():6s}] NDCG={result['ndcg5']:.3f} | {result['query'][:50]}")
                print(f"         {scores_str} | ETA: {eta/60:.0f}m", flush=True)
            except Exception as e:
                completed += 1
                print(f"[{completed:2d}/{len(jobs)}] ERROR on query {qi}: {e}", flush=True)
                all_results[qi] = {
                    "query_id": qi + 1, "query": golden[qi]["query"],
                    "brand": available_brands[qi % len(available_brands)],
                    "scores": [], "ndcg5": 0.0, "mrr": 0.0, "hit_rate": 0.0,
                }

    # Filter out None results
    all_results = [r for r in all_results if r is not None]

    # Summary
    total_time = time.time() - start_time
    avg_ndcg = sum(r["ndcg5"] for r in all_results) / len(all_results)
    avg_mrr = sum(r["mrr"] for r in all_results) / len(all_results)
    avg_hit = sum(r["hit_rate"] for r in all_results) / len(all_results)

    print(f"\n{'='*70}")
    print(f"  FINAL RESULTS — {len(all_results)} queries in {total_time/60:.1f} minutes")
    print(f"{'='*70}")
    print(f"  NDCG@5:    {avg_ndcg:.4f}")
    print(f"  MRR:       {avg_mrr:.4f}")
    print(f"  Hit Rate:  {avg_hit:.4f}")

    for brand in available_brands:
        br = [r for r in all_results if r["brand"] == brand]
        if br:
            print(f"\n  [{brand.upper()}] ({len(br)} queries)")
            print(f"    NDCG@5: {sum(r['ndcg5'] for r in br)/len(br):.4f}")
            print(f"    MRR:    {sum(r['mrr'] for r in br)/len(br):.4f}")
            print(f"    Hit:    {sum(r['hit_rate'] for r in br)/len(br):.4f}")

    all_scores = [s for r in all_results for s in r["scores"]]
    if all_scores:
        from collections import Counter
        dist = Counter(all_scores)
        print(f"\n  Score distribution ({len(all_scores)} recommendations):")
        for score in [3, 2, 1, 0]:
            pct = dist.get(score, 0) / len(all_scores) * 100
            bar = "█" * int(pct / 2)
            label = {3: "perfect", 2: "good", 1: "acceptable", 0: "irrelevant"}[score]
            print(f"    {score} ({label:10s}): {dist.get(score,0):3d} ({pct:5.1f}%) {bar}")

    worst = sorted(all_results, key=lambda r: r["ndcg5"])[:5]
    print(f"\n  Worst 5:")
    for r in worst:
        print(f"    [{r['brand'].upper()}] NDCG={r['ndcg5']:.3f} | {r['query'][:55]}")

    best = sorted(all_results, key=lambda r: -r["ndcg5"])[:5]
    print(f"\n  Best 5:")
    for r in best:
        print(f"    [{r['brand'].upper()}] NDCG={r['ndcg5']:.3f} | {r['query'][:55]}")

    # Save
    os.makedirs(EVAL_RESULTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(EVAL_RESULTS_DIR, f"golden_eval_{ts}.json")
    with open(output_path, "w") as f:
        json.dump({
            "summary": {
                "total_queries": len(all_results),
                "avg_ndcg5": round(avg_ndcg, 4),
                "avg_mrr": round(avg_mrr, 4),
                "avg_hit_rate": round(avg_hit, 4),
                "total_time_minutes": round(total_time / 60, 1),
                "workers": WORKERS,
            },
            "results": all_results,
        }, f, indent=2, default=str)
    print(f"\n  Saved to: {output_path}")
    print(f"  Total time: {total_time/60:.1f} minutes")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", help="Run only queries matching this category (e.g., gifting, occasion-based)")
    args = parser.parse_args()
    run_eval(category_filter=args.category)
