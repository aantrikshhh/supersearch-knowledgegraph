"""Outfit composition layer for SuperSearch recommendations.

After `db_query.py` retrieves candidate garments, workflows call this module to
score/diversify primary products, attach complementary accessories, build color
palettes, and return a structured `OutfitResult` for response formatting.
"""

from dataclasses import dataclass, field
from complementary_graphs import ComplementaryGraphs
from color_coordinator import coordinate_colors
from config import get_formality
from taxonomy import PRODUCT_TYPE_ALIASES


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
    kg_context: dict = field(default_factory=dict)
    kg_trace: dict = field(default_factory=dict)
    outfit_debug: dict = field(default_factory=dict)


def _split_csv(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _product_matches_type(product, canonical_type):
    canonical = str(canonical_type or "").strip().lower()
    if not canonical:
        return False
    terms = [canonical] + [a.lower() for a in PRODUCT_TYPE_ALIASES.get(canonical, [])]
    product_type = str(product.get("product_type", "")).lower()
    title = str(product.get("title", "")).lower()
    return any(
        term == product_type
        or term in product_type
        or product_type in term
        or product_type.rstrip("s") == term.rstrip("s")
        or term in title
        for term in terms
    )


def _product_type_matches_value(product, canonical_type):
    canonical = str(canonical_type or "").strip().lower()
    if not canonical:
        return False
    terms = [canonical] + [a.lower() for a in PRODUCT_TYPE_ALIASES.get(canonical, [])]
    product_type = str(product.get("product_type", "")).lower()
    return any(
        term == product_type
        or term in product_type
        or product_type in term
        or term.rstrip("s") == product_type.rstrip("s")
        for term in terms
    )


def _product_types_overlap(left, right):
    left_terms = [str(left or "").lower()] + [
        a.lower() for a in PRODUCT_TYPE_ALIASES.get(str(left or "").lower(), [])
    ]
    right_terms = [str(right or "").lower()] + [
        a.lower() for a in PRODUCT_TYPE_ALIASES.get(str(right or "").lower(), [])
    ]
    return any(
        l == r
        or l in r
        or r in l
        or l.rstrip("s") == r.rstrip("s")
        for l in left_terms if l
        for r in right_terms if r
    )


def form_outfit(candidates, kg_context, intents, comp_graphs=None, gender=None, top_n=3):
    """Build a complete outfit from DB candidates + KG context + complementary graphs.

    Args:
        candidates: list of product dicts from DB query
        kg_context: dict from KnowledgeGraph.lookup()
        intents: extracted intents dict
        comp_graphs: ComplementaryGraphs instance
        gender: optional "male" or "female" filter for accessory graphs
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
    requested_types = _split_csv(intents.get("product_type"))
    recommended_types = kg_context.get("product", {}).get("recommended", [])
    acceptable_types = kg_context.get("product", {}).get("acceptable", [])
    avoid_types = kg_context.get("product", {}).get("avoid", [])
    kg_avoided_types = {
        t for t in avoid_types
        if not any(_product_types_overlap(t, requested) for requested in requested_types)
    }
    user_avoided_types = set(_split_csv(intents.get("avoid_product_type")))

    scored = []
    rejected = []
    for p in candidates:
        if any(_product_matches_type(p, t) for t in user_avoided_types):
            rejected.append({
                "id": p.get("id"),
                "title": p.get("title", ""),
                "product_type": p.get("product_type", ""),
                "reason": "user_avoid_product_type",
            })
            continue
        if any(_product_type_matches_value(p, t) for t in kg_avoided_types):
            rejected.append({
                "id": p.get("id"),
                "title": p.get("title", ""),
                "product_type": p.get("product_type", ""),
                "reason": "kg_avoid_product_type",
            })
            continue

        score = 0
        score_reasons = []
        if requested_types and any(_product_matches_type(p, t) for t in requested_types):
            score = 12
            score_reasons.append("requested_product_type")
        elif any(_product_matches_type(p, t) for t in recommended_types):
            score = 10
            score_reasons.append("kg_recommended_product")
        elif any(_product_matches_type(p, t) for t in acceptable_types):
            score = 5
            score_reasons.append("kg_acceptable_product")
        else:
            score = 3
            score_reasons.append("fallback_candidate")

        # Boost for color match
        rec_colors = set(kg_context.get("colour", {}).get("recommended", []))
        if rec_colors and rec_colors != {"all"}:
            p_colors = set((p.get("colors") or "").split(","))
            if p_colors & rec_colors:
                score += 3
                score_reasons.append("kg_colour_match")

        # Boost for material match
        rec_materials = set(kg_context.get("material", {}).get("recommended", []))
        p_materials = set((p.get("materials") or "").split(","))
        if p_materials & rec_materials:
            score += 2
            score_reasons.append("kg_material_match")

        scored.append((p, score, score_reasons))

    scored.sort(key=lambda x: -x[1])

    # Diversify — don't pick 3 of the same type
    primary = []
    types_used = set()
    for p, s, score_reasons in scored:
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
    avoid_colors = {
        c.lower()
        for c in kg_context.get("colour", {}).get("avoid", [])
        if c and c.lower() != "all"
    }
    if avoid_colors:
        color_result["palette"] = [
            c for c in color_result.get("palette", [])
            if c.lower() not in avoid_colors
        ]
        color_result["accessory_colors"] = [
            c for c in color_result.get("accessory_colors", [])
            if c.lower() not in avoid_colors
        ]

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
        kg_context=kg_context,
        outfit_debug={
            "candidate_count": len(candidates),
            "requested_types": requested_types,
            "recommended_types": recommended_types,
            "acceptable_types": acceptable_types,
            "kg_avoided_types": sorted(kg_avoided_types),
            "user_avoided_types": sorted(user_avoided_types),
            "scored_candidates": [
                {
                    "rank": idx + 1,
                    "id": p.get("id"),
                    "title": p.get("title", ""),
                    "product_type": p.get("product_type", ""),
                    "colors": p.get("colors", ""),
                    "materials": p.get("materials", ""),
                    "price": p.get("price", ""),
                    "score": score,
                    "score_reasons": reasons,
                }
                for idx, (p, score, reasons) in enumerate(scored[:20])
            ],
            "rejected_candidates": rejected[:20],
            "selected_product_ids": [p.get("id") for p in primary],
        },
    )


def form_multiple_outfits(candidates, kg_context, intents, comp_graphs=None,
                          gender=None, count=3):
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
