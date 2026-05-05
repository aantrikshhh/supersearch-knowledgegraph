"""Runtime conversational eval for SuperSearch.

This evaluator drives the production ConversationManager turn-by-turn. It is
separate from the golden product-ranking eval because it checks dialogue
behavior: blocking clarification, session state carryover, follow-up routing,
and the next result set after a refinement.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import CONVERSATION_EVAL_PATH, EVAL_RESULTS_DIR
from taxonomy import PRODUCT_TYPE_ALIASES
from conversation import ConversationManager
from session import Session
from llm_client import call_llm


def load_json(path):
    with open(path) as f:
        if str(path).endswith(".jsonl"):
            return [json.loads(line) for line in f if line.strip()]
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
        return {
            "product_count": product_count,
            "sql": sql,
            "errors": errors,
            "days": debug.get("days", []),
        }
    return {
        "product_count": debug.get("product_count", 0),
        "sql": debug.get("sql", ""),
        "errors": debug.get("errors", []),
        "brand": debug.get("brand"),
        "db_path": debug.get("db_path"),
        "available_types": debug.get("available_types", []),
        "raw_llm_sql_response": debug.get("raw_llm_sql_response"),
        "timings": debug.get("timings", {}),
        "retries": debug.get("retries", []),
        "candidates": [
            {
                "rank": i + 1,
                "id": p.get("id"),
                "title": p.get("title", ""),
                "product_type": p.get("product_type", ""),
                "colors": p.get("colors", ""),
                "patterns": p.get("patterns", ""),
                "materials": p.get("materials", ""),
                "price": p.get("price", ""),
            }
            for i, p in enumerate(debug.get("products", [])[:20])
        ],
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


def kg_trace_for(outfit):
    trace = getattr(outfit, "kg_trace", {}) or {}
    day_plans = getattr(outfit, "_day_plans", [])
    if not day_plans:
        return trace
    return {
        "combined": trace,
        "days": [
            {
                "day": day.get("day"),
                "activity": day.get("activity"),
                "kg_trace": getattr(day.get("outfit"), "kg_trace", {}) or {},
            }
            for day in day_plans
        ],
    }


def classify_catalog_status(turn_summary, expect=None):
    """Separate catalog coverage gaps from system failures for weak result sets."""
    expect = expect or {}
    db_count = turn_summary.get("db_product_count", 0)
    primary_count = turn_summary.get("primary_product_count", 0)
    if turn_summary.get("needs_clarification"):
        return {"label": "not_applicable", "reason": "clarification_turn"}
    if db_count > 0 and primary_count > 0:
        return {"label": "covered", "reason": "result_products_available"}

    intents = turn_summary.get("intents", {})
    requested = [
        value.strip().lower()
        for value in str(intents.get("product_type") or "").split(",")
        if value.strip()
    ]
    available = {
        str(t).lower()
        for t in turn_summary.get("db_trace", {}).get("available_types", [])
        if t
    }
    allow_catalog_gap = bool(expect.get("allow_catalog_gap"))
    unsupported_terms = [
        "sneaker", "jeans", "denim jacket", "raincoat", "winter coat",
        "activewear", "leggings", "sports bra", "luggage",
    ]
    query_lower = turn_summary.get("user", "").lower()

    if allow_catalog_gap:
        return {"label": "catalog_gap_expected", "reason": "scenario_allows_catalog_gap"}
    if any(term in query_lower for term in unsupported_terms):
        return {"label": "catalog_gap_expected", "reason": "unsupported_product_family"}
    if requested and not any(
        req == db_type or req in db_type or db_type in req
        for req in requested
        for db_type in available
    ):
        return {"label": "catalog_gap_expected", "reason": "requested_type_not_directly_available"}
    if db_count == 0:
        return {"label": "retrieval_gap", "reason": "zero_db_candidates"}
    return {"label": "catalog_gap_bad_recovery", "reason": "db_candidates_without_primary_products"}


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
    db_trace = {
        key: db_debug.get(key)
        for key in (
            "brand", "db_path", "available_types", "raw_llm_sql_response",
            "timings", "retries", "candidates", "days",
        )
        if db_debug.get(key) not in (None, [], {})
    }
    return {
        "session_id": result.get("session_id"),
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
        "db_trace": db_trace,
        "kg_context": kg_context_for(outfit),
        "kg_trace": kg_trace_for(outfit),
        "outfit_debug": getattr(outfit, "outfit_debug", {}) or {},
        "runtime_trace": result.get("trace", {}),
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


JUDGE_SYSTEM = """You are an evaluation judge for a conversational fashion-shopping system.

Judge only from the evidence in each case. Do not assume unavailable catalog data is a system failure.
Return valid JSON with this exact shape:
{"cases":[{"case_id":"...","passed":true,"score":0,"labels":["..."],"classification":"...","rationale":"one concise sentence"}]}

Allowed classifications:
- ok
- catalog_gap_expected
- catalog_gap_bad_recovery
- retrieval_gap
- workflow_failure
- grounding_failure
- prompt_or_intent_failure
- memory_failure
- ranking_failure
- response_failure
- eval_issue
Scores: 3 excellent, 2 acceptable, 1 weak but usable, 0 failure."""


JUDGE_RUBRICS = {
    "understanding": """For each case, judge whether the final intents, workflow, and clarification decision match the shopper's query.
Focus on missed hard constraints, invented constraints, wrong route, and whether asking/proceeding was appropriate.""",
    "memory": """For each multi-turn case, judge whether the system correctly merged, reset, or overrode conversation context.
Check stale constraints, lost constraints, and topic switches.""",
    "kg": """For each case, judge whether KG usage is grounded in the query/intents.
Check wrong entity keys, missing useful KG coverage, hallucinated cultural specificity, and ignored avoid/recommend rules.""",
    "recommendation_response": """For each case, judge whether top products and the response are useful and grounded.
Do not mark catalog limitations as failures if the response exposes the limitation and offers reasonable adjacent alternatives.""",
}


def _extract_json_object(text):
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    return json.loads(match.group())


def _case_payloads(results, judge_name):
    cases = []
    for scenario in results:
        turns = scenario.get("turns", [])
        for idx, turn in enumerate(turns):
            case_id = f"{scenario['id']}::turn_{idx + 1}"
            base = {
                "case_id": case_id,
                "scenario_id": scenario["id"],
                "brand": scenario.get("brand", "aza"),
                "category": scenario.get("category"),
                "user": turn.get("user"),
                "workflow": turn.get("workflow"),
                "needs_clarification": turn.get("needs_clarification"),
                "clarifying_questions": turn.get("clarifying_questions", []),
                "suggested_followups": turn.get("suggested_followups", []),
                "intents": turn.get("intents", {}),
                "catalog_status": turn.get("catalog_status", {}),
                "deterministic_failures": turn.get("failures", []),
                "runtime_trace": _compact_runtime_trace(turn.get("runtime_trace", {})),
            }
            if judge_name == "understanding":
                cases.append(base)
            elif judge_name == "memory" and idx > 0:
                prev = turns[idx - 1]
                payload = dict(base)
                payload["previous_turn"] = {
                    "user": prev.get("user"),
                    "workflow": prev.get("workflow"),
                    "intents": prev.get("intents", {}),
                    "needs_clarification": prev.get("needs_clarification"),
                }
                cases.append(payload)
            elif judge_name == "kg" and not turn.get("needs_clarification"):
                payload = dict(base)
                payload["kg_context"] = turn.get("kg_context", {})
                payload["kg_trace"] = _compact_kg_trace(turn.get("kg_trace", {}))
                cases.append(payload)
            elif judge_name == "recommendation_response" and not turn.get("needs_clarification"):
                payload = dict(base)
                payload.update({
                    "primary_product_count": turn.get("primary_product_count"),
                    "db_product_count": turn.get("db_product_count"),
                    "primary_products": turn.get("primary_products", []),
                    "response_text": turn.get("response_text", ""),
                    "styling_notes": turn.get("styling_notes", []),
                    "outfit_debug": _compact_outfit_debug(turn.get("outfit_debug", {})),
                })
                cases.append(payload)
    return cases


def _compact_runtime_trace(trace):
    if not trace:
        return {}
    extraction = trace.get("extraction", {})
    return {
        "prior_turn_count": trace.get("prior_turn_count"),
        "prior_workflow": trace.get("prior_workflow"),
        "is_followup": trace.get("is_followup"),
        "followup_reason": trace.get("followup_reason"),
        "merge_mode": trace.get("merge_mode"),
        "new_intents": trace.get("new_intents", {}),
        "final_intents": trace.get("final_intents", {}),
        "intent_diff": trace.get("intent_diff", {}),
        "extraction": {
            "parsed_intents": extraction.get("parsed_intents", {}),
            "final_intents": extraction.get("final_intents", {}),
            "used_fallback": extraction.get("used_fallback"),
            "error": extraction.get("error"),
        },
        "router": trace.get("router", {}),
        "clarification": trace.get("clarification", {}),
    }


def _compact_kg_trace(trace):
    if not trace:
        return {}
    if "days" in trace:
        return {
            "days": [
                {
                    "day": day.get("day"),
                    "activity": day.get("activity"),
                    "kg_trace": _compact_kg_trace(day.get("kg_trace", {})),
                }
                for day in trace.get("days", [])[:5]
            ]
        }
    return {
        "lookup_keys": trace.get("lookup_keys", [])[:12],
        "missing_keys": trace.get("missing_keys", [])[:12],
        "conflicts": trace.get("conflicts", [])[:8],
        "result_summary": trace.get("result_summary", {}),
    }


def _compact_outfit_debug(debug):
    if not debug:
        return {}
    return {
        "candidate_count": debug.get("candidate_count"),
        "requested_types": debug.get("requested_types", []),
        "recommended_types": debug.get("recommended_types", []),
        "acceptable_types": debug.get("acceptable_types", []),
        "kg_avoided_types": debug.get("kg_avoided_types", []),
        "user_avoided_types": debug.get("user_avoided_types", []),
        "scored_candidates": debug.get("scored_candidates", [])[:5],
        "rejected_candidates": debug.get("rejected_candidates", [])[:5],
        "selected_product_ids": debug.get("selected_product_ids", []),
    }


def _run_judge_batch(judge_name, cases):
    prompt = (
        f"Judge name: {judge_name}\n\n"
        f"Rubric:\n{JUDGE_RUBRICS[judge_name]}\n\n"
        "Cases JSON:\n"
        f"{json.dumps(cases, default=str)}\n\n"
        "Return one result per case_id. Return JSON only."
    )
    judge_timeout = int(os.environ.get("KG_JUDGE_LLM_TIMEOUT", "90"))
    raw = call_llm(prompt, system_prompt=JUDGE_SYSTEM, timeout=judge_timeout)
    parsed = _extract_json_object(raw)
    if not parsed or "cases" not in parsed:
        raise ValueError("judge did not return {'cases': [...]}")
    by_id = {}
    for item in parsed.get("cases", []):
        if item.get("case_id"):
            item["judge"] = judge_name
            by_id[item["case_id"]] = item
    missing = [case["case_id"] for case in cases if case["case_id"] not in by_id]
    if missing:
        raise ValueError(f"judge omitted case ids: {missing[:5]}")
    return by_id


def attach_judges(result, judge_names, batch_size=10, workers=6):
    if not judge_names:
        return result

    judge_outputs = {name: {} for name in judge_names}
    judge_errors = []
    jobs = []
    for judge_name in judge_names:
        cases = _case_payloads(result["results"], judge_name)
        for i in range(0, len(cases), batch_size):
            jobs.append((judge_name, cases[i:i + batch_size]))

    if not jobs:
        result["judge_summary"] = {"judged_cases": 0, "errors": []}
        return result

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(_run_judge_batch, judge_name, cases): (judge_name, cases)
            for judge_name, cases in jobs
        }
        for future in as_completed(future_map):
            judge_name, cases = future_map[future]
            try:
                judge_outputs[judge_name].update(future.result())
            except Exception as exc:
                judge_errors.append({
                    "judge": judge_name,
                    "case_ids": [case["case_id"] for case in cases],
                    "error": str(exc),
                })
                if len(cases) > 1:
                    for case in cases:
                        try:
                            judge_outputs[judge_name].update(_run_judge_batch(judge_name, [case]))
                        except Exception as retry_exc:
                            judge_errors.append({
                                "judge": judge_name,
                                "case_ids": [case["case_id"]],
                                "error": f"retry failed: {retry_exc}",
                            })

    for scenario in result["results"]:
        for idx, turn in enumerate(scenario.get("turns", [])):
            case_id = f"{scenario['id']}::turn_{idx + 1}"
            turn["judges"] = {
                judge_name: outputs[case_id]
                for judge_name, outputs in judge_outputs.items()
                if case_id in outputs
            }

    judged_cases = sum(len(outputs) for outputs in judge_outputs.values())
    result["judge_summary"] = {
        "judge_names": judge_names,
        "judged_cases": judged_cases,
        "errors": judge_errors,
    }
    return result


def run_scenario(scenario, skip_response_llm=False, skip_core_llm=False):
    old_disable_llm = os.environ.get("KG_DISABLE_LLM")
    if skip_core_llm:
        os.environ["KG_DISABLE_LLM"] = "1"
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

    try:
        for idx, turn in enumerate(scenario["turns"], 1):
            started = time.time()
            try:
                result = conv.process(turn["user"])
                summary = summarize_turn(result)
                summary["elapsed_ms"] = round((time.time() - started) * 1000)
                turn_failures = check_expectations(summary, turn.get("expect", {}))
                summary["user"] = turn["user"]
                summary["catalog_status"] = classify_catalog_status(summary, turn.get("expect", {}))
                non_blocking = []
                if summary["catalog_status"]["label"] == "catalog_gap_expected":
                    product_failures = [
                        failure for failure in turn_failures
                        if failure.startswith("primary products:")
                        or failure.startswith("unique primary products:")
                        or failure.startswith("DB products:")
                        or failure.startswith("product type ")
                    ]
                    if product_failures and len(product_failures) == len(turn_failures):
                        non_blocking = product_failures
                        turn_failures = []
            except Exception as exc:
                summary = {
                    "error": str(exc),
                    "elapsed_ms": round((time.time() - started) * 1000),
                }
                turn_failures = [f"turn raised exception: {exc}"]
                non_blocking = []

            summary["user"] = turn["user"]
            summary["passed"] = not turn_failures
            summary["failures"] = turn_failures
            summary["non_blocking_findings"] = non_blocking
            turns.append(summary)
            failures.extend(f"turn {idx}: {failure}" for failure in turn_failures)
    finally:
        if skip_core_llm:
            if old_disable_llm is None:
                os.environ.pop("KG_DISABLE_LLM", None)
            else:
                os.environ["KG_DISABLE_LLM"] = old_disable_llm

    return {
        "id": scenario["id"],
        "brand": scenario.get("brand", "aza"),
        "category": scenario.get("category", ""),
        "source_tags": scenario.get("source_tags", []),
        "description": scenario.get("description", ""),
        "passed": not failures,
        "failures": failures,
        "turns": turns,
    }


def run_eval(path, scenario_filter=None, skip_response_llm=False,
             skip_core_llm=False, progress=False, limit=None):
    scenarios = load_json(path)
    if scenario_filter:
        wanted = set(scenario_filter)
        scenarios = [s for s in scenarios if s["id"] in wanted]
    if limit:
        scenarios = scenarios[:limit]

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
            "catalog_status_counts": _catalog_status_counts(results),
        },
        "results": results,
    }


def _catalog_status_counts(results):
    counts = {}
    for result in results:
        for turn in result.get("turns", []):
            label = turn.get("catalog_status", {}).get("label", "unknown")
            counts[label] = counts.get(label, 0) + 1
    return counts


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
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N scenarios after filtering.")
    parser.add_argument(
        "--judge",
        choices=["none", "understanding", "memory", "kg", "recommendation_response", "all"],
        action="append",
        default=None,
        help="Attach batched LLM judge outputs. Repeatable; use 'all' for all judges.",
    )
    parser.add_argument("--judge-batch-size", type=int, default=10)
    parser.add_argument("--judge-workers", type=int, default=6)
    parser.add_argument("--jsonl-trace", default=None, help="Optional per-turn JSONL trace output path.")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    result = run_eval(
        args.eval,
        scenario_filter=args.scenario,
        skip_response_llm=args.skip_response_llm,
        skip_core_llm=args.skip_core_llm,
        progress=args.progress,
        limit=args.limit,
    )

    requested_judges = args.judge or ["none"]
    if "all" in requested_judges:
        judge_names = ["understanding", "memory", "kg", "recommendation_response"]
    else:
        judge_names = [j for j in requested_judges if j != "none"]
    if judge_names:
        result = attach_judges(
            result,
            judge_names,
            batch_size=args.judge_batch_size,
            workers=args.judge_workers,
        )

    os.makedirs(EVAL_RESULTS_DIR, exist_ok=True)
    out = args.out or os.path.join(
        EVAL_RESULTS_DIR,
        f"conversation_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
    )
    with open(out, "w") as f:
        json.dump(result, f, indent=2, default=str)

    if args.jsonl_trace:
        with open(args.jsonl_trace, "w") as f:
            for scenario in result["results"]:
                for idx, turn in enumerate(scenario.get("turns", [])):
                    payload = {
                        "scenario_id": scenario["id"],
                        "brand": scenario.get("brand"),
                        "category": scenario.get("category"),
                        "turn_index": idx + 1,
                        **turn,
                    }
                    f.write(json.dumps(payload, default=str) + "\n")

    summary = result["summary"]
    print(
        f"Conversation eval: {summary['passed_scenarios']}/{summary['scenarios']} "
        f"scenarios passed, {summary['failed_turns']} failed turns"
    )
    print(f"Wrote {os.path.abspath(out)}")
    if args.jsonl_trace:
        print(f"Wrote JSONL trace {os.path.abspath(args.jsonl_trace)}")
    if result.get("judge_summary"):
        js = result["judge_summary"]
        print(f"Judge outputs: {js['judged_cases']} cases, {len(js.get('errors', []))} errors")
    for scenario in result["results"]:
        status = "PASS" if scenario["passed"] else "FAIL"
        print(f"{status} {scenario['id']}")
        for failure in scenario["failures"]:
            print(f"  - {failure}")

    if summary["failed_scenarios"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
