"""Place/profession workflow for setting-specific SuperSearch requests.

This path covers offices, museums, temples, airports, bars, schools, and
professional roles. It enriches weather and setting context before using the
standard recommendation pipeline.
"""

from workflows.base import run_standard_pipeline, resolve_gender
from weather_inference import enrich_intents_with_weather


def run(query, intents, brand, session=None, secondary=None):
    """Handle place or profession queries.

    Enriches with weather data if location+month available,
    then runs standard pipeline. Injects secondary activity context.
    """
    secondary = secondary or {}
    enrich_intents_with_weather(intents)
    gender = resolve_gender(intents)

    activity_context = secondary.get("activity_context", "")
    if activity_context and "activity" not in intents:
        intents["activity"] = activity_context

    outfit = run_standard_pipeline(query, intents, brand, gender)

    place = intents.get("place", "")
    profession = intents.get("profession", "")

    if place:
        outfit.styling_notes.insert(0, f"Dressing for: {place}")
    if profession:
        outfit.styling_notes.insert(0, f"Profession: {profession}")

    weather = intents.get("weather", "")
    if weather:
        outfit.styling_notes.append(f"Weather consideration: {weather}")

    if activity_context:
        outfit.styling_notes.append(f"Activity consideration: {activity_context}")

    return outfit
