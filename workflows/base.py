"""Shared execution path used by most SuperSearch workflows.

Domain workflows enrich intents, then this module performs KG lookup, SQL
retrieval, outfit assembly, accessory selection, and debug trace attachment.
It also owns singleton loading for the KG and complementary graph data.
"""

from knowledge_graph import KnowledgeGraph
from db_query import query_products
from complementary_graphs import ComplementaryGraphs
from outfit_builder import form_outfit, OutfitResult
from weather_inference import enrich_intents_with_weather
from config import KG_PATH


_kg = None
_comp = None


def get_kg():
    global _kg
    if _kg is None:
        _kg = KnowledgeGraph(KG_PATH)
    return _kg


def get_comp_graphs():
    global _comp
    if _comp is None:
        _comp = ComplementaryGraphs()
    return _comp


def resolve_gender(intents):
    """Return a DB/KG gender filter only when the user supplied one."""
    gender = intents.get("gender")
    return gender if gender in ("female", "male") else None


def run_standard_pipeline(query, intents, brand, gender=None):
    """The common pipeline: KG lookup → SQL → DB → outfit builder.

    Used by most workflow modules with minor variations.

    Returns:
        OutfitResult
    """
    kg = get_kg()
    comp = get_comp_graphs()

    gender = gender if gender in ("female", "male") else resolve_gender(intents)

    enrich_intents_with_weather(intents)

    kg_result = kg.lookup(intents, gender=gender)
    kg_context = kg.format_context(kg_result)

    db_result = query_products(query, intents, kg_context, brand)
    products = db_result.get("products", [])

    if not products:
        return OutfitResult(
            query=query,
            occasion=intents.get("occasion", ""),
            db_debug=db_result,
            kg_context=kg_result,
        )

    outfit = form_outfit(
        products, kg_result, intents,
        comp_graphs=comp, gender=gender,
    )
    outfit.query = query
    outfit.db_debug = db_result
    return outfit
