"""Infer weather from (location, month) using static table + LLM fallback."""

import json
import time
from config import WEATHER_TABLE, LLM_TIMEOUT
from llm_client import call_llm


def infer(location, month):
    """Get weather for a location + month.

    First tries static lookup, then falls back to LLM.

    Returns:
        dict with {weather, temp, humidity, rain}
    """
    if location and month:
        key = (location.lower().strip(), month.lower().strip())
        if key in WEATHER_TABLE:
            return WEATHER_TABLE[key]

        # Try partial match (city name might have extra words)
        for (loc, mon), data in WEATHER_TABLE.items():
            if loc in key[0] or key[0] in loc:
                if mon == key[1]:
                    return data

    # LLM fallback for unknown locations
    return _llm_infer(location, month)


def _llm_infer(location, month):
    """Use LLM to infer weather when static table has no match."""
    prompt = f"""What is the typical weather in {location} in {month}?
Return ONLY a JSON object with these exact keys:
{{"weather": "summer|winter|rainy|snowy|humid", "temp": "cold|cool|warm|hot", "humidity": "low|moderate|high", "rain": true|false}}"""

    try:
        import re
        raw = call_llm(prompt, timeout=LLM_TIMEOUT)
        match = re.search(r'\{[^{}]+\}', raw)
        if match:
            data = json.loads(match.group())
            return data
    except Exception:
        pass

    return {"weather": "summer", "temp": "warm", "humidity": "moderate", "rain": False}


def enrich_intents_with_weather(intents):
    """If intents have location + month but no weather, infer and add it.

    Modifies intents in place and returns the weather dict.
    """
    if "weather" in intents:
        return None

    location = intents.get("location", "")
    month = intents.get("month", "")

    if location and month:
        weather_data = infer(location, month)
        intents["weather"] = weather_data["weather"]
        return weather_data

    return None
