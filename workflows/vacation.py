"""Vacation workflow for capsule wardrobes and pack-a-bag requests.

Unlike single-outfit workflows, this module expands a trip query into multiple
daily outfits, applies weather/location inference, tracks color variety, and
returns packing-oriented recommendations.
"""

import json
import re
from workflows.base import get_kg, get_comp_graphs, resolve_gender
from weather_inference import infer as infer_weather
from outfit_builder import form_outfit, OutfitResult
from color_coordinator import check_color_variety
from db_query import query_products
from config import LLM_TIMEOUT
from llm_client import call_llm


def run(query, intents, brand, session=None, secondary=None):
    """Handle vacation/trip queries — plans multiple outfits across days.

    Steps:
    1. Infer weather from location + month
    2. LLM plans daily activities
    3. For each day: KG lookup + DB query + outfit build
    4. Ensure variety across days
    """
    kg = get_kg()
    comp = get_comp_graphs()

    location = intents.get("location", "destination")
    month = intents.get("month", "")
    duration = intents.get("duration", 3)
    gender = resolve_gender(intents)
    occasion = intents.get("occasion", intents.get("event", ""))

    # Step 1: Weather
    weather_data = {}
    if location and month:
        weather_data = infer_weather(location, month)

    weather = intents.get("weather", weather_data.get("weather", ""))

    # Step 2: Plan activities
    activities = _plan_activities(location, duration, occasion, weather, query)

    # Step 3: Build outfit per day
    day_plans = []
    all_used_ids = set()
    db_debug = []

    for i, activity in enumerate(activities[:duration]):
        day_intents = {**intents}
        if ":" in activity:
            parts = activity.split(":", 1)
            activity_type = parts[0].strip().lower()
            day_intents["_day_context"] = parts[1].strip()
        else:
            activity_type = activity.strip().lower()

        # Map activity to appropriate intent
        place_map = {"beach": "beach", "pool": "beach", "club": "club",
                     "sightseeing": "mall", "brunch": "restaurant", "dinner": "restaurant",
                     "temple": "temple", "shopping": "mall"}
        occasion_map = {"ceremony": occasion, "party": "bachelorette",
                        "night out": "date night", "celebration": occasion}
        activity_map = {"hiking": "hiking", "swimming": "swimming",
                        "yoga": "yoga", "dancing": "dancing"}

        for key, val in place_map.items():
            if key in activity_type:
                day_intents["place"] = val
                break
        for key, val in occasion_map.items():
            if key in activity_type:
                day_intents["occasion"] = val
                break
        for key, val in activity_map.items():
            if key in activity_type:
                day_intents["activity"] = val
                break

        if weather:
            day_intents["weather"] = weather

        # KG + DB
        kg_result = kg.lookup(day_intents, gender=gender)
        kg_context = kg.format_context(kg_result)
        db_result = query_products(
            f"outfit for {activity} in {location}",
            day_intents, kg_context, brand,
        )
        db_debug.append(db_result)

        products = [p for p in db_result.get("products", [])
                    if p["id"] not in all_used_ids]

        if products:
            outfit = form_outfit(
                products, kg_result, day_intents,
                comp_graphs=comp, gender=gender, top_n=1,
            )
            for p in outfit.primary_products:
                all_used_ids.add(p["id"])
        else:
            outfit = OutfitResult()

        day_plans.append({
            "day": i + 1,
            "activity": activity,
            "outfit": outfit,
        })

    # Step 4: Check variety
    outfit_colors = []
    for dp in day_plans:
        colors = []
        for p in dp["outfit"].primary_products:
            colors.extend((p.get("colors") or "").split(","))
        outfit_colors.append({"colors": colors})

    variety = check_color_variety(outfit_colors)

    # Build combined result
    result = OutfitResult(
        query=query,
        occasion=occasion or "vacation",
        formality="mixed",
    )
    result.primary_products = []
    result._day_plans = day_plans
    result._variety = variety
    result._weather = weather_data
    result._location = location
    result._duration = duration
    result.db_debug = {"days": db_debug}
    result.styling_notes = [
        f"Packing guide: {duration} days in {location}",
        f"Weather: {weather_data.get('weather', weather)} ({weather_data.get('temp', 'moderate')})",
        f"Color variety score: {variety['variety_score']}",
    ]
    if variety.get("suggestions"):
        result.styling_notes.extend(variety["suggestions"])

    for dp in day_plans:
        result.primary_products.extend(dp["outfit"].primary_products)

    return result


def _plan_activities(location, duration, occasion, weather, query):
    """Use LLM to plan daily activities for a trip."""
    prompt = f"""Plan {duration} daily activities for a trip to {location}.
Context: {occasion or 'leisure trip'}. Weather: {weather or 'moderate'}.
Original query: {query}

Return ONLY a JSON array of {duration} strings, one per day. Each string should be a short activity description.
Example: ["beach day", "temple visit & shopping", "night out at club", "brunch & sightseeing", "ceremony & celebration"]"""

    try:
        raw = call_llm(prompt, timeout=LLM_TIMEOUT)
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass

    # Fallback — generic activities
    defaults = ["casual sightseeing", "dinner out", "shopping & exploration",
                 "relaxation day", "celebration & party"]
    return defaults[:duration]
