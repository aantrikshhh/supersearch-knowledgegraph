"""Product matching: scores catalog products against knowledge graph context."""

from knowledge_graph import PRODUCT_TYPE_ALIASES


def _product_type_matches(product_type, kg_product_name):
    """Check if a product type matches a KG product name, including aliases."""
    if product_type == kg_product_name:
        return True
    aliases = PRODUCT_TYPE_ALIASES.get(kg_product_name, [])
    return product_type in aliases


def score_product(product, kg_context):
    """Score a single product against knowledge graph context.

    Returns a numeric score. Higher = better match.
    """
    score = 0.0

    products_data = kg_context.get("product", {})
    recommended = products_data.get("recommended", [])
    acceptable = products_data.get("acceptable", [])
    avoid = products_data.get("avoid", [])

    type_matched = False
    for rp in recommended:
        if _product_type_matches(product.product_type, rp):
            score += 10
            type_matched = True
            break

    if not type_matched:
        for ap in acceptable:
            if _product_type_matches(product.product_type, ap):
                score += 5
                type_matched = True
                break

    if not type_matched:
        for av in avoid:
            if _product_type_matches(product.product_type, av):
                score -= 20
                break

    for tag in ["colour", "pattern", "material"]:
        data = kg_context.get(tag, {})
        rec = data.get("recommended", [])
        avd = data.get("avoid", [])

        if tag == "colour":
            product_values = product.colors
        elif tag == "pattern":
            product_values = product.patterns
        elif tag == "material":
            product_values = product.materials
        else:
            product_values = []

        for val in product_values:
            if val in rec or "all" in rec:
                score += 3
            if val in avd:
                score -= 5

    return score


def match_products(products, kg_context, gender=None, top_n=30):
    """Match and rank products against knowledge graph context.

    Args:
        products: list of NormalizedProduct
        kg_context: dict from KnowledgeGraph.lookup()
        gender: optional gender filter
        top_n: number of top products to return

    Returns:
        list of (product, score) tuples, sorted by score descending
    """
    avoid_types = kg_context.get("product", {}).get("avoid", [])

    scored = []
    for p in products:
        if gender and p.gender not in (gender, "both"):
            continue

        is_avoided = False
        for av in avoid_types:
            if _product_type_matches(p.product_type, av):
                is_avoided = True
                break
        if is_avoided:
            continue

        s = score_product(p, kg_context)
        if s > 0:
            scored.append((p, s))

    scored.sort(key=lambda x: -x[1])
    return scored[:top_n]
