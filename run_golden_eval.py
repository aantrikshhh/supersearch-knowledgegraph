"""Run the pipeline against the golden eval set and compute NDCG scores.

Uses Claude Opus sub-agents to score each recommendation against the rubric,
then computes NDCG@5, MRR, and Hit Rate.
"""

import json
import os
import subprocess
import re
import time
import math
from datetime import datetime
from knowledge_graph import KnowledgeGraph
from db_query import query_products
from prompts import RECOMMENDATION_SYSTEM, RECOMMENDATION_USER

KG_PATH = "Master_Graph.xlsx"
BRANDS = ["masaba", "kalki", "aza"]
DB_CHECK = {
    "masaba": "masaba_products.db",
    "kalki": "kalki_products.db",
    "aza": "aza_products.db",
}

BATCH_SCORER_SYSTEM = """You are a fashion recommendation evaluator. Score ALL products at once.

For each product, assign a relevance score 0-3:
- 3 = Perfect match — exactly what the user should wear
- 2 = Good match — appropriate and would work well
- 1 = Acceptable — not ideal but not wrong
- 0 = Irrelevant — wrong product type, wrong context, or culturally inappropriate

Consider: product type match, color/material/pattern alignment, cultural constraints.

Return ONLY a JSON array of objects: [{"product_index": 1, "score": <0-3>, "reason": "<one sentence>"}, ...]"""

BATCH_SCORER_USER = """## User Query
{query}

## Scoring Rubric
- Perfect (3): {perfect}
- Good (2): {good}
- Acceptable (1): {acceptable}
- Irrelevant (0): {irrelevant}

## Cultural Constraints
{cultural}

## Expected Product Types
- Ideal: {ideal_types}
- Avoid: {avoid_types}

## Products to Score (score ALL of them)
{products_list}

Score every product above. Return a JSON array with score and reason for each."""


def call_claude_batch_scorer(query, rubric, expected, cultural, products):
    """Score ALL products in a single LLM call."""
    products_text = ""
    for i, p in enumerate(products):
        products_text += f"\n{i+1}. [{p.get('product_type','')}] {p.get('title','')} | colors: {p.get('colors','')} | materials: {p.get('materials','')} | price: {p.get('price','')}"

    prompt = BATCH_SCORER_USER.format(
        query=query,
        perfect=rubric.get("3_perfect", ""),
        good=rubric.get("2_good", ""),
        acceptable=rubric.get("1_acceptable", ""),
        irrelevant=rubric.get("0_irrelevant", ""),
        cultural=cultural,
        ideal_types=", ".join(expected.get("ideal", [])),
        avoid_types=", ".join(expected.get("avoid", [])),
        products_list=products_text,
    )

    cmd = ["claude", "-p", prompt, "--output-format", "text",
           "--model", "claude-haiku-4-5-20251001",
           "--system-prompt", BATCH_SCORER_SYSTEM]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        return [{"score": 0, "reason": "Error"} for _ in products]
    raw = result.stdout.strip()
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        try:
            scores = json.loads(match.group())
            return scores
        except json.JSONDecodeError:
            pass
    return [{"score": 0, "reason": "Parse error"} for _ in products]


def call_claude_recommend(query, intents, kg_context, products, brand):
    """Get LLM recommendations from candidate products."""
    products_json = json.dumps(products, indent=2)
    intents_str = json.dumps(intents, indent=2)
    system = RECOMMENDATION_SYSTEM.format(brand_name=brand)
    prompt = RECOMMENDATION_USER.format(
        query=query, intents=intents_str, kg_context=kg_context,
        count=len(products), brand_name=brand, products_json=products_json,
    )
    cmd = ["claude", "-p", prompt, "--output-format", "text",
           "--model", "claude-haiku-4-5-20251001",
           "--system-prompt", system]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        return []
    raw = result.stdout.strip()
    match = re.search(r'\[.*\]', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return []


def dcg(scores):
    """Compute Discounted Cumulative Gain."""
    return sum(s / math.log2(i + 2) for i, s in enumerate(scores))


def ndcg_at_k(scores, k=5):
    """Compute NDCG@k."""
    scores = scores[:k]
    actual = dcg(scores)
    ideal = dcg(sorted(scores, reverse=True))
    if ideal == 0:
        return 0.0
    return actual / ideal


def mrr(scores):
    """Mean Reciprocal Rank — position of first relevant (score >= 2) result."""
    for i, s in enumerate(scores):
        if s >= 2:
            return 1.0 / (i + 1)
    return 0.0


def hit_rate(scores, threshold=1):
    """Did at least one result score >= threshold?"""
    return 1.0 if any(s >= threshold for s in scores) else 0.0


def run_eval():
    print("=" * 70)
    print("  GOLDEN EVAL: Pipeline → NDCG Scoring")
    print("=" * 70)

    with open("golden_eval_set.json") as f:
        golden = json.load(f)
    print(f"Loaded {len(golden)} golden queries")

    kg = KnowledgeGraph(KG_PATH)
    print("Knowledge graph loaded")

    # Check which brand DBs exist
    available_brands = [b for b in BRANDS if os.path.exists(DB_CHECK[b])]
    print(f"Available brand DBs: {available_brands}")

    all_results = []
    all_ndcg = []
    all_mrr = []
    all_hit = []

    for qi, entry in enumerate(golden):
        query = entry["query"]
        intents = entry["intents"]
        rubric = entry.get("scoring_rubric", {})
        expected = entry.get("expected_product_types", {})
        cultural = entry.get("cultural_constraints", "None")
        expected_attrs = entry.get("expected_attributes", {})

        # Pick a brand — rotate through available ones
        brand = available_brands[qi % len(available_brands)]

        print(f"\n[{qi+1:2d}/50] [{brand.upper():6s}] {query[:60]}")

        # Step 1: KG lookup
        kg_result = kg.lookup(intents)
        kg_context = kg.format_context(kg_result)

        # Step 2: SQL generation + DB query
        try:
            db_result = query_products(query, intents, kg_context, brand)
            db_products = db_result["products"]
            sql = db_result.get("sql", "")
            print(f"  DB: {len(db_products)} products | SQL gen: {db_result['timings'].get('sql_generation_ms',0)/1000:.1f}s")
        except Exception as e:
            print(f"  DB ERROR: {e}")
            db_products = []
            sql = ""

        if not db_products:
            print(f"  SKIP — no products returned")
            all_ndcg.append(0.0)
            all_mrr.append(0.0)
            all_hit.append(0.0)
            all_results.append({
                "query_id": qi + 1, "query": query, "brand": brand,
                "intents": intents, "db_products": 0, "recommendations": [],
                "scores": [], "ndcg5": 0.0, "mrr": 0.0, "hit_rate": 0.0,
            })
            continue

        # Step 3: LLM recommendation
        products_for_llm = [{
            "product_id": p["id"], "title": p["title"],
            "product_type": p["product_type"],
            "colors": p.get("colors", ""), "patterns": p.get("patterns", ""),
            "materials": p.get("materials", ""), "price": p.get("price", 0),
        } for p in db_products[:20]]

        print(f"  Recommending...")
        recs = call_claude_recommend(query, intents, kg_context, products_for_llm, brand)
        if not recs:
            recs = [{"title": p["title"], "product_id": p["product_id"],
                     "product_type": p["product_type"]} for p in products_for_llm[:5]]

        # Step 4: Score ALL recommendations in ONE batch call
        rec_products = []
        for rec in recs[:5]:
            title = rec.get("title", "Unknown")
            prod = next((p for p in products_for_llm if p.get("product_id") == rec.get("product_id")
                         or p.get("title") == title), {})
            rec_products.append({
                "title": title,
                "product_type": prod.get("product_type", rec.get("product_type", "")),
                "colors": prod.get("colors", ""),
                "materials": prod.get("materials", ""),
                "patterns": prod.get("patterns", ""),
                "price": prod.get("price", ""),
            })

        print(f"  Scoring {len(rec_products)} products (batch)...")
        batch_scores = call_claude_batch_scorer(query, rubric, expected, cultural, rec_products)

        scores = []
        scored_recs = []
        for ri, rec in enumerate(recs[:5]):
            score_entry = batch_scores[ri] if ri < len(batch_scores) else {"score": 0, "reason": "missing"}
            s = int(score_entry.get("score", 0))
            reason = score_entry.get("reason", "")
            scores.append(s)
            scored_recs.append({
                "rank": ri + 1,
                "title": rec.get("title", ""),
                "product_type": rec_products[ri]["product_type"] if ri < len(rec_products) else "",
                "relevance_score": s,
                "reason": reason,
            })
            print(f"    #{ri+1} [{s}] {rec.get('title','')[:50]} — {reason[:60]}")

        # Compute metrics
        n = ndcg_at_k(scores, k=5)
        m = mrr(scores)
        h = hit_rate(scores, threshold=2)

        all_ndcg.append(n)
        all_mrr.append(m)
        all_hit.append(h)

        print(f"  → NDCG@5={n:.3f} | MRR={m:.3f} | Hit={h:.0f}")

        all_results.append({
            "query_id": qi + 1, "query": query, "brand": brand,
            "intents": intents, "db_products": len(db_products),
            "sql": sql[:200], "kg_context": kg_context,
            "recommendations": scored_recs,
            "scores": scores, "ndcg5": round(n, 4), "mrr": round(m, 4),
            "hit_rate": round(h, 4),
        })

    # Final summary
    print(f"\n{'=' * 70}")
    print(f"  FINAL RESULTS ({len(golden)} queries)")
    print(f"{'=' * 70}")
    avg_ndcg = sum(all_ndcg) / len(all_ndcg) if all_ndcg else 0
    avg_mrr = sum(all_mrr) / len(all_mrr) if all_mrr else 0
    avg_hit = sum(all_hit) / len(all_hit) if all_hit else 0

    print(f"  NDCG@5:    {avg_ndcg:.4f}")
    print(f"  MRR:       {avg_mrr:.4f}")
    print(f"  Hit Rate:  {avg_hit:.4f}")

    # Per-brand breakdown
    for brand in available_brands:
        brand_results = [r for r in all_results if r["brand"] == brand]
        if brand_results:
            b_ndcg = sum(r["ndcg5"] for r in brand_results) / len(brand_results)
            b_mrr = sum(r["mrr"] for r in brand_results) / len(brand_results)
            b_hit = sum(r["hit_rate"] for r in brand_results) / len(brand_results)
            print(f"\n  [{brand.upper()}] ({len(brand_results)} queries)")
            print(f"    NDCG@5: {b_ndcg:.4f} | MRR: {b_mrr:.4f} | Hit Rate: {b_hit:.4f}")

    # Score distribution
    all_scores = [s for r in all_results for s in r["scores"]]
    if all_scores:
        from collections import Counter
        dist = Counter(all_scores)
        print(f"\n  Score distribution (across {len(all_scores)} recommendations):")
        for score in [3, 2, 1, 0]:
            pct = dist.get(score, 0) / len(all_scores) * 100
            bar = "█" * int(pct / 2)
            print(f"    {score} (={'perfect' if score==3 else 'good' if score==2 else 'acceptable' if score==1 else 'irrelevant':10s}): {dist.get(score,0):3d} ({pct:5.1f}%) {bar}")

    # Worst queries
    worst = sorted(all_results, key=lambda r: r["ndcg5"])[:5]
    print(f"\n  Worst 5 queries (lowest NDCG):")
    for r in worst:
        print(f"    [{r['brand'].upper()}] NDCG={r['ndcg5']:.3f} | {r['query'][:55]}")

    # Save results
    os.makedirs("eval_results", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"eval_results/golden_eval_{ts}.json"
    with open(output_path, "w") as f:
        json.dump({
            "summary": {
                "total_queries": len(golden),
                "avg_ndcg5": round(avg_ndcg, 4),
                "avg_mrr": round(avg_mrr, 4),
                "avg_hit_rate": round(avg_hit, 4),
            },
            "results": all_results,
        }, f, indent=2, default=str)
    print(f"\n  Full results saved to: {output_path}")


if __name__ == "__main__":
    run_eval()
