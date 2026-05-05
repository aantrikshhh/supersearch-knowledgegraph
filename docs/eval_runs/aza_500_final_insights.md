# Aza 500 Conversation Eval Mining Report

Generated: 2026-05-05T19:39:52
Artifact: `eval_results/aza_conversation_500_deterministic_final.json`

## Summary

- Scenarios: 500
- Passed scenarios: 500
- Failed scenarios: 0
- Turns: 655
- Failed turns: 0

## Grounding Sources

- Workflow spec: `docs/specs/Workflows.docx`
- Aza official category/wedding/menswear pages: https://www.azafashions.com/
- Google Conversational Commerce UX/testing guidance: https://docs.cloud.google.com/retail/docs/conversational-commerce-ux-guide
- Baymard ecommerce search/filter UX guidance: https://baymard.com/learn/ecommerce-ux-best-practices
- Microsoft RAG groundedness/relevance/retrieval evaluator concepts: https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/rag-evaluators

## Flow Mix

- activity_health: 40 turns
- comparison_accessory: 40 turns
- general: 30 turns
- gifting: 80 turns
- occasion: 100 turns
- place_profession: 50 turns
- refinement: 150 turns
- topic_switch: 120 turns
- vacation: 45 turns

## Catalog Coverage Classification

- covered: 639
- not_applicable: 14
- retrieval_gap: 2

## Deterministic Failure Buckets

- None

## Judge Classifications

- No judge outputs attached in this artifact.

## Example Clusters

- No failures or non-ok judge classifications.
## Change Log

Implemented in this run:

- Added `data/eval/generated/aza_conversation_500_seed13.json` and `.jsonl` with 500 Aza-backed, shopper-general flows.
- Added `scripts/eval/generate_aza_conversation_flows.py` for deterministic suite regeneration.
- Added full runtime trace capture: extraction, follow-up/reset reason, router reason, KG lookup provenance, SQL/DB candidate trace, outfit scoring, catalog status, and response fallback state.
- Added batched LLM judge support in `scripts/eval/run_conversation_eval.py` for understanding, memory, KG grounding, and recommendation/response judging.
- Added `scripts/eval/mine_conversation_eval.py` to cluster deterministic failures, catalog gaps, and judge classifications.
- Added `KG_DISABLE_LLM` support in `llm_client.py` so deterministic eval sweeps cannot accidentally launch Codex subprocesses.
- Fixed generic wedding clarification policy so broad wedding outfit queries ask for wedding context, while explicit product searches like sherwani/gown/saree can proceed without blocking.
- Fixed gender detection to use word boundaries, so words like `retirement` no longer match `men`.
- Added hard `avoid_colour` extraction and deterministic SQL exclusion for user phrases like `not red`.
- Improved follow-up detection for accessory/comparison turns such as `What jewellery works with this?` and `Can I wear this to a temple also?`.
- Removed the over-eager `brunch -> date night` mapping so cafe brunch queries route as place/setting queries.
- Added `not too heavy` / `not heavy` / `easy to carry` as lightweight functional-need signals after the judge smoke found that miss.
- Added KG provenance through `KnowledgeGraph.lookup_with_trace()`.
- Added outfit scoring/debug traces for selected and rejected candidates.
- Expanded runtime regression tests from 25 to 31.

Commands run:

```bash
python3 scripts/eval/generate_aza_conversation_flows.py --seed 13
python3 -m unittest tests.test_runtime_flow -v
python3 scripts/eval/run_conversation_eval.py --eval data/eval/generated/aza_conversation_500_seed13.json --skip-core-llm --skip-response-llm --out eval_results/aza_conversation_500_deterministic_final.json --jsonl-trace eval_results/aza_conversation_500_deterministic_final.jsonl
python3 scripts/eval/mine_conversation_eval.py eval_results/aza_conversation_500_deterministic_final.json --markdown-out docs/eval_runs/aza_500_final_insights.md
KG_JUDGE_LLM_TIMEOUT=60 python3 scripts/eval/run_conversation_eval.py --eval data/eval/generated/aza_conversation_500_judge_sample_seed13.json --limit 1 --skip-core-llm --skip-response-llm --judge all --judge-batch-size 1 --judge-workers 2 --out eval_results/aza_conversation_500_all_judges_smoke.json --jsonl-trace eval_results/aza_conversation_500_all_judges_smoke.jsonl
KG_JUDGE_LLM_TIMEOUT=60 python3 scripts/eval/run_conversation_eval.py --eval data/eval/generated/aza_conversation_500_seed13.json --scenario aza_refinement_075 --skip-core-llm --skip-response-llm --judge memory --judge-batch-size 1 --judge-workers 1 --out eval_results/aza_conversation_500_memory_judge_smoke.json --jsonl-trace eval_results/aza_conversation_500_memory_judge_smoke.jsonl
```

Judge-run note:

- The four judge types are implemented and smoke-tested.
- Full judged sweeps through the Codex CLI subprocess path are slow; the attempted 45- and 90-scenario all-judge samples were killed after proving the payload/latency issue.
- The runner now trims judge payloads and supports `KG_JUDGE_LLM_TIMEOUT`; for larger judged sweeps, run smaller batches or move the same JSON case payloads to an async Batch API path.

Remaining risks:

- The 500-flow deterministic pass validates routing, hard constraints, tracing, fallback SQL, catalog-gap classification, and conversation state under deterministic LLM fallback. It does not prove live LLM extraction/SQL quality over all 500 flows.
- Judge smoke artifacts prove judge plumbing and schemas, but they are not a statistically meaningful judged eval.
- Catalog gap classification is conservative and should be improved with catalog coverage probes per requested product family.
