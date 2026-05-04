"""Workflow: general — weather + bodytype defaults for broad queries."""

from workflows.base import run_standard_pipeline
from weather_inference import enrich_intents_with_weather


def run(query, intents, brand, session=None, secondary=None):
    """Handle general/broad queries with minimal specific intents."""
    enrich_intents_with_weather(intents)
    gender = intents.get("gender", "female")
    return run_standard_pipeline(query, intents, brand, gender)
