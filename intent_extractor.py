"""User-query understanding for the SuperSearch recommendation pipeline.

The extractor converts free-form shopping language into structured intents
such as occasion, product type, budget, size/body signals, weather, and travel
duration. It combines an LLM parse with deterministic enrichment so downstream
KG lookup and SQL generation receive stable constraints.
"""

import json
import re
import time
from config import LLM_TIMEOUT
from taxonomy import (
    AGEGROUP_TERMS,
    GIFT_ACTION_PATTERNS,
    GIFT_TERMS,
    PRODUCT_TYPE_ALIASES,
    RELATION_ALIASES,
    RELATION_GENDERS,
    RELIGION_KEYWORDS,
    RELIGION_WEDDING_TERMS,
    USER_GENDER_TERMS,
)
from llm_client import call_llm
from prompts import INTENT_EXTRACTION_SYSTEM, INTENT_EXTRACTION_USER


def extract(query, session_context=None):
    """Extract structured intents from a natural language query.

    Args:
        query: user's raw query text
        session_context: optional dict with prior turn intents for context

    Returns:
        tuple of (intents_dict, elapsed_ms)
    """
    prompt = INTENT_EXTRACTION_USER.format(query=query)

    if session_context:
        prompt += f"\n\nPrevious context from conversation: {json.dumps(session_context)}"
        prompt += "\nMerge relevant prior context with new intents from this message."

    start = time.time()
    try:
        raw = call_llm(prompt, system_prompt=INTENT_EXTRACTION_SYSTEM, timeout=LLM_TIMEOUT)
    except Exception:
        elapsed = (time.time() - start) * 1000
        return _enrich_intents({}, query), elapsed
    elapsed = (time.time() - start) * 1000
    match = re.search(r'\{[^{}]*\}', raw)
    if match:
        try:
            intents = json.loads(match.group())
            intents = _enrich_intents(intents, query)
            return intents, elapsed
        except json.JSONDecodeError:
            pass

    return _enrich_intents({}, query), elapsed


FUNCTIONAL_KEYWORDS = {
    "breathable": "breathable", "dance-friendly": "dance-friendly", "dance friendly": "dance-friendly",
    "haldi-proof": "haldi-proof", "haldi proof": "haldi-proof", "stain-resistant": "stain-resistant",
    "stain resistant": "stain-resistant", "wrinkle-free": "wrinkle-free", "wrinkle free": "wrinkle-free",
    "sweat-proof": "sweat-proof", "sweat proof": "sweat-proof", "waterproof": "waterproof",
    "water proof": "waterproof", "lightweight": "lightweight", "light weight": "lightweight",
    "quick-dry": "quick-dry", "quick dry": "quick-dry", "stretchable": "stretchable",
    "iron-free": "iron-free", "iron free": "iron-free", "machine-washable": "machine-washable",
    "machine washable": "machine-washable", "travel-friendly": "travel-friendly",
    "travel friendly": "travel-friendly", "warm": "warm", "layerable": "layerable",
}

STYLE_KEYWORDS = {
    "slimming": "slimming", "flattering": "flattering", "elongating": "elongating",
    "modest": "modest", "trendy": "trendy", "minimalist": "minimalist",
    "bold": "bold", "classic": "classic", "statement": "statement-piece",
    "instagram-worthy": "instagram-worthy", "instagram worthy": "instagram-worthy",
}


EVENT_KEYWORDS = {
    "diwali": "Diwali",
    "durga puja": "Durga Puja",
    "ganesh chaturthi": "Ganesh Chaturthi",
    "lakshmi puja": "Lakshmi Puja",
    "puja": "Lakshmi Puja",
    "holi": "Holi",
    "onam": "Onam",
    "navratri": "Navratri",
    "garba": "Navratri",
    "christmas": "Christmas",
    "eid": "Eid",
    "pongal": "Pongal",
    "bihu": "Bihu",
    "lohri": "Lohri",
    "baisakhi": "Baisakhi",
}

OCCASION_KEYWORDS = {
    "sangeet": "sangeet",
    "mehendi": "mehendi",
    "haldi": "haldi",
    "engagement": "engagement",
    "roka": "roka",
    "wedding": "wedding",
    "reception": "wedding",
    "gala": "gala",
    "cocktail party": "gala",
    "cocktail": "gala",
    "bachelorette": "bachelorette",
    "farewell": "farewell party",
    "date": "date night",
    "brunch": "date night",
    "music festival": "festival",
    "festival": "festival",
    "birthday": "birthday party",
    "baby shower": "baby shower",
    "anniversary": "anniversary",
    "housewarming": "housewarming",
    "retirement": "retirement party",
    "nikah": "muslim wedding",
    "nikkah": "muslim wedding",
    "church wedding": "christian wedding",
    "interview": "interview",
    "corporate event": "corporate event",
    "office party": "office party",
    "graduation": "graduation",
    "picnic": "picnic",
    "prom": "prom",
    "concert": "concert",
    "funeral": "funeral",
}

PLACE_KEYWORDS = {
    "office": "office",
    "temple": "temple",
    "mosque": "mosque",
    "church": "church",
    "airport": "airport",
    "museum": "museum",
    "beach": "beach",
    "rooftop bar": "restaurant",
    "bar": "restaurant",
    "restaurant": "restaurant",
    "cafe": "cafe",
    "college": "office",
    "school": "office",
    "mountain": "mountains",
    "mountains": "mountains",
}

PROFESSION_KEYWORDS = {
    "accountant": "accountant",
    "actor": "actor",
    "artist": "artist",
    "athlete": "athlete",
    "chef": "chef",
    "designer": "designer",
    "developer": "developer",
    "doctor": "doctor",
    "driver": "driver",
    "engineer": "engineer",
    "entrepreneur": "entrepreneur",
    "homemaker": "homemaker",
    "lawyer": "lawyer",
    "manager": "manager",
    "model": "model",
    "musician": "musician",
    "nurse": "nurse",
    "photographer": "photographer",
    "pilot": "pilot",
    "student": "student",
    "teacher": "teacher",
    "writer": "writer",
}

ACTIVITY_KEYWORDS = {
    "long flight": "traveling",
    "flight": "traveling",
    "travel": "traveling",
    "yoga": "yoga",
    "hiking": "hiking",
    "hike": "hiking",
    "swim": "swimming",
    "cycling": "cycling",
    "cycle": "cycling",
    "dance": "dancing",
    "dancing": "dancing",
    "clubbing": "dancing",
    "swimming": "swimming",
}

BODYTYPE_KEYWORDS = {
    "plus size": "plus size",
    "plus-size": "plus size",
    "petite": "petite",
    "broad shoulders": "broad shoulders",
}

HEALTH_KEYWORDS = {
    "back pain": "back pain",
    "sensitive skin": "sensitive skin",
    "hypoallergenic": "sensitive skin",
    "knee pain": "knee pain",
    "flat feet": "flat feet",
    "poor circulation": "poor circulation",
    "allergies": "allergies",
    "sweat": "sweating",
    "sweating": "sweating",
}

COLOR_KEYWORDS = [
    "white", "cream", "off white", "ivory", "gold", "red", "pink", "blue",
    "green", "yellow", "orange", "black", "maroon", "purple", "silver",
]


def _has_phrase(text, phrase):
    pattern = r"(?<![a-z0-9])" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _has_gift_signal(query_lower):
    if any(_has_phrase(query_lower, keyword) for keyword in GIFT_TERMS):
        return True
    return any(re.search(pattern, query_lower) for pattern in GIFT_ACTION_PATTERNS)


def _infer_relation(query_lower):
    for keyword, value in RELATION_ALIASES.items():
        if _has_phrase(query_lower, keyword) or _has_phrase(query_lower, keyword + "'s"):
            return value
    return None


def _canonical_product_type(value):
    value_lower = str(value or "").lower().strip()
    if not value_lower:
        return None
    for canonical, aliases in PRODUCT_TYPE_ALIASES.items():
        terms = [canonical] + aliases
        if any(value_lower == term.lower() for term in terms):
            return canonical
    return value_lower


def _is_generic_wedding_query(query_lower):
    if not _has_phrase(query_lower, "wedding"):
        return False
    if _is_accepted_general_wedding(query_lower):
        return False
    return not any(_has_phrase(query_lower, term) for term in RELIGION_WEDDING_TERMS)


def _is_accepted_general_wedding(query_lower):
    return (
        _has_phrase(query_lower, "general wedding")
        or _has_phrase(query_lower, "general wedding guest")
        or re.search(r"\bgeneral\b.{0,40}\bwedding\b", query_lower) is not None
    )


def normalize_intents(query, intents=None, preserve_existing=False):
    """Return deterministic runtime/eval intent enrichment for a query."""
    return _enrich_intents(dict(intents or {}), query, preserve_existing=preserve_existing)


def _enrich_intents(intents, query, preserve_existing=False):
    """Add inferred fields the LLM might have missed."""
    query_lower = query.lower()

    if "product_type" in intents:
        canonical_product = _canonical_product_type(intents.get("product_type"))
        if canonical_product:
            intents["product_type"] = canonical_product

    # Deterministic enrichment only handles high-confidence canonicalization.
    if "event" not in intents:
        for keyword, value in EVENT_KEYWORDS.items():
            if _has_phrase(query_lower, keyword):
                intents["event"] = value
                break

    if "occasion" not in intents and "event" not in intents:
        for keyword, value in OCCASION_KEYWORDS.items():
            if _has_phrase(query_lower, keyword):
                intents["occasion"] = value
                break

    if "place" not in intents:
        for keyword, value in PLACE_KEYWORDS.items():
            if _has_phrase(query_lower, keyword):
                intents["place"] = value
                break

    if "profession" not in intents:
        for keyword, value in PROFESSION_KEYWORDS.items():
            if _has_phrase(query_lower, keyword):
                intents["profession"] = value
                break

    if "religion" not in intents:
        for keyword, value in RELIGION_KEYWORDS.items():
            if _has_phrase(query_lower, keyword):
                intents["religion"] = value
                break
    if "religion" in intents:
        intents["_needs_religion"] = False

    # "general wedding" is the user's explicit answer to use a generic context.
    # Do not let an LLM-provided _needs_religion flag survive that answer.
    if _is_accepted_general_wedding(query_lower):
        if not preserve_existing and intents.get("occasion") in (None, "hindu wedding"):
            intents["occasion"] = "wedding"
        if intents.get("occasion") == "wedding":
            intents["_needs_religion"] = False

    # Generic wedding should not silently become a specific religion/culture.
    # We ask a clarification question later unless the user provided a cue.
    if _is_generic_wedding_query(query_lower):
        if not preserve_existing and intents.get("occasion") in (None, "hindu wedding"):
            intents["occasion"] = "wedding"
        if "religion" not in intents and intents.get("occasion") == "wedding":
            intents["_needs_religion"] = True

    if "activity" not in intents:
        for keyword, value in ACTIVITY_KEYWORDS.items():
            if _has_phrase(query_lower, keyword):
                intents["activity"] = value
                break

    if "bodytype" not in intents:
        for keyword, value in BODYTYPE_KEYWORDS.items():
            if _has_phrase(query_lower, keyword):
                intents["bodytype"] = value
                break

    if "health" not in intents:
        for keyword, value in HEALTH_KEYWORDS.items():
            if _has_phrase(query_lower, keyword):
                intents["health"] = value
                break

    if "weather" not in intents:
        if _has_phrase(query_lower, "humid") or _has_phrase(query_lower, "humidity"):
            intents["weather"] = "humid"
        elif any(_has_phrase(query_lower, w) for w in ("hot weather", "indian summer", "summer", "sunny")):
            intents["weather"] = "summer"
        elif any(_has_phrase(query_lower, w) for w in ("cold", "winter", "warm outfit")):
            intents["weather"] = "winter"
        elif any(_has_phrase(query_lower, w) for w in ("rain", "rainy", "waterproof")):
            intents["weather"] = "rainy"

    if "budget" not in intents:
        if any(w in query_lower for w in ("cheap", "budget", "affordable", "economical")):
            intents["budget"] = "affordable"
        elif "luxury" in query_lower:
            intents["budget"] = "luxury"
        elif any(w in query_lower for w in ("premium", "designer")):
            intents["budget"] = "premium"

    if "month" not in intents:
        for month in ("january", "february", "march", "april", "may", "june",
                      "july", "august", "september", "october", "november", "december"):
            if month in query_lower:
                intents["month"] = month.title()
                break

    if "location" not in intents:
        for location in (
            "goa", "mumbai", "rajasthan", "sri lanka", "europe", "kerala",
            "dubai", "delhi", "bangalore", "hyderabad", "chennai", "kolkata",
        ):
            if location in query_lower:
                intents["location"] = location.title()
                break

    if "agegroup" not in intents:
        for agegroup, terms in AGEGROUP_TERMS.items():
            if any(_has_phrase(query_lower, term) for term in terms):
                intents["agegroup"] = agegroup
                break
        if "agegroup" not in intents and re.search(r'\b30\s*(?:year|yr)', query_lower):
            intents["agegroup"] = "adult"

    if "relation" not in intents:
        relation = _infer_relation(query_lower)
        if relation:
            intents["relation"] = relation

    if "colour" not in intents:
        colors = [c for c in COLOR_KEYWORDS if _has_phrase(query_lower, c)]
        if colors:
            intents["colour"] = ",".join(colors)

    # Infer gender from relation
    relation = intents.get("relation", "")
    if relation and "gender" not in intents:
        profile_gender = RELATION_GENDERS.get(relation)
        if profile_gender:
            intents["gender"] = profile_gender

    if "gender" not in intents:
        for gender, terms in USER_GENDER_TERMS.items():
            if any(_has_phrase(query_lower, term) for term in terms):
                intents["gender"] = gender
                break

    # Infer gender from product mentions in query
    if "gender" not in intents:
        female_products = ["saree", "lehenga", "anarkali", "salwar", "blouse", "dupatta",
                           "chaniya choli", "ghagra choli", "kurti", "churidar"]
        male_products = ["sherwani", "kurta pajama", "jodhpuri", "bandhgala"]
        if any(p in query_lower for p in female_products):
            intents["gender"] = "female"
        elif any(p in query_lower for p in male_products):
            intents["gender"] = "male"

    # Detect gifting intent
    if _has_gift_signal(query_lower):
        intents["_is_gift"] = True

    # Detect vacation/packing intent
    pack_signals = ["pack", "packing", "trip", "vacation", "travel for", "days in",
                    "week in", "wardrobe set", "capsule wardrobe"]
    if any(s in query_lower for s in pack_signals):
        intents["_is_vacation"] = True

    # Extract duration if mentioned
    duration_match = re.search(r'(\d+)[\s-]*(?:day|days|night|nights)', query_lower)
    if duration_match:
        intents["duration"] = int(duration_match.group(1))

    # Extract numeric budget constraints
    if "price_max" not in intents:
        price_max_match = re.search(
            r'(?:under|below|within|less than|upto|up to|budget)\s*(?:rs\.?|inr|₹|rupees?)?\s*(\d[\d,]*)',
            query_lower)
        if price_max_match:
            intents["price_max"] = int(price_max_match.group(1).replace(",", ""))

    if "price_min" not in intents:
        price_min_match = re.search(
            r'(?:above|over|more than|starting|starting from|upwards of)\s*(?:rs\.?|inr|₹|rupees?)?\s*(\d[\d,]*)',
            query_lower)
        if price_min_match:
            intents["price_min"] = int(price_min_match.group(1).replace(",", ""))

    # Detect product_type from query if LLM missed it
    # Skip if negation words precede the product mention ("not a saree", "no lehenga")
    negation_patterns = [r"\bnot\s+(?:a\s+)?{}\b", r"\bno\s+{}\b",
                         r"\banything\s+but\s+(?:a\s+)?{}\b",
                         r"\bavoid\s+{}\b", r"\bwithout\s+(?:a\s+)?{}\b",
                         r"\binstead\s+of\s+(?:a\s+)?{}\b",
                         r"\bexcept\s+{}\b", r"\bother\s+than\s+(?:a\s+)?{}\b"]
    avoided_types = []
    for canonical, aliases in PRODUCT_TYPE_ALIASES.items():
        terms = [canonical] + aliases
        matched_term = next((t for t in terms if _has_phrase(query_lower, t)), None)
        if not matched_term:
            continue
        # Check if a negation word precedes this product mention
        is_negated = any(
            re.search(pat.format(re.escape(matched_term)), query_lower)
            for pat in negation_patterns
        )
        if is_negated:
            avoided_types.append(canonical)
        elif "product_type" not in intents:
            intents["product_type"] = canonical
    if avoided_types and "avoid_product_type" not in intents:
        intents["avoid_product_type"] = ",".join(sorted(set(avoided_types)))

    # Detect functional needs
    if "functional_needs" not in intents:
        found = [v for k, v in FUNCTIONAL_KEYWORDS.items() if k in query_lower]
        if found:
            intents["functional_needs"] = ",".join(sorted(set(found)))

    # Detect style goals
    if "style_goals" not in intents:
        found = [v for k, v in STYLE_KEYWORDS.items() if k in query_lower]
        if found:
            intents["style_goals"] = ",".join(sorted(set(found)))

    return intents
