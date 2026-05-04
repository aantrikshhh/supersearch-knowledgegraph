"""Workflow: health — comfort and feature-based recommendations."""

from workflows.base import run_standard_pipeline, get_comp_graphs


def run(query, intents, brand, session=None, secondary=None):
    """Handle health-driven queries.

    Prioritizes comfort, materials, and fit based on health condition.
    Uses complementary features graph for feature pairing.
    Injects formality/place context from secondary signals when available.
    """
    secondary = secondary or {}
    gender = intents.get("gender", "female")

    # Inject secondary formality context so KG picks appropriate product types
    formality_occasion = secondary.get("formality_context", "")
    if formality_occasion and "occasion" not in intents and "event" not in intents:
        intents["occasion"] = formality_occasion

    place_context = secondary.get("place_context", "")
    if place_context and "place" not in intents:
        intents["place"] = place_context

    outfit = run_standard_pipeline(query, intents, brand, gender)

    health = intents.get("health", "")
    outfit.styling_notes.insert(0, f"Health priority: {health}")

    comp = get_comp_graphs()
    fit_pairs = comp.get_complementary_features("clothing", "fit", "loose")
    if fit_pairs:
        paired = [f"{p['feature_name']}={p['feature_value']}" for p in fit_pairs[:2]]
        outfit.styling_notes.append(f"Complementary features: {', '.join(paired)}")

    return outfit
