"""Runtime conversational eval for SuperSearch.

This evaluator drives the production ConversationManager turn-by-turn. It is
separate from the golden product-ranking eval because it checks dialogue
behavior: blocking clarification, session state carryover, follow-up routing,
and the next result set after a refinement.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CONVERSATION_EVAL_PATH, EVAL_RESULTS_DIR
from taxonomy import PRODUCT_TYPE_ALIASES
from conversation import ConversationManager
from session import Session


def load_json(path):
    with open(path) as f:
        return json.load(f)


def product_type_matches(expected, actual, title=""):
    expected = (expected or "").strip().lower()
    actual = (actual or "").strip().lower()
    if not expected or not actual:
        return False
    terms = {expected}
    terms.update(t.lower() for t in PRODUCT_TYPE_ALIASES.get(expected, []))
    title = (title or "").strip().lower()
    return any(
        term == actual
        or term in actual
        or actual in term
        or term.rstrip("s") == actual.rstrip("s")
        or term in title
        for term in terms
    )


def db_debug_for(outfit):
    debug = outfit.db_debug or {}
    if "days" in debug:
        product_count = sum(day.get("product_count", 0) for day in debug.get("days", []))
        sql = "\n\n".join(day.get("sql", "") for day in debug.get("days", []))
        errors = []
        for day in debug.get("days", []):
            errors.extend(day.get("errors", []))
        return {"product_count": product_count, "sql": sql, "errors": errors}
    return {
        "product_count": debug.get("product_count", 0),
        "sql": debug.get("sql", ""),
        "errors": debug.get("errors", []),
    }


def kg_context_for(outfit):
    """Return KG lookup context used by the workflow.

    Standard workflows attach the KG lookup result directly to OutfitResult.
    Vacation builds one child outfit per day, so preserve each day's KG context
    as well as any top-level context present on the combined result.
    """
    context = getattr(outfit, "kg_context", {}) or {}
    day_plans = getattr(outfit, "_day_plans", [])
    if not day_plans:
        return context

    return {
        "combined": context,
        "days": [
            {
                "day": day.get("day"),
                "activity": day.get("activity"),
                "kg_context": getattr(day.get("outfit"), "kg_context", {}) or {},
            }
            for day in day_plans
        ],
    }


def summarize_turn(result):
    outfit = result["outfit"]
    db_debug = db_debug_for(outfit)
    products = [
        {
            "id": p.get("id"),
            "title": p.get("title", ""),
            "product_type": p.get("product_type", ""),
            "colors": p.get("colors", ""),
            "price": p.get("price", ""),
        }
        for p in outfit.primary_products[:5]
    ]
    day_plans = getattr(outfit, "_day_plans", [])
    unique_product_ids = {
        str(p.get("id"))
        for p in outfit.primary_products
        if p.get("id") is not None
    }
    return {
        "workflow": result["workflow"],
        "is_followup": result["is_followup"],
        "needs_clarification": result.get("needs_clarification", False),
        "clarifying_questions": result.get("clarifying_questions", []),
        "suggested_followups": result.get("suggested_followups", []),
        "intents": result["intents"],
        "response_text": result["response_text"],
        "primary_product_count": len(outfit.primary_products),
        "unique_primary_product_count": len(unique_product_ids),
        "primary_products": products,
        "day_plan_count": len(day_plans),
        "db_product_count": db_debug["product_count"],
        "sql": db_debug["sql"],
        "db_errors": db_debug["errors"],
        "kg_context": kg_context_for(outfit),
        "styling_notes": outfit.styling_notes,
    }


def _expect_equal(failures, label, actual, expected):
    if actual != expected:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")


def check_expectations(turn_summary, expect):
    failures = []

    if "workflow" in expect:
        _expect_equal(failures, "workflow", turn_summary["workflow"], expect["workflow"])
    if "needs_clarification" in expect:
        _expect_equal(
            failures,
            "needs_clarification",
            turn_summary["needs_clarification"],
            expect["needs_clarification"],
        )
    if "is_followup" in expect:
        _expect_equal(failures, "is_followup", turn_summary["is_followup"], expect["is_followup"])

    joined_questions = " ".join(turn_summary.get("clarifying_questions", [])).lower()
    response = turn_summary.get("response_text", "").lower()
    for text in expect.get("clarifying_contains", []):
        needle = text.lower()
        if needle not in joined_questions and needle not in response:
            failures.append(f"clarifying text missing {text!r}")

    if "min_suggested_followups" in expect:
        actual = len(turn_summary.get("suggested_followups", []))
        if actual < expect["min_suggested_followups"]:
            failures.append(f"suggested followups: expected >= {expect['min_suggested_followups']}, got {actual}")
    joined_followups = " ".join(turn_summary.get("suggested_followups", [])).lower()
    for text in expect.get("suggested_contains", []):
        if text.lower() not in joined_followups:
            failures.append(f"suggested followups missing {text!r}")

    intents = turn_summary["intents"]
    for key, expected in expect.get("intent_equals", {}).items():
        actual = intents.get(key)
        if actual != expected:
            failures.append(f"intent {key}: expected {expected!r}, got {actual!r}")
    for key, allowed in expect.get("intent_one_of", {}).items():
        actual = intents.get(key)
        if actual not in allowed:
            failures.append(f"intent {key}: expected one of {allowed!r}, got {actual!r}")
    for key, forbidden in expect.get("intent_not_equals", {}).items():
        actual = intents.get(key)
        if actual == forbidden:
            failures.append(f"intent {key}: should not equal {forbidden!r}")
    for key, expected_values in expect.get("intent_contains", {}).items():
        actual = str(intents.get(key, "")).lower()
        if isinstance(expected_values, str):
            expected_values = [expected_values]
        for expected in expected_values:
            if str(expected).lower() not in actual:
                failures.append(f"intent {key}: expected to contain {expected!r}, got {intents.get(key)!r}")
    for key in expect.get("intent_truthy", []):
        if not intents.get(key):
            failures.append(f"intent {key} should be truthy")
    for key in expect.get("intent_absent", []):
        if intents.get(key):
            failures.append(f"intent {key} should be absent/false, got {intents.get(key)!r}")

    if "min_primary_products" in expect:
        actual = turn_summary["primary_product_count"]
        if actual < expect["min_primary_products"]:
            failures.append(f"primary products: expected >= {expect['min_primary_products']}, got {actual}")
    if "max_primary_products" in expect:
        actual = turn_summary["primary_product_count"]
        if actual > expect["max_primary_products"]:
            failures.append(f"primary products: expected <= {expect['max_primary_products']}, got {actual}")
    if "min_unique_primary_products" in expect:
        actual = turn_summary["unique_primary_product_count"]
        if actual < expect["min_unique_primary_products"]:
            failures.append(f"unique primary products: expected >= {expect['min_unique_primary_products']}, got {actual}")
    if "min_day_plans" in expect:
        actual = turn_summary["day_plan_count"]
        if actual < expect["min_day_plans"]:
            failures.append(f"day plans: expected >= {expect['min_day_plans']}, got {actual}")
    if "min_db_products" in expect:
        actual = turn_summary["db_product_count"]
        if actual < expect["min_db_products"]:
            failures.append(f"DB products: expected >= {expect['min_db_products']}, got {actual}")

    products = turn_summary.get("primary_products", [])
    allowed_types = expect.get("product_types_all", [])
    if allowed_types and products:
        for product in products:
            actual_type = product.get("product_type", "")
            title = product.get("title", "")
            if not any(product_type_matches(expected, actual_type, title) for expected in allowed_types):
                failures.append(
                    f"product type {actual_type!r} did not match allowed {allowed_types!r}"
                )
    excluded_types = expect.get("product_types_none", [])
    if excluded_types and products:
        for product in products:
            actual_type = product.get("product_type", "")
            title = product.get("title", "")
            if any(product_type_matches(excluded, actual_type, title) for excluded in excluded_types):
                failures.append(
                    f"product type {actual_type!r} matched excluded {excluded_types!r}"
                )

    sql = turn_summary.get("sql", "")
    sql_lower = sql.lower()
    for text in expect.get("sql_contains", []):
        if text.lower() not in sql_lower:
            failures.append(f"SQL missing {text!r}")
    for text in expect.get("sql_not_contains", []):
        if text.lower() in sql_lower:
            failures.append(f"SQL should not contain {text!r}")

    for text in expect.get("response_contains", []):
        if text.lower() not in response:
            failures.append(f"response missing {text!r}")
    for text in expect.get("response_not_contains", []):
        if text.lower() in response:
            failures.append(f"response should not contain {text!r}")

    return failures


def run_scenario(scenario, skip_response_llm=False, skip_core_llm=False):
    if skip_core_llm:
        import db_query as db_query_module
        import intent_extractor as intent_extractor_module
        import workflows.vacation as vacation_module
        import conversation as conversation_module

        def fail_core_llm(*_args, **_kwargs):
            raise RuntimeError("core LLM disabled for conversation eval")

        db_query_module.call_llm = fail_core_llm
        intent_extractor_module.call_llm = fail_core_llm
        vacation_module.call_llm = fail_core_llm
        conversation_module.call_llm = fail_core_llm

    if skip_response_llm:
        import conversation as conversation_module

        def fail_response_llm(*_args, **_kwargs):
            raise RuntimeError("response LLM disabled for conversation eval")

        conversation_module.call_llm = fail_response_llm

    session = Session(brand=scenario.get("brand", "aza"))
    conv = ConversationManager(session=session, brand=session.brand)
    turns = []
    failures = []

    for idx, turn in enumerate(scenario["turns"], 1):
        started = time.time()
        try:
            result = conv.process(turn["user"])
            summary = summarize_turn(result)
            summary["elapsed_ms"] = round((time.time() - started) * 1000)
            turn_failures = check_expectations(summary, turn.get("expect", {}))
        except Exception as exc:
            summary = {
                "error": str(exc),
                "elapsed_ms": round((time.time() - started) * 1000),
            }
            turn_failures = [f"turn raised exception: {exc}"]

        summary["user"] = turn["user"]
        summary["passed"] = not turn_failures
        summary["failures"] = turn_failures
        turns.append(summary)
        failures.extend(f"turn {idx}: {failure}" for failure in turn_failures)

    return {
        "id": scenario["id"],
        "brand": scenario.get("brand", "aza"),
        "description": scenario.get("description", ""),
        "passed": not failures,
        "failures": failures,
        "turns": turns,
    }


def run_eval(path, scenario_filter=None, skip_response_llm=False, skip_core_llm=False, progress=False):
    scenarios = load_json(path)
    if scenario_filter:
        wanted = set(scenario_filter)
        scenarios = [s for s in scenarios if s["id"] in wanted]

    results = []
    for idx, scenario in enumerate(scenarios, 1):
        if progress:
            print(f"[{idx}/{len(scenarios)}] {scenario['id']}", flush=True)
        results.append(run_scenario(
            scenario,
            skip_response_llm=skip_response_llm,
            skip_core_llm=skip_core_llm,
        ))
    passed = sum(1 for r in results if r["passed"])
    total_turns = sum(len(r["turns"]) for r in results)
    failed_turns = sum(1 for r in results for t in r["turns"] if not t["passed"])
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "eval_path": os.path.abspath(path),
        "summary": {
            "scenarios": len(results),
            "passed_scenarios": passed,
            "failed_scenarios": len(results) - passed,
            "turns": total_turns,
            "failed_turns": failed_turns,
        },
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", default=CONVERSATION_EVAL_PATH)
    parser.add_argument("--scenario", action="append", help="Run only this scenario id; repeatable.")
    parser.add_argument(
        "--skip-response-llm",
        action="store_true",
        help="Use deterministic fallback response text to speed up flow checks.",
    )
    parser.add_argument(
        "--skip-core-llm",
        action="store_true",
        help="Force deterministic intent, SQL, vacation-plan, and response fallbacks.",
    )
    parser.add_argument("--progress", action="store_true", help="Print scenario progress while running.")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    result = run_eval(
        args.eval,
        scenario_filter=args.scenario,
        skip_response_llm=args.skip_response_llm,
        skip_core_llm=args.skip_core_llm,
        progress=args.progress,
    )

    os.makedirs(EVAL_RESULTS_DIR, exist_ok=True)
    out = args.out or os.path.join(
        EVAL_RESULTS_DIR,
        f"conversation_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )
    with open(out, "w") as f:
        json.dump(result, f, indent=2, default=str)

    summary = result["summary"]
    print(
        f"Conversation eval: {summary['passed_scenarios']}/{summary['scenarios']} "
        f"scenarios passed, {summary['failed_turns']} failed turns"
    )
    print(f"Wrote {os.path.abspath(out)}")
    for scenario in result["results"]:
        status = "PASS" if scenario["passed"] else "FAIL"
        print(f"{status} {scenario['id']}")
        for failure in scenario["failures"]:
            print(f"  - {failure}")

    if summary["failed_scenarios"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
