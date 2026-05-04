"""Outfit composition — combines primary products with accessories using complementary graphs."""

from dataclasses import dataclass, field
from complementary_graphs import ComplementaryGraphs
from color_coordinator import coordinate_colors
from config import get_formality


@dataclass
class OutfitResult:
    primary_products: list = field(default_factory=list)
    shoes: list = field(default_factory=list)
    bags: list = field(default_factory=list)
    jewellery: list = field(default_factory=list)
    color_palette: dict = field(default_factory=dict)
    styling_notes: list = field(default_factory=list)
    formality: str = ""
    occasion: str = ""
    query: str = ""
    db_debug: dict = field(default_factory=dict)


def form_outfit(candidates, kg_context, intents, comp_graphs=None, gender="female", top_n=3):
    """Build a complete outfit from DB candidates + KG context + complementary graphs.

    Args:
        candidates: list of product dicts from DB query
        kg_context: dict from KnowledgeGraph.lookup()
        intents: extracted intents dict
        comp_graphs: ComplementaryGraphs instance
        gender: "male" or "female"
        top_n: number of primary product options to include

    Returns:
        OutfitResult with primary products, accessories, colors, and styling notes
    """
    if comp_graphs is None:
        comp_graphs = ComplementaryGraphs()

    occasion = intents.get("occasion", intents.get("event", ""))
    formality_level, formality_config = get_formality(
        occasion=intents.get("occasion"),
        event=intents.get("event"),
    )

    # Pick primary products — prefer recommended types, then diversify
    recommended_types = set(kg_context.get("product", {}).get("recommended", []))
    acceptable_types = set(kg_context.get("product", {}).get("acceptable", []))
    avoid_types = set(kg_context.get("product", {}).get("avoid", []))

    scored = []
    for p in candidates:
        ptype = p.get("product_type", "")
        score = 0
        if ptype in recommended_types:
            score = 10
        elif ptype in acceptable_types:
            score = 5
        elif ptype in avoid_types:
            continue
        else:
            score = 3

        # Boost for color match
        rec_colors = set(kg_context.get("colour", {}).get("recommended", []))
        if rec_colors and rec_colors != {"all"}:
            p_colors = set((p.get("colors") or "").split(","))
            if p_colors & rec_colors:
                score += 3

        # Boost for material match
        rec_materials = set(kg_context.get("material", {}).get("recommended", []))
        p_materials = set((p.get("materials") or "").split(","))
        if p_materials & rec_materials:
            score += 2

        scored.append((p, score))

    scored.sort(key=lambda x: -x[1])

    # Diversify — don't pick 3 of the same type
    primary = []
    types_used = set()
    for p, s in scored:
        ptype = p.get("product_type", "")
        if len(primary) >= top_n:
            break
        if ptype in types_used and len(primary) > 0:
            if len([x for x in scored if x[0].get("product_type") != ptype and x[1] > 0]) > 0:
                continue
        primary.append(p)
        types_used.add(ptype)

    if not primary and scored:
        primary = [scored[0][0]]

    # Get accessories from complementary product graph
    occasion_tag = occasion or formality_level
    accessories = comp_graphs.get_outfit_accessories(occasion_tag, gender)

    # Coordinate colors
    primary_colors = []
    for p in primary[:1]:
        colors = (p.get("colors") or "").split(",")
        primary_colors.extend(c.strip() for c in colors if c.strip())

    context_map = {
        "formal": "ethnic", "semi_formal": "ethnic",
        "casual": "casual", "festive": "ethnic", "mourning": "ethnic",
    }
    color_context = context_map.get(formality_level, "all")
    color_result = coordinate_colors(primary_colors[:3], context=color_context, comp_graphs=comp_graphs)

    # Build styling notes
    notes = []
    bodytype = intents.get("bodytype", "")
    if bodytype:
        fit_recs = kg_context.get("fit", {}).get("recommended", [])
        if fit_recs:
            notes.append(f"For {bodytype} body type: look for {', '.join(fit_recs)} silhouettes")

    health = intents.get("health", "")
    if health:
        material_recs = kg_context.get("material", {}).get("recommended", [])
        if material_recs:
            notes.append(f"For {health}: prioritize {', '.join(material_recs)} fabrics")

    weather = intents.get("weather", "")
    if weather:
        notes.append(f"Weather ({weather}): dress accordingly")

    return OutfitResult(
        primary_products=primary,
        shoes=[s["name"] for s in accessories.get("shoes", [])[:2]],
        bags=[b["name"] for b in accessories.get("bag", [])[:2]],
        jewellery=[j["name"] for j in accessories.get("jewellery", [])[:3]],
        color_palette=color_result,
        styling_notes=notes,
        formality=formality_level,
        occasion=occasion,
        query="",
    )


def form_multiple_outfits(candidates, kg_context, intents, comp_graphs=None,
                          gender="female", count=3):
    """Build multiple distinct outfit options.

    Splits candidates into groups by product type to ensure variety.
    """
    if comp_graphs is None:
        comp_graphs = ComplementaryGraphs()

    # Group candidates by product type
    from collections import defaultdict
    by_type = defaultdict(list)
    for p in candidates:
        by_type[p.get("product_type", "unknown")].append(p)

    recommended_types = kg_context.get("product", {}).get("recommended", [])
    type_order = recommended_types + [t for t in by_type if t not in recommended_types]

    outfits = []
    used_ids = set()

    for i in range(count):
        # Rotate through product types for variety
        available = []
        for ptype in type_order:
            for p in by_type.get(ptype, []):
                if p["id"] not in used_ids:
                    available.append(p)

        if not available:
            break

        outfit = form_outfit(
            available[:10], kg_context, intents,
            comp_graphs=comp_graphs, gender=gender, top_n=1,
        )

        if outfit.primary_products:
            for p in outfit.primary_products:
                used_ids.add(p["id"])
            outfits.append(outfit)

    return outfits
