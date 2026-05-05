# Search Evaluation And Improvement Methodology

This document explains how we test and improve SuperSearch search quality across workflows, configs, KG, prompts, SQL retrieval, product ranking, and conversation memory.

The goal is not to make evals look good. The goal is to mine failures and traces for product improvements while separating true system failures from expected catalog limitations.

## Core Principles

- Test the actual runtime path whenever possible: `ConversationManager -> intent extraction -> router -> workflow -> KG -> SQL -> DB -> outfit builder -> response`.
- Prefer deterministic checks for hard correctness: workflow, clarification, hard constraints, SQL preservation, follow-up/reset behavior, and catalog status.
- Use LLM judges where qualitative judgment adds signal: intent/policy critique, memory critique, KG grounding critique, recommendation/response usefulness.
- Do not treat a catalog gap as a search failure. It is only a failure if the system hides the limitation, hallucinates, drops constraints, or returns unrelated products instead of a clean fallback.
- Fix repeated failure classes, not individual phrasings. Changes should generalize to workflow/config/KG/prompt logic.

## Eval Layers

| Layer | What It Checks | Main Tool |
|---|---|---|
| Unit regression | Known runtime bugs and hard invariants | `python3 -m unittest tests.test_runtime_flow -v` |
| Golden ranking eval | Product relevance ranking, NDCG/MRR/Hit Rate | `scripts/eval/run_golden_eval_parallel.py` |
| Conversation eval | Clarifications, follow-ups, topic switches, runtime traces | `scripts/eval/run_conversation_eval.py` |
| Mining report | Failure clusters, catalog gaps, judge classifications | `scripts/eval/mine_conversation_eval.py` |
| Visualizer | Human inspection of turns, KG, SQL, products, judges | `scripts/eval/conversation_eval_visualizer.py` |

## Conversation Eval Method

For the Aza 500 run, we generated shopper-general flows that could happen on a premium Indian fashion marketplace. Aza was used only as the inventory grounding catalog because it has the largest local DB, not because the queries should overfit to Aza-specific category names.

The generated suite covers:

- occasion, festival, wedding, and ceremony searches
- gifting searches
- post-result refinements
- mid-conversation topic switches and resets
- place/profession searches
- vacation/capsule wardrobe searches
- activity, functional, and health searches
- sparse/general discovery searches
- comparison and accessory follow-ups

The generator is:

```bash
python3 scripts/eval/generate_aza_conversation_flows.py --seed 13
```

Tracked fixtures:

- `data/eval/generated/aza_conversation_500_seed13.json`
- `data/eval/generated/aza_conversation_500_seed13.jsonl`
- `data/eval/generated/aza_conversation_500_judge_sample_seed13.json`

## What Each Turn Logs

Each conversation turn logs enough detail to debug the decision path:

- user query, scenario id, brand, category, turn index
- workflow and router reason
- whether the turn was treated as a follow-up
- follow-up/reset reason
- clarification requirement and question text
- prior intents, new intents, final merged intents, intent diff
- extraction trace, including fallback/error state
- KG lookup context and KG provenance
- SQL, SQL fallback/retry errors, timings, DB candidates
- catalog status classification
- selected primary products and unique counts
- outfit scoring/debug data
- response text, styling notes, suggested follow-ups
- optional LLM judge outputs

This is meant to answer "why did the system do this?" without rerunning the query.

## Catalog Gap Classification

Weak product results are classified before deciding whether to fix code:

| Classification | Meaning | Fix Required? |
|---|---|---|
| `covered` | Catalog returned primary products | No, unless quality is bad |
| `not_applicable` | Clarification turn, no retrieval expected | No |
| `catalog_gap_expected` | Query is reasonable but catalog likely lacks coverage | No product fix; document it |
| `catalog_gap_bad_recovery` | Catalog gap is real, but response/recovery is poor | Yes |
| `retrieval_gap` | Catalog likely has relevant products, but retrieval found none | Yes |
| `workflow_failure` | Wrong route, clarification, or conversation behavior | Yes |
| `grounding_failure` | KG/prompt applied unsupported rules | Yes |
| `eval_issue` | Scenario expectation was wrong or too strict | Fix eval |

This prevents patching around inventory limits.

## LLM Judges

LLM judges are not the only evaluation method. They are used for judgment-heavy review after deterministic checks have run.

Implemented judge types:

- understanding/policy judge: missed or invented constraints, route sanity, clarification sanity
- memory judge: merge/reset/override behavior across turns
- KG grounding judge: whether KG usage matches query and avoids hallucinated cultural specificity
- recommendation/response judge: product usefulness, response grounding, hallucination, follow-up quality

The runner supports batching and parallel judge workers:

```bash
KG_JUDGE_LLM_TIMEOUT=60 python3 scripts/eval/run_conversation_eval.py \
  --eval data/eval/generated/aza_conversation_500_judge_sample_seed13.json \
  --skip-core-llm \
  --skip-response-llm \
  --judge all \
  --judge-batch-size 1 \
  --judge-workers 2 \
  --out eval_results/aza_conversation_500_all_judges_smoke.json \
  --jsonl-trace eval_results/aza_conversation_500_all_judges_smoke.jsonl
```

Current limitation: full judged sweeps through the Codex CLI subprocess path are slow. The code trims judge payloads and supports timeouts, but large judged sweeps should either run in small batches or move the same case payloads to an async Batch API path.

## How We Improve Search From Evals

The loop is:

1. Generate or update shopper flows.
2. Run deterministic conversation eval.
3. Mine failures and catalog gaps.
4. Inspect examples in the visualizer.
5. Map each cluster to an owner area.
6. Make a general fix.
7. Add or update unit regression tests.
8. Rerun the affected slice and full deterministic suite.
9. Run judge smoke or targeted judged samples.
10. Document the result and remaining risks.

Failure clusters map to fix areas:

| Insight | Likely Fix Area |
|---|---|
| Gift query routes to occasion/general | `taxonomy.py`, `intent_extractor.py`, `router.py` |
| Generic wedding silently becomes Hindu wedding | KG aliases, clarification policy, prompts |
| Explicit product search asks too many questions | clarification policy in `conversation.py` |
| Follow-up loses product/gender/budget | `session.py`, follow-up detection, merge logic |
| Topic switch keeps stale context | follow-up/reset detection |
| `not red` or `no sarees` ignored | intent enrichment, SQL hard constraints |
| KG uses wrong entity or misses coverage | KG rows, aliases, `KnowledgeGraph.lookup_with_trace()` output |
| SQL drops budget/product/color constraints | `db_query.py` prompt/validation/fallback |
| Product candidates exist but top results are weak | outfit scoring and ranking |
| Response hides limitation or hallucinates | response prompt and fallback text |

## Aza 500 Eval Performed Here

The Aza 500 run was performed in deterministic fallback mode first. That disables core LLM calls and validates runtime plumbing, deterministic enrichment, routing, KG lookup, SQL fallback, catalog status, and conversation state quickly and repeatably.

Commands:

```bash
python3 scripts/eval/generate_aza_conversation_flows.py --seed 13

python3 -m unittest tests.test_runtime_flow -v

python3 scripts/eval/run_conversation_eval.py \
  --eval data/eval/generated/aza_conversation_500_seed13.json \
  --skip-core-llm \
  --skip-response-llm \
  --out eval_results/aza_conversation_500_deterministic_final.json \
  --jsonl-trace eval_results/aza_conversation_500_deterministic_final.jsonl

python3 scripts/eval/mine_conversation_eval.py \
  eval_results/aza_conversation_500_deterministic_final.json \
  --markdown-out docs/eval_runs/aza_500_final_insights.md

python3 scripts/eval/conversation_eval_visualizer.py \
  --eval eval_results/aza_conversation_500_deterministic_final.json \
  --judge eval_results/aza_conversation_500_all_judges_smoke.json \
  --judge eval_results/aza_conversation_500_memory_judge_smoke.json \
  --out eval_results/aza_conversation_500_visualizer.html
```

Final deterministic result:

- 500/500 scenarios passed
- 655/655 turns passed
- catalog status: `covered=639`, `not_applicable=14`, `retrieval_gap=2`
- unit regressions: 31/31 passing

Judge smoke results:

- all non-memory judges were smoke-tested on one result turn
- memory judge was smoke-tested on a two-turn refinement scenario
- one judge finding identified a real missed constraint: `not too heavy` was not captured as lightweight
- fix added: `not too heavy`, `not heavy`, and `easy to carry` now map to `functional_needs=lightweight`

Artifacts:

- deterministic eval JSON: `eval_results/aza_conversation_500_deterministic_final.json`
- deterministic JSONL trace: `eval_results/aza_conversation_500_deterministic_final.jsonl`
- visualizer: `eval_results/aza_conversation_500_visualizer.html`
- final report: `docs/eval_runs/aza_500_final_insights.md`

Note: `eval_results/` is intentionally gitignored, so local run artifacts are not pushed. The generated fixtures, scripts, and markdown reports are tracked.

## What This Run Fixed

The initial deterministic sweep found clustered failures. General fixes were made instead of query-specific patches:

- explicit wedding product searches can proceed without religion clarification
- `retirement` no longer triggers `men` gender detection
- `not red` becomes a hard color avoidance constraint
- accessory/comparison turns like `What jewellery works with this?` are treated as follow-ups
- `Can I wear this to a temple also?` is treated as a follow-up
- `brunch` no longer maps to `date night`, so cafe brunch routes as a place/setting query
- `not too heavy` maps to lightweight functional need
- KG provenance is logged for every lookup
- outfit scoring/debug data is logged for selected and rejected candidates

## How To Use The Visualizer

Generate it:

```bash
python3 scripts/eval/conversation_eval_visualizer.py \
  --eval eval_results/aza_conversation_500_deterministic_final.json \
  --judge eval_results/aza_conversation_500_all_judges_smoke.json \
  --judge eval_results/aza_conversation_500_memory_judge_smoke.json \
  --out eval_results/aza_conversation_500_visualizer.html
```

Open:

```bash
open eval_results/aza_conversation_500_visualizer.html
```

Useful filters:

- category: isolate gifting, vacation, topic switches, etc.
- workflow: inspect routing behavior
- catalog status: inspect retrieval gaps
- follow-up/result/clarification: inspect conversation behavior
- judge: inspect qualitative findings
- search: query text, SQL, product titles, intents, KG context

## External Methodology References Used

- Aza official site for catalog realism: https://www.azafashions.com/
- Google conversational commerce UX guidance: https://docs.cloud.google.com/retail/docs/conversational-commerce-ux-guide
- Baymard ecommerce UX/search guidance: https://baymard.com/learn/ecommerce-ux-best-practices
- Microsoft RAG evaluator concepts for groundedness/relevance/retrieval: https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/rag-evaluators
