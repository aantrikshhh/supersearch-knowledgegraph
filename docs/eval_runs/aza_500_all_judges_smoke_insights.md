# Aza 500 Conversation Eval Mining Report

Generated: 2026-05-05T19:53:15
Artifact: `eval_results/aza_conversation_500_all_judges_smoke.json`

## Summary

- Scenarios: 1
- Passed scenarios: 1
- Failed scenarios: 0
- Turns: 1
- Failed turns: 0

## Grounding Sources

- Workflow spec: `docs/specs/Workflows.docx`
- Aza official category/wedding/menswear pages
- Google Conversational Commerce UX/testing guidance
- Baymard ecommerce search/filter UX guidance
- Microsoft RAG groundedness/relevance/retrieval evaluator concepts

## Flow Mix

- occasion: 1 turns

## Catalog Coverage Classification

- covered: 1

## Deterministic Failure Buckets

- None

## Judge Classifications

- kg:ok: 1
- recommendation_response:response_failure: 1
- understanding:prompt_or_intent_failure: 1

## Example Clusters

### judge:prompt_or_intent_failure
- `aza_occasion_032` turn 1: coord set for office party women not too heavy -> The system correctly routed to the occasion workflow and captured coord set/office party/women, but it missed the key constraint "not too heavy" which should have been reflected in the final intents.

### judge:response_failure
- `aza_occasion_032` turn 1: coord set for office party women not too heavy -> It returns relevant coord-set options but ignores the 'not too heavy' constraint and offers almost no helpful grounding/styling beyond a possibly off 'Formality: casual' tag for an office party.

## Change Log

This section should be completed after fixes are made in the same run.
