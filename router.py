"""Deterministic workflow router for extracted SuperSearch intents.

The router chooses which domain workflow owns a request, such as vacation,
occasion, health, place/profession, activity, gifting, or general. It also
captures secondary signals so compound queries do not silently drop context.
"""

from enum import Enum
import re

from taxonomy import GIFT_ACTION_PATTERNS, GIFT_TERMS, PRODUCT_TYPE_ALIASES


class WorkflowType(Enum):
    VACATION = "vacation"
    PLACE_PROFESSION = "place_profession"
    ACTIVITY = "activity"
    OCCASION = "occasion"
    HEALTH = "health"
    GENERAL = "general"
    GIFTING = "gifting"


def _has_gift_signal(query_lower):
    return (
        any(re.search(rf"\b{re.escape(term)}\b", query_lower) for term in GIFT_TERMS)
        or any(re.search(pattern, query_lower) for pattern in GIFT_ACTION_PATTERNS)
    )


def classify(intents, query=""):
    """Classify intents into a workflow type using deterministic rules.

    Priority order matters — first match wins.
    """
    return classify_reason(intents, query)[0]


def classify_reason(intents, query=""):
    """Classify intents and include the deterministic rule that won."""
    query_lower = query.lower()

    # 1. Vacation — explicit packing/trip signals
    if intents.get("_is_vacation"):
        return WorkflowType.VACATION, "_is_vacation"
    if intents.get("duration"):
        return WorkflowType.VACATION, "duration"
    if intents.get("activity") == "vacation":
        return WorkflowType.VACATION, "activity_vacation"

    # 2. Gifting — buying a present for someone else.
    # Query text is checked too because golden/eval callers may pass raw intents
    # that were not normalized through intent_extractor.
    if intents.get("_is_gift") or _has_gift_signal(query_lower):
        return WorkflowType.GIFTING, "_is_gift_or_query_gift_signal"

    # 3. Occasion/Event — the most common flow
    if "occasion" in intents or "event" in intents:
        return WorkflowType.OCCASION, "occasion_or_event"

    # 4. Activity — specific activity (not vacation)
    if intents.get("activity") == "traveling" and ("place" in intents or "profession" in intents):
        return WorkflowType.PLACE_PROFESSION, "traveling_with_place_or_profession"
    if "activity" in intents:
        return WorkflowType.ACTIVITY, "activity"

    # 5. Health — comfort/health is primary concern
    if "health" in intents and "place" not in intents and "occasion" not in intents:
        return WorkflowType.HEALTH, "health_without_stronger_context"

    # 6. Place/Profession — going somewhere or dressing for a role
    if "place" in intents or "profession" in intents:
        return WorkflowType.PLACE_PROFESSION, "place_or_profession"

    # 7. General — weather, bodytype, or vague queries
    return WorkflowType.GENERAL, "fallback_general"


def classify_with_context(intents, query=""):
    """Classify intents and return primary workflow + secondary signals.

    Secondary signals capture cross-workflow context that the winning
    workflow would otherwise lose.
    """
    primary, reason = classify_reason(intents, query)
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
    query_lower = query.lower()
    product_mentions = []
    for canonical, aliases in PRODUCT_TYPE_ALIASES.items():
        if canonical in query_lower or any(a in query_lower for a in aliases):
            product_mentions.append(canonical)
    if len(product_mentions) > 1:
        secondary["multi_product"] = product_mentions

    return primary, secondary


def classify_with_trace(intents, query=""):
    """Classify and return router provenance for trace artifacts."""
    primary, reason = classify_reason(intents, query)
    primary_with_context, secondary = classify_with_context(intents, query)
    return primary_with_context, secondary, {
        "workflow": primary_with_context.value,
        "reason": reason,
        "secondary": secondary,
        "intent_keys": sorted(intents.keys()),
        "gift_signal": _has_gift_signal(query.lower()),
    }


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
