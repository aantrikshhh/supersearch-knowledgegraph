"""Gifting workflow for recipient-aware SuperSearch recommendations.

Gift queries add relation, likely gender/age, and occasion constraints before
retrieval, so recommendations feel appropriate for the recipient rather than
only matching the buyer's surface search terms.
"""

from workflows.base import run_standard_pipeline, get_kg, get_comp_graphs
from outfit_builder import form_outfit, OutfitResult
from db_query import query_products


RELATION_PROFILES = {
    "mom": {"gender": "female", "agegroup": "middle-aged", "style": "elegant"},
    "sister": {"gender": "female", "agegroup": "young adult", "style": "trendy"},
    "wife": {"gender": "female", "agegroup": "adult", "style": "elegant"},
    "daughter": {"gender": "female", "agegroup": "teenager", "style": "trendy"},
    "niece": {"gender": "female", "agegroup": "teenager", "style": "trendy"},
    "aunt": {"gender": "female", "agegroup": "middle-aged", "style": "classic"},
    "grandmother": {"gender": "female", "agegroup": "senior", "style": "classic"},
    "friend": {"gender": "female", "agegroup": "young adult", "style": "trendy"},
    "dad": {"gender": "male", "agegroup": "middle-aged", "style": "classic"},
    "brother": {"gender": "male", "agegroup": "young adult", "style": "casual"},
    "husband": {"gender": "male", "agegroup": "adult", "style": "classic"},
    "nephew": {"gender": "male", "agegroup": "teenager", "style": "casual"},
    "uncle": {"gender": "male", "agegroup": "middle-aged", "style": "classic"},
    "grandfather": {"gender": "male", "agegroup": "senior", "style": "classic"},
    "colleague": {"gender": "female", "agegroup": "adult", "style": "professional"},
    "boss": {"gender": "female", "agegroup": "middle-aged", "style": "professional"},
}


def run(query, intents, brand, session=None, secondary=None):
    """Handle gifting queries — infer recipient profile and find appropriate gifts.

    Steps:
    1. Infer gender + agegroup from relation
    2. Merge inferred attributes into intents
    3. Run standard pipeline with recipient's profile
    4. Add gifting-specific styling notes
    """
    relation = intents.get("relation", "friend")
    profile = RELATION_PROFILES.get(relation, RELATION_PROFILES["friend"])

    gender = intents.get("gender", profile["gender"])
    agegroup = intents.get("agegroup", profile["agegroup"])

    # Merge recipient profile into intents
    gift_intents = {**intents}
    gift_intents["gender"] = gender
    gift_intents["agegroup"] = agegroup
    gift_intents.pop("_is_gift", None)

    outfit = run_standard_pipeline(query, gift_intents, brand, gender)

    outfit.styling_notes.insert(0, f"Gift for: {relation} ({gender}, {agegroup})")
    outfit.styling_notes.append(f"Style preference: {profile['style']}")

    occasion = intents.get("occasion", "")
    if occasion:
        outfit.styling_notes.append(f"Occasion: {occasion}")

    return outfit
