"""Top-level multi-turn orchestrator for SuperSearch chat requests.

This module connects intent extraction, session state, deterministic workflow
routing, product retrieval, and final response formatting. It is the closest
thing to the production assistant loop: every user message enters here before
being dispatched to one of the workflow modules.
"""

import json
import re
from session import Session
from intent_extractor import extract as extract_intents, get_last_extraction_trace
from router import classify_with_trace, get_workflow
from config import LLM_TIMEOUT
from llm_client import call_llm
from outfit_builder import OutfitResult


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
    "make it",
    # Accessories
    "matching shoes", "matching bag", "matching jewel", "accessories for",
    "matching jewellery", "matching jewelry", "jewellery works", "jewelry works",
    "shoes for", "bag for", "jewellery for", "jewelry for",
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
    r"^(can i|can you)\s+(wear|use|pair|style)",
    r"^(what|how)\s+about",
    r"^(what|which).+\b(with|for)\s+(this|that)\b",
    r"\bfor (this|that|the)\b",
    r"^(no|nah|nope),?\s+",
]

# Suggested follow-ups per workflow — returned alongside results as refinement actions.
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
        self._last_response_trace = {}

    def process(self, user_message):
        """Process a user message and return structured result."""
        prior_intents = dict(self.session.active_intents)
        prior_workflow = self.session.active_workflow
        prior_turn_count = len(self.session.turns)
        is_followup, followup_reason = self._followup_decision(user_message)

        if is_followup and self.session.active_intents:
            context = self.session.get_context_for_extraction()
            new_intents, elapsed = extract_intents(user_message, session_context=context)
            self.session.merge_intents(new_intents)
            intents = self.session.active_intents
            merge_mode = "merge"
        else:
            intents, elapsed = extract_intents(user_message)
            self.session.active_intents = intents
            new_intents = intents
            merge_mode = "reset"

        extraction_trace = get_last_extraction_trace()
        workflow_type, secondary, router_trace = classify_with_trace(intents, user_message)
        clarifying_questions = self._get_clarifying_questions(
            user_message, intents, workflow_type
        )
        trace = {
            "prior_turn_count": prior_turn_count,
            "prior_workflow": prior_workflow,
            "prior_intents": prior_intents,
            "is_followup": is_followup,
            "followup_reason": followup_reason,
            "merge_mode": merge_mode,
            "new_intents": dict(new_intents),
            "final_intents": dict(intents),
            "intent_diff": self._intent_diff(prior_intents, intents),
            "extraction": extraction_trace,
            "router": router_trace,
            "secondary": secondary,
            "clarification": {
                "needed": bool(clarifying_questions),
                "questions": clarifying_questions,
                "reasons": self._clarification_reasons(user_message, intents, workflow_type),
            },
        }
        if clarifying_questions:
            self.session.active_workflow = workflow_type.value
            self.session.add_turn(user_message, intents, workflow_type.value)
            response_text = " ".join(clarifying_questions)
            return {
                "workflow": workflow_type.value,
                "intents": intents,
                "outfit": OutfitResult(query=user_message, occasion=intents.get("occasion", "")),
                "response_text": response_text,
                "is_followup": is_followup,
                "session_id": self.session.session_id,
                "suggested_followups": [],
                "needs_clarification": True,
                "clarifying_questions": clarifying_questions,
                "trace": trace,
            }

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
        trace["response"] = self._last_response_trace

        return {
            "workflow": workflow_type.value,
            "intents": intents,
            "outfit": outfit,
            "response_text": response_text,
            "is_followup": is_followup,
            "session_id": self.session.session_id,
            "suggested_followups": followups,
            "needs_clarification": False,
            "clarifying_questions": [],
            "trace": trace,
        }

    def _is_followup(self, message):
        """Detect if this is a follow-up to a previous turn."""
        return self._followup_decision(message)[0]

    def _followup_decision(self, message):
        """Detect follow-ups and include the winning reason for eval traces."""
        if not self.session.turns:
            return False, "no_prior_turns"
        msg_lower = message.lower().strip()

        reset_phrases = (
            "forget that", "ignore that", "start over", "reset",
            "different question", "new question", "different event",
            "different occasion",
        )
        if any(phrase in msg_lower for phrase in reset_phrases):
            return False, "explicit_reset_phrase"

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
                return False, "full_query_with_event_and_product_anchor"

        # Short messages after initial turn are likely follow-ups
        has_reference = any(ref in msg_lower for ref in ("this", "that", "these", "those", "first", "second"))
        if len(msg_lower.split()) <= 4:
            return True, "short_message_after_prior_turn"
        if has_reference and any(
            term in msg_lower
            for term in ("wear", "work", "works", "jewel", "bag", "shoe", "accessor", "pair", "style", "temple")
        ):
            return True, "deictic_product_or_styling_reference"

        matched_signal = next((signal for signal in FOLLOWUP_SIGNALS if signal in msg_lower), None)
        if matched_signal:
            return True, f"followup_signal:{matched_signal.strip()}"

        matched_pattern = next((pattern for pattern in FOLLOWUP_PATTERNS if re.search(pattern, msg_lower)), None)
        if matched_pattern:
            return True, f"followup_pattern:{matched_pattern}"

        return False, "no_followup_signal"

    def _intent_diff(self, before, after):
        keys = sorted(set(before) | set(after))
        diff = {"added": {}, "removed": {}, "changed": {}}
        for key in keys:
            if key not in before and key in after:
                diff["added"][key] = after[key]
            elif key in before and key not in after:
                diff["removed"][key] = before[key]
            elif before.get(key) != after.get(key):
                diff["changed"][key] = {"from": before.get(key), "to": after.get(key)}
        return diff

    def _get_suggested_followups(self, intents, workflow_type):
        """Return up to 3 suggested follow-up actions based on missing intents.

        These are refinement actions shown alongside results, not blocking
        questions.
        """
        rules = SUGGESTED_FOLLOWUPS.get(workflow_type.value, [])
        suggestions = []
        for condition, text in rules:
            if condition(intents):
                suggestions.append(text)
            if len(suggestions) >= 3:
                break
        return suggestions

    def _get_clarifying_questions(self, query, intents, workflow_type):
        """Return blocking questions for constraints that should not be guessed."""
        questions = []
        query_lower = query.lower()

        accepted_general_wedding = re.search(r"\bgeneral\b.{0,40}\bwedding\b", query_lower) is not None
        generic_wedding = (
            not accepted_general_wedding
            and "religion" not in intents
            and (
                intents.get("_needs_religion")
                or intents.get("occasion") == "wedding"
                or (
                    "wedding" in query_lower
                    and not any(
                        marker in query_lower
                        for marker in (
                            "hindu", "muslim", "islam", "nikah", "christian",
                            "church", "sikh", "gurudwara", "anand karaj",
                        )
                    )
                )
            )
        )
        product_specific = bool(intents.get("product_type"))
        if workflow_type.value == "occasion" and generic_wedding and not product_specific:
            questions.append(
                "Which wedding context should I use: Hindu, Muslim, Christian, Sikh, or general wedding guest?"
            )

        if "gender" not in intents and self._gender_is_material(query, intents):
            questions.append(
                "Who is this for: women, men, or kids?"
            )

        return questions[:2]

    def _clarification_reasons(self, query, intents, workflow_type):
        """Return machine-readable reasons behind blocking questions."""
        reasons = []
        query_lower = query.lower()
        accepted_general_wedding = re.search(r"\bgeneral\b.{0,40}\bwedding\b", query_lower) is not None
        generic_wedding = (
            not accepted_general_wedding
            and "religion" not in intents
            and (
                intents.get("_needs_religion")
                or intents.get("occasion") == "wedding"
                or (
                    "wedding" in query_lower
                    and not any(
                        marker in query_lower
                        for marker in (
                            "hindu", "muslim", "islam", "nikah", "christian",
                            "church", "sikh", "gurudwara", "anand karaj",
                        )
                    )
                )
            )
        )
        product_specific = bool(intents.get("product_type"))
        if workflow_type.value == "occasion" and generic_wedding and not product_specific:
            reasons.append("generic_wedding_requires_context")
        if "gender" not in intents and self._gender_is_material(query, intents):
            reasons.append("gender_material_to_retrieval")
        return reasons[:2]

    def _gender_is_material(self, query, intents):
        """Decide whether retrieval would be too broad without a recipient/gender."""
        if intents.get("relation") and intents.get("gender"):
            return False
        if intents.get("product_type") in {
            "saree", "lehenga", "salwar", "sherwani", "swimsuit",
        }:
            return False
        if re.search(r"\b(women|woman|men|man|girl|boy|kids|child)\b", query.lower()):
            return False
        return True

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

        avoid_colours = self._avoid_colours_for_response(query, outfit, intents)
        palette = [
            c for c in outfit.color_palette.get('palette', [])
            if c.lower() not in avoid_colours
        ]
        guardrail = ""
        if avoid_colours:
            guardrail = (
                "\nAvoided colours: "
                + ", ".join(sorted(avoid_colours))
                + ". Do not recommend, praise, or suggest these as garment, accessory, or accent colours."
            )

        prompt = f"""Format this fashion recommendation as a friendly, helpful response.

User asked: "{query}"

Recommendations:
{json.dumps(products_summary, indent=2)}

Accessories: shoes={outfit.shoes}, bags={outfit.bags}, jewellery={outfit.jewellery}
Color palette: {json.dumps(palette)}
Styling notes: {outfit.styling_notes}
Formality: {outfit.formality}
{guardrail}

Write a conversational response (3-5 sentences) that presents the top picks naturally,
mentions why they fit the occasion, and suggests accessories. No markdown, keep it warm and helpful."""

        if suggested_followups:
            prompt += (
                f"\n\nAt the end, briefly suggest these refinement options the user "
                f"can explore: {suggested_followups}"
            )

        self._last_response_trace = {
            "prompt": prompt,
            "used_fallback": False,
            "error": None,
            "raw_response": None,
        }

        try:
            response = call_llm(prompt, timeout=LLM_TIMEOUT)
            self._last_response_trace["raw_response"] = response
            return response
        except Exception as exc:
            self._last_response_trace.update({
                "used_fallback": True,
                "error": str(exc),
            })

        titles = [p.get("title", "") for p in outfit.primary_products[:3]]
        if not titles:
            return (
                "I could not find an exact product match in this catalog for those constraints. "
                "Try relaxing the product type, budget, colour, or occasion constraints so I can show the closest available options."
            )
        return f"Here are my top picks: {', '.join(titles)}. {' '.join(outfit.styling_notes[:2])}"

    def _avoid_colours_for_response(self, query, outfit, intents):
        """Collect colors that the final formatter must not recommend."""
        avoid = {
            c.lower()
            for c in outfit.kg_context.get("colour", {}).get("avoid", [])
            if c and c.lower() != "all"
        }
        avoid.update(
            c.strip().lower()
            for c in str(intents.get("avoid_colour") or intents.get("avoid_color") or "").split(",")
            if c.strip()
        )
        query_lower = query.lower()
        wedding_context = "wedding" in query_lower or intents.get("occasion") in {
            "wedding", "hindu wedding", "muslim wedding", "christian wedding",
        }
        if wedding_context and "guest" in query_lower:
            avoid.update({"red", "white", "ivory"})
        if wedding_context and "mother of" in query_lower:
            avoid.add("red")
        return avoid
