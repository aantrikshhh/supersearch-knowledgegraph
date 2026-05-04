"""Occasion workflow for event, ceremony, and festival outfit searches.

This is the main path for weddings, sangeet, Diwali, Onam, Navratri, gala,
engagement, and similar queries. It combines KG cultural rules, formality, and
optional explicit product types before building one or more outfits.
"""

from workflows.base import run_standard_pipeline, get_kg, get_comp_graphs, resolve_gender
from outfit_builder import form_multiple_outfits
from db_query import query_products
from config import get_formality


def run(query, intents, brand, session=None, secondary=None):
    """Handle occasion/event queries like weddings, festivals, parties.

    Special handling:
    - Religion-aware color/product constraints
    - Formality-level appropriate products
    - Multiple outfit options for major occasions
    """
    kg = get_kg()
    comp = get_comp_graphs()

    gender = resolve_gender(intents)
    occasion = intents.get("occasion", intents.get("event", ""))
    formality, formality_config = get_formality(
        occasion=intents.get("occasion"),
        event=intents.get("event"),
    )

    # KG lookup with religion awareness
    kg_result = kg.lookup(intents, gender=gender)
    kg_context = kg.format_context(kg_result)

    # DB query
    db_result = query_products(query, intents, kg_context, brand)
    products = db_result.get("products", [])

    if not products:
        return run_standard_pipeline(query, intents, brand, gender)

    # For major occasions (weddings, sangeet, engagement), show multiple options
    major_occasions = {
        "hindu wedding", "muslim wedding", "christian wedding",
        "sangeet", "engagement", "roka", "mehendi", "haldi",
        "gala", "prom",
    }

    if occasion in major_occasions:
        outfits = form_multiple_outfits(
            products, kg_result, intents,
            comp_graphs=comp, gender=gender, count=3,
        )
        if outfits:
            primary = outfits[0]
            primary.styling_notes.insert(0, f"Formality: {formality} occasion")
            if intents.get("religion"):
                primary.styling_notes.append(
                    f"Cultural context: {intents['religion']} ceremony"
                )
            primary.query = query
            primary._alternatives = outfits[1:]
            primary.db_debug = db_result
            return primary

    # Standard single-outfit for less formal occasions
    from outfit_builder import form_outfit
    outfit = form_outfit(
        products, kg_result, intents,
        comp_graphs=comp, gender=gender,
    )
    outfit.query = query
    outfit.db_debug = db_result
    outfit.styling_notes.insert(0, f"Formality: {formality}")
    return outfit
