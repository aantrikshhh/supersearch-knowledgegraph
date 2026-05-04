"""Top-level multi-turn orchestrator for SuperSearch chat requests.

This module connects intent extraction, session state, deterministic workflow
routing, product retrieval, and final response formatting. It is the closest
thing to the production assistant loop: every user message enters here before
being dispatched to one of the workflow modules.
"""

import json
import re
from session import Session
from intent_extractor import extract as extract_intents
from router import classify, classify_with_context, get_workflow
from config import LLM_TIMEOUT
from llm_client import call_llm


FOLLOWUP_SIGNALS = [
    # Refinement
    "instead", "but ", "cheaper", "expensive", "different", "another",
    "show more", "more options", "more like", "similar to",
    # Color changes
    "in blue", "in red", "in pink", "in black", "in green", "in white",
    "in gold", "in yellow", "in maroon", "in purple", "in beige",
    # Attribute filters
    "plus-size", "plus size", "petite", "under ", "below ", "above ",
    # Comparison / exploration
    "what about", "how about", "also ", "and also", "compare",
    "which has", "which one", "which is", "which works",
    # Negation / replacement
    "something else", "not this", "change", "swap", "replace", "remove",
    # Accessories
    "matching shoes", "matching bag", "matching jewel", "accessories for",
    "shoes for", "bag for", "jewellery for",
    # Vague follow-ups
    "yes", "ok show", "okay", "sure", "go ahead",
    "that one", "this one", "the first", "the second",
    # Fabric / quality / style
    "fabric", "quality", "material", "silk", "cotton", "georgette",
    "twirl", "flowy", "fitted", "comfortable",
    # Size
    "size", "sizes", "xl", "xxl",
]

FOLLOWUP_PATTERNS = [
    r"^(show|give|get)\s+(me\s+)?(more|other|different|similar)",
    r"^(only|just)\s+",
    r"^(do you have|is there|are there)",
    r"^(can i|can you)\s+(see|get|have)",
    r"^(what|how)\s+about",
    r"\bfor (this|that|the)\b",
    r"^(no|nah|nope),?\s+",
]

# Suggested follow-ups per workflow — returned alongside results (like Rufus buttons).
# Each rule checks what intents are MISSING and suggests refinement actions.
SUGGESTED_FOLLOWUPS = {
    "occasion": [
        (lambda i: "wedding" in i.get("occasion", "") and "religion" not in i,
         "Show options for a specific ceremony (sangeet, mehendi, haldi, reception)"),
        (lambda i: "budget" not in i and "price_max" not in i,
         "Filter by budget range"),
        (lambda i: "bodytype" not in i,
         "Show options for your body type"),
        (lambda i: "product_type" not in i,
         "Show only sarees / lehengas / kurta sets"),
    ],
    "vacation": [
        (lambda i: "location" not in i,
         "Specify your destination"),
        (lambda i: "month" not in i,
         "Which month are you traveling?"),
        (lambda i: "activity" not in i,
         "Any specific activities planned?"),
    ],
    "place_profession": [
        (lambda i: "weather" not in i and "month" not in i,
         "What's the weather like?"),
        (lambda i: "budget" not in i and "price_max" not in i,
         "Filter by budget"),
    ],
    "activity": [
        (lambda i: "health" not in i,
         "Any comfort or health considerations?"),
        (lambda i: "weather" not in i,
         "What's the weather like?"),
    ],
    "health": [
        (lambda i: "occasion" not in i and "place" not in i,
         "What's the occasion or setting?"),
    ],
    "gifting": [
        (lambda i: "occasion" not in i and "event" not in i,
         "Is this for a specific occasion?"),
        (lambda i: "budget" not in i and "price_max" not in i,
         "What's your budget range?"),
    ],
    "general": [
        (lambda i: "occasion" not in i and "place" not in i and "activity" not in i,
         "What's the occasion or setting?"),
        (lambda i: "budget" not in i,
         "Filter by budget range"),
    ],
}


class ConversationManager:
    def __init__(self, session=None, brand="aza"):
        self.session = session or Session(brand=brand)

    def process(self, user_message):
        """Process a user message and return structured result."""
        is_followup = self._is_followup(user_message)

        if is_followup and self.session.active_intents:
            context = self.session.get_context_for_extraction()
            new_intents, elapsed = extract_intents(user_message, session_context=context)
            self.session.merge_intents(new_intents)
            intents = self.session.active_intents
        else:
            intents, elapsed = extract_intents(user_message)
            self.session.active_intents = intents

        workflow_type, secondary = classify_with_context(intents, user_message)
        workflow = get_workflow(workflow_type)
        outfit = workflow.run(
            user_message, intents, self.session.brand,
            session=self.session, secondary=secondary,
        )

        self.session.active_workflow = workflow_type.value
        self.session.add_turn(user_message, intents, workflow_type.value)

        followups = self._get_suggested_followups(intents, workflow_type)
        response_text = self._format_response(
            outfit, user_message, intents, workflow_type, followups,
        )

        return {
            "workflow": workflow_type.value,
            "intents": intents,
            "outfit": outfit,
            "response_text": response_text,
            "is_followup": is_followup,
            "session_id": self.session.session_id,
            "suggested_followups": followups,
        }

    def _is_followup(self, message):
        """Detect if this is a follow-up to a previous turn."""
        if not self.session.turns:
            return False
        msg_lower = message.lower().strip()

        # Full queries after a prior turn should reset context, not merge into it.
        # Example: "Kurta set under 2000 for Diwali women" is a new query even
        # though it contains the follow-up signal "under ".
        has_deictic = any(d in msg_lower for d in (" this", " that", " these", " those", " first", " second"))
        if len(msg_lower.split()) > 4 and not has_deictic:
            query_anchors = [
                "wedding", "sangeet", "mehendi", "haldi", "diwali", "onam",
                "navratri", "holi", "office", "temple", "airport", "vacation",
                "trip", "hiking", "cycling", "brunch", "gala", "party",
            ]
            garment_anchors = [
                "saree", "lehenga", "kurta", "dress", "salwar", "chaniya",
                "jacket", "swimwear", "tracksuit", "gown",
            ]
            if any(a in msg_lower for a in query_anchors) and any(g in msg_lower for g in garment_anchors):
                return False

        # Short messages after initial turn are likely follow-ups
        if len(msg_lower.split()) <= 4:
            return True

        if any(signal in msg_lower for signal in FOLLOWUP_SIGNALS):
            return True

        if any(re.search(pattern, msg_lower) for pattern in FOLLOWUP_PATTERNS):
            return True

        return False

    def _get_suggested_followups(self, intents, workflow_type):
        """Return up to 3 suggested follow-up actions based on missing intents.

        Modeled after Rufus — these are refinement buttons shown alongside results,
        not blocking questions.
        """
        rules = SUGGESTED_FOLLOWUPS.get(workflow_type.value, [])
        suggestions = []
        for condition, text in rules:
            if condition(intents):
                suggestions.append(text)
            if len(suggestions) >= 3:
                break
        return suggestions

    def _format_response(self, outfit, query, intents, workflow_type,
                         suggested_followups=None):
        """Use LLM to format the OutfitResult into a natural response."""
        products_summary = []
        for p in outfit.primary_products[:5]:
            products_summary.append({
                "title": p.get("title", ""),
                "type": p.get("product_type", ""),
                "colors": p.get("colors", ""),
                "price": p.get("price", 0),
            })

        prompt = f"""Format this fashion recommendation as a friendly, helpful response.

User asked: "{query}"

Recommendations:
{json.dumps(products_summary, indent=2)}

Accessories: shoes={outfit.shoes}, bags={outfit.bags}, jewellery={outfit.jewellery}
Color palette: {json.dumps(outfit.color_palette.get('palette', []))}
Styling notes: {outfit.styling_notes}
Formality: {outfit.formality}

Write a conversational response (3-5 sentences) that presents the top picks naturally,
mentions why they fit the occasion, and suggests accessories. No markdown, keep it warm and helpful."""

        if suggested_followups:
            prompt += (
                f"\n\nAt the end, briefly suggest these refinement options the user "
                f"can explore: {suggested_followups}"
            )

        try:
            return call_llm(prompt, timeout=LLM_TIMEOUT)
        except Exception:
            pass

        titles = [p.get("title", "") for p in outfit.primary_products[:3]]
        return f"Here are my top picks: {', '.join(titles)}. {' '.join(outfit.styling_notes[:2])}"
