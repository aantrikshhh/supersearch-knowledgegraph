# Aza 500 Conversation Eval Mining Report

Generated: 2026-05-05T19:36:21
Artifact: `eval_results/aza_conversation_500_deterministic_initial.json`

## Summary

- Scenarios: 500
- Passed scenarios: 422
- Failed scenarios: 78
- Turns: 655
- Failed turns: 98

## Grounding Sources

- Workflow spec: `docs/specs/Workflows.docx`
- Aza official category/wedding/menswear pages
- Google Conversational Commerce UX/testing guidance
- Baymard ecommerce search/filter UX guidance
- Microsoft RAG groundedness/relevance/retrieval evaluator concepts

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

- covered: 589
- not_applicable: 65
- retrieval_gap: 1

## Deterministic Failure Buckets

- workflow: 114
- catalog_or_retrieval: 25
- conversation_memory: 8

## Judge Classifications

- No judge outputs attached in this artifact.

## Example Clusters

### catalog_or_retrieval
- `aza_occasion_025` turn 1: gown for reception women not too heavy -> DB products: expected >= 1, got 0
- `aza_occasion_037` turn 1: bandhgala for wedding reception men not too heavy -> DB products: expected >= 1, got 0
- `aza_occasion_005` turn 1: gown for reception women under 20000 -> DB products: expected >= 1, got 0
- `aza_occasion_057` turn 1: bandhgala for wedding reception men breathable for humid weather -> DB products: expected >= 1, got 0
- `aza_occasion_085` turn 1: gown for reception women simple and elegant -> DB products: expected >= 1, got 0
- `aza_occasion_065` turn 1: gown for reception women premium but not bridal -> DB products: expected >= 1, got 0

### conversation_memory
- `aza_comparison_accessory_016` turn 2: Can I wear this to a temple also? -> is_followup: expected True, got False
- `aza_comparison_accessory_015` turn 2: Can I wear this to a temple also? -> is_followup: expected True, got False
- `aza_comparison_accessory_008` turn 2: What jewellery works with this? -> is_followup: expected True, got False
- `aza_comparison_accessory_014` turn 2: Can I wear this to a temple also? -> is_followup: expected True, got False
- `aza_comparison_accessory_005` turn 2: What jewellery works with this? -> is_followup: expected True, got False
- `aza_comparison_accessory_007` turn 2: What jewellery works with this? -> is_followup: expected True, got False

### workflow
- `aza_occasion_025` turn 1: gown for reception women not too heavy -> needs_clarification: expected False, got True
- `aza_occasion_037` turn 1: bandhgala for wedding reception men not too heavy -> needs_clarification: expected False, got True
- `aza_occasion_005` turn 1: gown for reception women under 20000 -> needs_clarification: expected False, got True
- `aza_occasion_057` turn 1: bandhgala for wedding reception men breathable for humid weather -> needs_clarification: expected False, got True
- `aza_occasion_085` turn 1: gown for reception women simple and elegant -> needs_clarification: expected False, got True
- `aza_occasion_065` turn 1: gown for reception women premium but not bridal -> needs_clarification: expected False, got True

## Change Log

This section should be completed after fixes are made in the same run.
