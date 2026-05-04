"""Brand-specific catalog normalization for SuperSearch product retrieval.

Each brand export has different JSON shape, product fields, and image/URL
conventions. This module converts those raw scraper outputs into one
`NormalizedProduct` schema so `scripts/data/build_db.py` can build consistent
SQLite tables for the downstream SQL recommendation flow.
"""

import json
import re
from dataclasses import dataclass, field
from collections import Counter
from taxonomy import PRODUCT_TYPE_ALIASES


@dataclass
class NormalizedProduct:
    id: str
    title: str
    product_type: str
    colors: list = field(default_factory=list)
    patterns: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    occasions: list = field(default_factory=list)
    gender: str = "female"
    price: float = 0.0
    url: str = ""
    image_url: str = ""
    description: str = ""


def _normalize_product_type(raw_type):
    """Map catalog product types to knowledge graph product names."""
    lower = raw_type.lower().strip()
    for kg_name, aliases in PRODUCT_TYPE_ALIASES.items():
        if lower == kg_name or lower in aliases:
            return kg_name
    return lower


class MasabaAdapter:
    """Adapter for House of Masaba (Shopify + rich tags, 971 products)."""

    def __init__(self, json_path):
        with open(json_path) as f:
            data = json.load(f)
        self.raw_products = data.get("products", data if isinstance(data, list) else [])
        self.products = [self._normalize(p) for p in self.raw_products]

    def _extract_tags_by_prefix(self, tags):
        result = {}
        for tag in tags:
            if "_" in tag:
                prefix, value = tag.split("_", 1)
                prefix_lower = prefix.lower()
                if prefix_lower not in result:
                    result[prefix_lower] = []
                result[prefix_lower].append(value)
        return result

    def _normalize(self, product):
        tags = product.get("tags", [])
        parsed = self._extract_tags_by_prefix(tags)

        colors = [c.lower() for c in parsed.get("color", [])]
        if "twin" in parsed:
            colors.extend(c.lower() for c in parsed["twin"])
        colors = list(set(colors))

        materials = [m.lower() for m in parsed.get("fabric", [])]
        occasions = [o.lower() for o in parsed.get("occasion", [])]
        patterns = [p.lower() for p in parsed.get("print", [])]

        gender_tags = parsed.get("gender", [])
        gender = "female"
        if any(g.lower() in ("men", "male") for g in gender_tags):
            gender = "male"

        raw_type = product.get("product_type", "")
        product_type = _normalize_product_type(raw_type)

        variants = product.get("variants", [])
        price = 0.0
        if variants:
            price = float(variants[0].get("price", 0))

        images = product.get("images", [])
        image_url = ""
        if images:
            img = images[0]
            image_url = img.get("src", "") if isinstance(img, dict) else str(img)

        body = product.get("body_html", "")
        description = re.sub(r"<[^>]+>", " ", body).strip()[:500]

        return NormalizedProduct(
            id=str(product.get("id", "")),
            title=product.get("title", ""),
            product_type=product_type,
            colors=colors,
            patterns=patterns,
            materials=materials,
            occasions=occasions,
            gender=gender,
            price=price,
            url=product.get("product_url", ""),
            image_url=image_url,
            description=description,
        )


class KalkiAdapter:
    """Adapter for Kalki Fashion (Shopify, 9664 products).
    Attributes extracted from titles and body_html since tags are minimal.
    """

    COLORS = [
        "red", "blue", "green", "yellow", "pink", "black", "white", "beige",
        "gold", "silver", "maroon", "navy", "purple", "orange", "peach",
        "cream", "ivory", "teal", "coral", "lavender", "magenta", "grey",
        "brown", "turquoise", "burgundy", "rust", "mustard", "olive",
        "aqua", "lime", "mint", "rose", "wine", "off white", "ombre",
        "multicolor", "multi", "pastel", "dusty pink", "dusty rose",
        "powder blue", "baby pink", "sky blue", "sea green", "peacock blue",
        "hot pink", "royal blue",
    ]

    MATERIALS = [
        "silk", "cotton", "georgette", "chiffon", "net", "velvet", "satin",
        "organza", "crepe", "linen", "rayon", "polyester", "lace", "tulle",
        "brocade", "jacquard", "denim", "jersey", "wool", "lycra", "modal",
        "chanderi", "banarasi", "chinon", "muslin", "tissue", "taffeta",
        "dola silk",
    ]

    PATTERNS = [
        "printed", "embroidered", "embroidery", "sequin", "sequins", "floral",
        "woven", "zari", "thread work", "mirror", "resham", "cutdana",
        "stone", "beaded", "applique", "chikankari", "bandhani", "tie dye",
        "polka", "striped", "checked", "paisley", "abstract", "animal print",
        "geometric", "block print", "digital", "foil",
    ]

    def __init__(self, json_path):
        with open(json_path) as f:
            data = json.load(f)
        self.raw_products = data.get("products", data if isinstance(data, list) else [])
        self.products = [self._normalize(p) for p in self.raw_products]

    def _extract_from_text(self, text, keywords):
        text_lower = text.lower()
        return list(set(k for k in keywords if k in text_lower))

    def _normalize(self, product):
        title = product.get("title", "")
        body = product.get("body_html", "")
        combined = f"{title} {body}"

        colors = self._extract_from_text(title, self.COLORS)
        materials = self._extract_from_text(combined, self.MATERIALS)
        patterns = self._extract_from_text(combined, self.PATTERNS)

        raw_type = product.get("product_type", "")
        product_type = _normalize_product_type(raw_type)

        gender = "female"
        if raw_type.lower() in ("men", "mens", "men's footwear"):
            gender = "male"
        elif raw_type.lower() == "kidswear":
            gender = "both"

        variants = product.get("variants", [])
        price = 0.0
        if variants:
            price = float(variants[0].get("price", 0))

        images = product.get("images", [])
        image_url = ""
        if images:
            img = images[0]
            image_url = img.get("src", "") if isinstance(img, dict) else str(img)

        description = re.sub(r"<[^>]+>", " ", body).strip()[:500]

        return NormalizedProduct(
            id=str(product.get("id", "")),
            title=title,
            product_type=product_type,
            colors=colors,
            patterns=patterns,
            materials=materials,
            occasions=[],
            gender=gender,
            price=price,
            url=product.get("product_url", ""),
            image_url=image_url,
            description=description,
        )


class AzaAdapter:
    """Adapter for Aza Fashions (NextJS/Unbxd, 224800 products).
    Cleanest schema with structured attributes.
    """

    CATEGORY_TO_PRODUCT = {
        "sarees": "saree",
        "lehengas": "lehenga",
        "kurta sets": "kurta",
        "kurtas": "kurta",
        "dresses": "dress",
        "gowns": "dress",
        "anarkalis": "salwar",
        "sharara sets": "salwar",
        "palazzo sets": "pant",
        "co-ord sets": "coord",
        "jackets": "jacket",
        "tops": "top",
        "shirts": "top",
        "skirts": "skirt",
        "pants": "pant",
        "trousers": "pant",
        "blouses": "top",
        "jumpsuits": "dress",
        "kaftans": "dress",
        "sherwani sets": "sherwani",
        "kurta pajama sets": "kurta",
        "nehru jackets": "jacket",
        "blazers": "jacket",
    }

    def __init__(self, json_path, max_products=None):
        with open(json_path) as f:
            data = json.load(f)
        raw = data.get("products", data if isinstance(data, list) else [])
        if max_products:
            raw = raw[:max_products]
        self.raw_products = raw
        self.products = [self._normalize(p) for p in self.raw_products]

    def _normalize(self, product):
        category = product.get("category", {}) or {}
        level2 = (category.get("level2") or "").lower()
        product_type = self.CATEGORY_TO_PRODUCT.get(level2, level2)
        if not product_type:
            level1 = (category.get("level1") or "").lower()
            product_type = level1

        colors = [c.lower() for c in (product.get("color") or [])]

        attrs = product.get("attributes", {}) or {}
        materials = [m.lower() for m in (attrs.get("fabric") or [])]
        patterns = [p.lower() for p in (attrs.get("pattern") or [])]
        occasions = [o.lower() for o in (attrs.get("occasion") or [])]

        audience = product.get("audience") or []
        gender = "female"
        if any(a.lower() in ("men", "boys") for a in audience):
            gender = "male"

        price = float(product.get("selling_price") or product.get("price") or 0)

        images = product.get("image_url") or []
        image_url = images[0] if images else ""

        return NormalizedProduct(
            id=str(product.get("id", "")),
            title=product.get("title", ""),
            product_type=product_type,
            colors=colors,
            patterns=patterns,
            materials=materials,
            occasions=occasions,
            gender=gender,
            price=price,
            url=product.get("product_url", ""),
            image_url=image_url,
            description=product.get("stylist_note") or "",
        )


def load_catalog(brand, catalog_path, max_products=None):
    """Load and normalize a product catalog."""
    adapters = {
        "masaba": MasabaAdapter,
        "house of masaba": MasabaAdapter,
        "kalki": KalkiAdapter,
        "kalki fashion": KalkiAdapter,
        "aza": AzaAdapter,
        "aza fashions": AzaAdapter,
    }
    brand_lower = brand.lower()
    adapter_cls = adapters.get(brand_lower)
    if not adapter_cls:
        raise ValueError(f"Unknown brand: {brand}")

    if adapter_cls == AzaAdapter and max_products:
        return adapter_cls(catalog_path, max_products=max_products)
    return adapter_cls(catalog_path)
