"""Gifting workflow for recipient-aware SuperSearch recommendations.

Gift queries preserve explicit recipient, product, budget, and occasion
constraints. The only relation-based inference here is gender for relations
whose gender is explicit in language, such as mom, dad, sister, or brother.
"""

from taxonomy import RELATION_GENDERS
from workflows.base import run_standard_pipeline, resolve_gender


def run(query, intents, brand, session=None, secondary=None):
    """Handle gifting queries without adding style/age assumptions."""
    relation = intents.get("relation")

    gender = resolve_gender(intents) or RELATION_GENDERS.get(relation)
    agegroup = intents.get("agegroup")

    # Merge recipient profile into intents
    gift_intents = {**intents}
    if gender:
        gift_intents["gender"] = gender
    if agegroup:
        gift_intents["agegroup"] = agegroup
    gift_intents.pop("_is_gift", None)

    outfit = run_standard_pipeline(query, gift_intents, brand, gender)

    recipient = relation or "recipient"
    profile_bits = [bit for bit in (gender, agegroup) if bit]
    profile_text = f" ({', '.join(profile_bits)})" if profile_bits else ""
    outfit.styling_notes.insert(0, f"Gift for: {recipient}{profile_text}")

    occasion = intents.get("occasion", "")
    if occasion:
        outfit.styling_notes.append(f"Occasion: {occasion}")

    return outfit
