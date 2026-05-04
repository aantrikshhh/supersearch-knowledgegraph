"""Route extracted intents to the appropriate workflow."""

from enum import Enum


class WorkflowType(Enum):
    VACATION = "vacation"
    PLACE_PROFESSION = "place_profession"
    ACTIVITY = "activity"
    OCCASION = "occasion"
    HEALTH = "health"
    GENERAL = "general"
    GIFTING = "gifting"


def classify(intents, query=""):
    """Classify intents into a workflow type using deterministic rules.

    Priority order matters — first match wins.
    """
    query_lower = query.lower()

    # 1. Vacation — explicit packing/trip signals
    if intents.get("_is_vacation"):
        return WorkflowType.VACATION
    if intents.get("duration"):
        return WorkflowType.VACATION
    if intents.get("activity") == "vacation":
        return WorkflowType.VACATION

    # 2. Gifting — buying for someone else
    if intents.get("_is_gift"):
        return WorkflowType.GIFTING

    # 3. Occasion/Event — the most common flow
    if "occasion" in intents or "event" in intents:
        return WorkflowType.OCCASION

    # 4. Activity — specific activity (not vacation)
    if "activity" in intents:
        return WorkflowType.ACTIVITY

    # 5. Health — comfort/health is primary concern
    if "health" in intents and "place" not in intents and "occasion" not in intents:
        return WorkflowType.HEALTH

    # 6. Place/Profession — going somewhere or dressing for a role
    if "place" in intents or "profession" in intents:
        return WorkflowType.PLACE_PROFESSION

    # 7. General — weather, bodytype, or vague queries
    return WorkflowType.GENERAL


def classify_with_context(intents, query=""):
    """Classify intents and return primary workflow + secondary signals.

    Secondary signals capture cross-workflow context that the winning
    workflow would otherwise lose.
    """
    primary = classify(intents, query)
    secondary = {}

    if primary == WorkflowType.HEALTH:
        if "occasion" in intents or "event" in intents:
            secondary["formality_context"] = intents.get("occasion", intents.get("event", ""))
        if "place" in intents:
            secondary["place_context"] = intents["place"]

    if primary == WorkflowType.OCCASION and "health" in intents:
        secondary["health_context"] = intents["health"]

    if primary == WorkflowType.PLACE_PROFESSION and "activity" in intents:
        secondary["activity_context"] = intents["activity"]

    if primary == WorkflowType.ACTIVITY and ("occasion" in intents or "event" in intents):
        secondary["formality_context"] = intents.get("occasion", intents.get("event", ""))

    # Detect multi-product requests
    from knowledge_graph import PRODUCT_TYPE_ALIASES
    query_lower = query.lower()
    product_mentions = []
    for canonical, aliases in PRODUCT_TYPE_ALIASES.items():
        if canonical in query_lower or any(a in query_lower for a in aliases):
            product_mentions.append(canonical)
    if len(product_mentions) > 1:
        secondary["multi_product"] = product_mentions

    return primary, secondary


def get_workflow(workflow_type):
    """Import and return the workflow module for the given type."""
    from workflows import (
        vacation, place_profession, activity,
        occasion, health, general, gifting,
    )

    mapping = {
        WorkflowType.VACATION: vacation,
        WorkflowType.PLACE_PROFESSION: place_profession,
        WorkflowType.ACTIVITY: activity,
        WorkflowType.OCCASION: occasion,
        WorkflowType.HEALTH: health,
        WorkflowType.GENERAL: general,
        WorkflowType.GIFTING: gifting,
    }
    return mapping[workflow_type]
