"""Activity workflow for movement- or task-driven fashion requests.

Handles queries such as yoga, hiking, swimming, cycling, or dance-friendly
clubbing by preserving activity constraints while delegating retrieval and
outfit assembly to the shared workflow base.
"""

from workflows.base import run_standard_pipeline, get_comp_graphs


def run(query, intents, brand, session=None, secondary=None):
    """Handle activity queries like yoga, hiking, swimming.

    If activity implies a specific product (swimming→swimsuit),
    the KG already handles this via the activity entity lookup.
    """
    gender = intents.get("gender", "female")
    outfit = run_standard_pipeline(query, intents, brand, gender)

    activity = intents.get("activity", "")
    outfit.styling_notes.insert(0, f"Activity: {activity}")

    # Get activity-appropriate accessories
    comp = get_comp_graphs()
    activity_occasion_map = {
        "running": "sporty", "hiking": "sporty", "cycling": "sporty",
        "yoga": "sporty", "swimming": "summer", "skiing": "winter",
        "dancing": "party", "traveling": "casual", "fishing": "casual",
    }
    occasion_tag = activity_occasion_map.get(activity, "casual")
    accessories = comp.get_outfit_accessories(occasion_tag, gender)

    if accessories.get("shoes"):
        outfit.shoes = [s["name"] for s in accessories["shoes"][:2]]

    health = intents.get("health", "")
    if health:
        outfit.styling_notes.append(f"Health consideration: {health} — prioritizing comfort")

    return outfit
