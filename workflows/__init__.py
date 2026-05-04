"""Workflow package for domain-specific SuperSearch request handling.

The router imports these modules lazily to avoid circular imports. Each
workflow receives query + intents + brand, enriches context for its domain, and
then usually delegates to `workflows.base.run_standard_pipeline()`.
"""
