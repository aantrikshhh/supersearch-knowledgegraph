"""Centralized configuration — paths, model names, constants."""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Knowledge Graph
KG_PATH = os.path.join(BASE_DIR, "Master_Graph.xlsx")

# Complementary Graphs
COMP_GRAPHS_DIR = os.path.join(BASE_DIR, "assistant", "all_graph_components")
COMP_COLOURS_PATH = os.path.join(COMP_GRAPHS_DIR, "complimentary_colours_graph.xlsx")
COMP_PRODUCTS_PATH = os.path.join(COMP_GRAPHS_DIR, "complimentary_product_graph.xlsx")
COMP_FEATURES_PATH = os.path.join(COMP_GRAPHS_DIR, "complimentary_features_graph.xlsx")
BAG_INFO_PATH = os.path.join(COMP_GRAPHS_DIR, "bag_info.csv")
SHOES_INFO_PATH = os.path.join(COMP_GRAPHS_DIR, "shoes_info.csv")
JEWELLERY_INFO_PATH = os.path.join(COMP_GRAPHS_DIR, "jewellery_info.csv")

# Product Databases
DB_DIR = BASE_DIR
BRAND_DB_PATHS = {
    "masaba": os.path.join(DB_DIR, "masaba_products.db"),
    "kalki": os.path.join(DB_DIR, "kalki_products.db"),
    "aza": os.path.join(DB_DIR, "aza_products.db"),
}

# Product Catalog Sources
CATALOG_PATHS = {
    "masaba": "/Users/aant/repos/scraper-infra/data/house_of_masaba_products.json",
    "kalki": "/Users/aant/repos/scraper-infra/data/kalki_fashion_products.json",
    "aza": "/Users/aant/repos/scraper-infra/data/aza_fashions_products.json",
}

# LLM
# Default to a Codex CLI model supported by the installed local CLI. Override
# with KG_LLM_MODEL if the deployment has access to a smaller/faster model.
LLM_MODEL = os.environ.get("KG_LLM_MODEL", "gpt-5.2")
LLM_TIMEOUT = 120

# Eval
EVAL_RESULTS_DIR = os.path.join(BASE_DIR, "eval_results")
GOLDEN_EVAL_PATH = os.path.join(BASE_DIR, "golden_eval_set.json")

# Weather lookup — major Indian cities + popular destinations
WEATHER_TABLE = {
    ("goa", "january"): {"weather": "winter", "temp": "warm", "humidity": "low", "rain": False},
    ("goa", "february"): {"weather": "winter", "temp": "warm", "humidity": "low", "rain": False},
    ("goa", "march"): {"weather": "summer", "temp": "hot", "humidity": "moderate", "rain": False},
    ("goa", "april"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("goa", "may"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("goa", "june"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("goa", "july"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("goa", "august"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("goa", "september"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("goa", "october"): {"weather": "rainy", "temp": "warm", "humidity": "moderate", "rain": True},
    ("goa", "november"): {"weather": "winter", "temp": "warm", "humidity": "low", "rain": False},
    ("goa", "december"): {"weather": "winter", "temp": "warm", "humidity": "low", "rain": False},
    ("delhi", "january"): {"weather": "winter", "temp": "cold", "humidity": "low", "rain": False},
    ("delhi", "february"): {"weather": "winter", "temp": "cold", "humidity": "low", "rain": False},
    ("delhi", "march"): {"weather": "summer", "temp": "warm", "humidity": "low", "rain": False},
    ("delhi", "april"): {"weather": "summer", "temp": "hot", "humidity": "low", "rain": False},
    ("delhi", "may"): {"weather": "summer", "temp": "hot", "humidity": "moderate", "rain": False},
    ("delhi", "june"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("delhi", "july"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("delhi", "august"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("delhi", "september"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("delhi", "october"): {"weather": "summer", "temp": "warm", "humidity": "moderate", "rain": False},
    ("delhi", "november"): {"weather": "winter", "temp": "cool", "humidity": "low", "rain": False},
    ("delhi", "december"): {"weather": "winter", "temp": "cold", "humidity": "low", "rain": False},
    ("mumbai", "january"): {"weather": "winter", "temp": "warm", "humidity": "moderate", "rain": False},
    ("mumbai", "february"): {"weather": "winter", "temp": "warm", "humidity": "moderate", "rain": False},
    ("mumbai", "march"): {"weather": "summer", "temp": "hot", "humidity": "moderate", "rain": False},
    ("mumbai", "april"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("mumbai", "may"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("mumbai", "june"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("mumbai", "july"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("mumbai", "august"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("mumbai", "september"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("mumbai", "october"): {"weather": "summer", "temp": "warm", "humidity": "high", "rain": False},
    ("mumbai", "november"): {"weather": "winter", "temp": "warm", "humidity": "moderate", "rain": False},
    ("mumbai", "december"): {"weather": "winter", "temp": "warm", "humidity": "moderate", "rain": False},
    ("bangalore", "january"): {"weather": "winter", "temp": "cool", "humidity": "low", "rain": False},
    ("bangalore", "february"): {"weather": "winter", "temp": "warm", "humidity": "low", "rain": False},
    ("bangalore", "march"): {"weather": "summer", "temp": "warm", "humidity": "low", "rain": False},
    ("bangalore", "april"): {"weather": "summer", "temp": "hot", "humidity": "moderate", "rain": True},
    ("bangalore", "may"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("bangalore", "june"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("bangalore", "july"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("bangalore", "august"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("bangalore", "september"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("bangalore", "october"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("bangalore", "november"): {"weather": "winter", "temp": "cool", "humidity": "moderate", "rain": True},
    ("bangalore", "december"): {"weather": "winter", "temp": "cool", "humidity": "low", "rain": False},
    ("jaipur", "january"): {"weather": "winter", "temp": "cold", "humidity": "low", "rain": False},
    ("jaipur", "march"): {"weather": "summer", "temp": "warm", "humidity": "low", "rain": False},
    ("jaipur", "may"): {"weather": "summer", "temp": "hot", "humidity": "low", "rain": False},
    ("jaipur", "july"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("jaipur", "october"): {"weather": "summer", "temp": "warm", "humidity": "moderate", "rain": False},
    ("jaipur", "december"): {"weather": "winter", "temp": "cold", "humidity": "low", "rain": False},
    ("hyderabad", "january"): {"weather": "winter", "temp": "cool", "humidity": "low", "rain": False},
    ("hyderabad", "may"): {"weather": "summer", "temp": "hot", "humidity": "moderate", "rain": False},
    ("hyderabad", "july"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("hyderabad", "december"): {"weather": "winter", "temp": "cool", "humidity": "low", "rain": False},
    ("chennai", "january"): {"weather": "winter", "temp": "warm", "humidity": "moderate", "rain": False},
    ("chennai", "may"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("chennai", "july"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("chennai", "november"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("chennai", "december"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("kolkata", "january"): {"weather": "winter", "temp": "cool", "humidity": "moderate", "rain": False},
    ("kolkata", "may"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("kolkata", "july"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("kolkata", "october"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("kolkata", "december"): {"weather": "winter", "temp": "cool", "humidity": "moderate", "rain": False},
    ("lucknow", "january"): {"weather": "winter", "temp": "cold", "humidity": "moderate", "rain": False},
    ("lucknow", "may"): {"weather": "summer", "temp": "hot", "humidity": "low", "rain": False},
    ("lucknow", "july"): {"weather": "rainy", "temp": "warm", "humidity": "high", "rain": True},
    ("lucknow", "december"): {"weather": "winter", "temp": "cold", "humidity": "moderate", "rain": False},
    ("udaipur", "january"): {"weather": "winter", "temp": "cool", "humidity": "low", "rain": False},
    ("udaipur", "may"): {"weather": "summer", "temp": "hot", "humidity": "low", "rain": False},
    ("udaipur", "july"): {"weather": "rainy", "temp": "warm", "humidity": "moderate", "rain": True},
    ("udaipur", "december"): {"weather": "winter", "temp": "cool", "humidity": "low", "rain": False},
    ("dubai", "january"): {"weather": "winter", "temp": "warm", "humidity": "moderate", "rain": False},
    ("dubai", "may"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("dubai", "july"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("dubai", "december"): {"weather": "winter", "temp": "warm", "humidity": "moderate", "rain": False},
    ("london", "january"): {"weather": "winter", "temp": "cold", "humidity": "high", "rain": True},
    ("london", "april"): {"weather": "winter", "temp": "cool", "humidity": "moderate", "rain": True},
    ("london", "july"): {"weather": "summer", "temp": "warm", "humidity": "moderate", "rain": False},
    ("london", "december"): {"weather": "winter", "temp": "cold", "humidity": "high", "rain": True},
    ("paris", "january"): {"weather": "winter", "temp": "cold", "humidity": "high", "rain": True},
    ("paris", "july"): {"weather": "summer", "temp": "warm", "humidity": "moderate", "rain": False},
    ("paris", "december"): {"weather": "winter", "temp": "cold", "humidity": "high", "rain": True},
    ("new york", "january"): {"weather": "snowy", "temp": "cold", "humidity": "moderate", "rain": False},
    ("new york", "july"): {"weather": "summer", "temp": "hot", "humidity": "high", "rain": False},
    ("new york", "december"): {"weather": "snowy", "temp": "cold", "humidity": "moderate", "rain": False},
}

# Formality hierarchy
FORMALITY_LEVELS = {
    "formal": {
        "occasions": ["hindu wedding", "muslim wedding", "christian wedding", "engagement",
                      "roka", "sangeet", "reception", "gala", "corporate event"],
        "product_bias": ["saree", "lehenga", "salwar", "sherwani"],
        "material_bias": ["silk", "velvet", "brocade", "satin", "organza"],
        "pattern_bias": ["embroidered", "embellished", "sequin", "zari"],
    },
    "semi_formal": {
        "occasions": ["mehendi", "haldi", "anniversary", "graduation", "prom",
                      "date night", "interview", "farewell party", "retirement party"],
        "product_bias": ["kurta", "dress", "coord", "salwar"],
        "material_bias": ["georgette", "chiffon", "crepe", "silk"],
        "pattern_bias": ["ethnic", "floral", "embroidered"],
    },
    "casual": {
        "occasions": ["birthday party", "reunion", "office party", "picnic",
                      "concert", "housewarming", "baby shower"],
        "product_bias": ["dress", "coord", "kurta", "top"],
        "material_bias": ["cotton", "linen", "rayon", "denim"],
        "pattern_bias": ["printed", "floral", "casual", "striped"],
    },
    "festive": {
        "occasions": ["festival", "bachelorette"],
        "events": ["Diwali", "Holi", "Navratri", "Ganesh Chaturthi", "Christmas",
                    "Eid", "Pongal", "Onam", "Bihu", "Lohri"],
        "product_bias": ["saree", "lehenga", "kurta", "salwar"],
        "material_bias": ["silk", "georgette", "chiffon"],
        "pattern_bias": ["ethnic", "embellished", "sequin", "mirror work"],
    },
    "mourning": {
        "occasions": ["funeral"],
        "product_bias": ["saree", "salwar", "kurta"],
        "material_bias": ["cotton"],
        "color_bias": ["white", "off white", "cream"],
        "avoid_colors": ["red", "pink", "bright"],
    },
}

def get_formality(occasion=None, event=None):
    """Get formality level for an occasion or event."""
    if occasion:
        for level, config in FORMALITY_LEVELS.items():
            if occasion in config.get("occasions", []):
                return level, config
    if event:
        for level, config in FORMALITY_LEVELS.items():
            if event in config.get("events", []):
                return level, config
    return "casual", FORMALITY_LEVELS["casual"]
