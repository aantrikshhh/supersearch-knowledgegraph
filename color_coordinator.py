"""Color coordination across outfit items using complementary color graph."""

from complementary_graphs import ComplementaryGraphs


def coordinate_colors(primary_colors, context="all", comp_graphs=None):
    """Given primary garment colors and a context, suggest a coordinated palette.

    Args:
        primary_colors: list of colors from the primary garment (e.g., ["red", "gold"])
        context: "casual", "ethnic", "formal", "party", "western", "all"
        comp_graphs: ComplementaryGraphs instance (loaded once, reused)

    Returns:
        dict with {
            primary: list of primary colors,
            complementary: list of complementary colors with harmony info,
            palette: unified color palette recommendation,
            accessory_colors: suggested colors for shoes/bag/jewellery,
        }
    """
    if comp_graphs is None:
        comp_graphs = ComplementaryGraphs()

    all_complements = []
    seen = set()

    for color in primary_colors:
        pairs = comp_graphs.get_complementary_colors(color, context=context, top_n=5)
        for p in pairs:
            if p["color"] not in seen and p["color"] not in primary_colors:
                all_complements.append(p)
                seen.add(p["color"])

    all_complements.sort(key=lambda x: x["rank"])

    # Build accessory color suggestions — metallics and neutrals rank highest for accessories
    metallic = [c for c in all_complements if c["harmony"] == "metallic"]
    neutral = [c for c in all_complements if c["harmony"] == "neutral"]
    accent = [c for c in all_complements if c["harmony"] not in ("metallic", "neutral")]

    accessory_colors = []
    if metallic:
        accessory_colors.append(metallic[0]["color"])
    if neutral:
        accessory_colors.append(neutral[0]["color"])
    if accent and len(accessory_colors) < 3:
        accessory_colors.append(accent[0]["color"])
    if not accessory_colors:
        accessory_colors = [c["color"] for c in all_complements[:2]]

    palette = primary_colors + [c["color"] for c in all_complements[:3]]

    return {
        "primary": primary_colors,
        "complementary": all_complements[:5],
        "palette": palette,
        "accessory_colors": accessory_colors,
    }


def check_color_variety(outfits):
    """Check color variety across multiple outfits (for pack_a_bag).

    Args:
        outfits: list of dicts with "colors" key

    Returns:
        dict with variety_score (0-1) and suggestions
    """
    all_primary = []
    for outfit in outfits:
        colors = outfit.get("colors", [])
        if colors:
            all_primary.append(colors[0] if isinstance(colors, list) else colors)

    unique = len(set(all_primary))
    total = len(all_primary) if all_primary else 1
    variety = unique / total

    suggestions = []
    if variety < 0.6:
        from collections import Counter
        repeated = Counter(all_primary).most_common(1)
        if repeated:
            suggestions.append(f"Too much {repeated[0][0]} — swap one outfit to a different color family")

    return {"variety_score": round(variety, 2), "suggestions": suggestions}
