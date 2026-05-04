"""General fallback workflow for broad or underspecified SuperSearch queries.

When no specialized workflow clearly owns a request, this path applies light
weather/body enrichment and still runs the standard KG → SQL → outfit pipeline
so the assistant returns usable recommendations.
"""

from workflows.base import run_standard_pipeline, resolve_gender
from weather_inference import enrich_intents_with_weather


def run(query, intents, brand, session=None, secondary=None):
    """Handle general/broad queries with minimal specific intents."""
    enrich_intents_with_weather(intents)
    gender = resolve_gender(intents)
    return run_standard_pipeline(query, intents, brand, gender)
