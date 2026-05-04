"""Shared fashion taxonomy and canonicalization rules.

Keep this module limited to stable vocabulary mappings that multiple runtime
paths need. Scenario-specific preferences belong in the KG, prompts, or eval
fixtures, not here.
"""

INTENT_ALIASES = {
    "occasion": {
        "Indian wedding": "wedding",
        "indian wedding": "wedding",
        "griha pravesh": "housewarming",
        "rice ceremony": "baby shower",
        "college fest": "festival",
        "reception": "wedding",
    },
    "event": {
        "Puja": "Lakshmi Puja",
        "Disneyland": "Coachella",
    },
}

GENERIC_ENTITY_EXPANSIONS = {
    ("occasion", "wedding"): ["hindu wedding", "muslim wedding", "christian wedding"],
}

PRODUCT_TYPE_ALIASES = {
    "salwar": [
        "salwar kameez", "salwar suit", "churidar", "anarkali",
        "anarkali suit", "sharara", "sharara set", "gharara",
    ],
    "coord": [
        "co ord set", "co-ord set", "coordinate set", "co ord",
        "coord set", "fusion set", "fusion sets",
    ],
    "kurta": ["kurta set", "kurta sets", "kurti", "kurtas & tunics"],
    "pant": ["palazzo", "palazzo set", "pallazo set", "bottoms", "trousers"],
    "dress": ["dresses", "gown", "gowns", "maxi dress", "midi dress", "jumpsuit"],
    "top": ["tops & shirts", "shirt", "blouse", "crop top", "croptop"],
    "lehenga": ["lehengas", "lehenga set", "lehenga sets", "chaniya choli", "ghagra choli", "choli"],
    "jacket": ["jackets", "bandi", "bandi set", "bandi sets"],
    "skirt": ["skirt set", "skirts"],
    "swimsuit": ["swimwear"],
    "saree": ["sarees", "sari"],
    "sherwani": ["sherwani sets", "sherwanis", "raja koti set", "bandhgala", "bandhgalas", "achkan", "jodhpuri"],
    "kaftan": ["kaftans"],
    "tracksuit": ["trackees", "trackee"],
    "scarf": ["scarves", "scarf", "shawl", "shawls", "stole", "stoles", "scarves & stoles"],
}

RELATION_ALIASES = {
    "mother": "mom",
    "mom": "mom",
    "mum": "mom",
    "sister": "sister",
    "wife": "wife",
    "daughter": "daughter",
    "niece": "niece",
    "aunt": "aunt",
    "aunty": "aunt",
    "grandmother": "grandmother",
    "grandma": "grandmother",
    "friend": "friend",
    "colleague": "colleague",
    "coworker": "colleague",
    "co-worker": "colleague",
    "boss": "boss",
    "father": "dad",
    "dad": "dad",
    "brother": "brother",
    "husband": "husband",
    "son": "son",
    "nephew": "nephew",
    "uncle": "uncle",
    "grandfather": "grandfather",
    "grandpa": "grandfather",
}

# Only encode gender when the relation itself is gendered. Do not infer age,
# style, or gender for neutral relations like friend/colleague/boss.
RELATION_GENDERS = {
    "mom": "female",
    "sister": "female",
    "wife": "female",
    "daughter": "female",
    "niece": "female",
    "aunt": "female",
    "grandmother": "female",
    "dad": "male",
    "brother": "male",
    "husband": "male",
    "son": "male",
    "nephew": "male",
    "uncle": "male",
    "grandfather": "male",
}

GIFT_TERMS = ("gift", "gifting", "present")
GIFT_ACTION_PATTERNS = (
    r"\b(?:buy|buying|get|getting|pick|picking)\b.+\bfor\b",
    r"\b(?:present|gift)\b.+\bfor\b",
)

RELIGION_KEYWORDS = {
    "hindu": "Hinduism",
    "hinduism": "Hinduism",
    "muslim": "Islam",
    "islam": "Islam",
    "islamic": "Islam",
    "nikah": "Islam",
    "nikkah": "Islam",
    "christian": "Christianity",
    "christianity": "Christianity",
    "church": "Christianity",
    "sikh": "Sikhism",
    "sikhism": "Sikhism",
    "gurudwara": "Sikhism",
    "anand karaj": "Sikhism",
}

RELIGION_WEDDING_TERMS = tuple(RELIGION_KEYWORDS.keys())

USER_GENDER_TERMS = {
    "female": (
        "women", "woman", "female", "girl", "girls", "wife", "mother",
        "mom", "lady", "ladies", "bride", "bridesmaid",
    ),
    "male": (
        "men", "man", "male", "boy", "boys", "husband", "father",
        "dad", "gentleman", "gentlemen", "groom", "groomsman",
    ),
}

AGEGROUP_TERMS = {
    "child": ("kid", "kids", "child", "children", "tween", "toddler"),
    "teenager": ("teenage", "teenager", "teen"),
}
