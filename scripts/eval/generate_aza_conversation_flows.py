"""Generate an Aza-backed shopper-general conversational eval suite.

The generated scenarios are intentionally about realistic shopper behavior on
an Indian fashion marketplace, not Aza-specific category overfitting. Aza is
used as the inventory grounding catalog because it is the largest local DB.
"""

import argparse
import json
import random
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT_DIR / "data" / "eval" / "generated" / "aza_conversation_500_seed13.json"


SOURCE_TAGS = [
    "workflow_doc",
    "aza_inventory_grounded",
    "shopper_general",
    "web_research",
]


def expect(workflow=None, needs_clarification=False, is_followup=None,
           intent_equals=None, intent_contains=None, allow_catalog_gap=False,
           min_primary_products=None, min_db_products=None, product_types_all=None,
           sql_contains=None, clarifying_contains=None):
    data = {}
    if workflow:
        data["workflow"] = workflow
    data["needs_clarification"] = needs_clarification
    if is_followup is not None:
        data["is_followup"] = is_followup
    if intent_equals:
        data["intent_equals"] = intent_equals
    if intent_contains:
        data["intent_contains"] = intent_contains
    if allow_catalog_gap:
        data["allow_catalog_gap"] = True
    if min_primary_products is not None:
        data["min_primary_products"] = min_primary_products
    if min_db_products is not None:
        data["min_db_products"] = min_db_products
    if product_types_all:
        data["product_types_all"] = product_types_all
    if sql_contains:
        data["sql_contains"] = sql_contains
    if clarifying_contains:
        data["clarifying_contains"] = clarifying_contains
    return data


def scenario(category, idx, turns, description):
    return {
        "id": f"aza_{category}_{idx:03d}",
        "brand": "aza",
        "category": category,
        "description": description,
        "source_tags": SOURCE_TAGS,
        "turns": turns,
    }


def turn(user, expectation):
    return {"user": user, "expect": expectation}


def build_occasion(n, rng):
    occasions = [
        ("sangeet", "lehenga", "women"),
        ("mehendi", "sharara", "women"),
        ("haldi", "kurta", "women"),
        ("engagement", "saree", "women"),
        ("reception", "gown", "women"),
        ("Diwali", "kurta set", "women"),
        ("Eid", "anarkali", "women"),
        ("Navratri garba", "chaniya choli", "women"),
        ("Onam", "saree", "women"),
        ("cocktail party", "dress", "women"),
        ("roka", "saree", "women"),
        ("office party", "coord set", "women"),
        ("birthday party", "dress", "women"),
        ("baby shower", "dress", "women"),
        ("graduation", "kurta set", "women"),
        ("groomsmen sangeet", "kurta jacket set", "men"),
        ("wedding reception", "bandhgala", "men"),
        ("Diwali dinner", "kurta", "men"),
        ("Eid lunch", "sherwani", "men"),
        ("kids Diwali party", "lehenga", "girl"),
    ]
    modifiers = [
        "under 20000",
        "not too heavy",
        "breathable for humid weather",
        "premium but not bridal",
        "simple and elegant",
        "dance-friendly",
        "in blue",
        "not red",
        "for summer",
        "with minimal embroidery",
    ]
    flows = []
    for i in range(n):
        occ, product, audience = occasions[i % len(occasions)]
        mod = modifiers[(i // len(occasions)) % len(modifiers)]
        if i % 17 == 0:
            user = f"Affordable wedding guest outfit for {audience}"
            exp = expect(
                "occasion",
                needs_clarification=True,
                clarifying_contains=["wedding context"],
                intent_equals={"occasion": "wedding"},
            )
        else:
            user = f"{product} for {occ} {audience} {mod}"
            exp = expect(
                "occasion",
                needs_clarification=False,
                min_db_products=1,
                allow_catalog_gap=i % 23 == 0,
            )
        flows.append(scenario("occasion", i + 1, [turn(user, exp)], "Occasion/festival/wedding shopper query."))
    rng.shuffle(flows)
    return flows[:n]


def build_gifting(n, rng):
    recipients = [
        ("mom", "saree", "birthday", "female"),
        ("dad", "kurta", "retirement", "male"),
        ("wife", "dress", "anniversary", "female"),
        ("husband", "bandhgala", "Diwali", "male"),
        ("sister", "lehenga", "sangeet", "female"),
        ("brother", "sherwani", "wedding", "male"),
        ("daughter", "kurta set", "Diwali", "female"),
        ("son", "kurta", "Eid", "male"),
        ("colleague", "shawl", "retirement", None),
        ("friend", "dress", "baby shower", None),
    ]
    styles = [
        "under 15000",
        "premium",
        "traditional",
        "modern",
        "not too flashy",
        "comfortable",
        "ready for a formal event",
        "in pastel colors",
    ]
    flows = []
    for i in range(n):
        relation, product, event, gender = recipients[i % len(recipients)]
        style = styles[(i // len(recipients)) % len(styles)]
        audience = "" if gender else (" women" if i % 2 else "")
        user = f"{event} gift {product} for {relation}{audience} {style}"
        needs = gender is None and not audience
        exp = expect(
            "gifting",
            needs_clarification=needs,
            clarifying_contains=["women, men, or kids"] if needs else None,
            intent_equals={"relation": relation} if relation not in ("friend", "colleague") else None,
            allow_catalog_gap=product in {"shawl"},
            min_db_products=None if needs else 1,
        )
        flows.append(scenario("gifting", i + 1, [turn(user, exp)], "Recipient-aware gifting query."))
    rng.shuffle(flows)
    return flows[:n]


def build_refinement(n, rng):
    bases = [
        "Show lehengas for sangeet women",
        "Kurta set for Diwali women under 25000",
        "Saree for engagement women",
        "Sherwani for wedding men",
        "Dress for cocktail party women",
    ]
    followups = [
        ("show cheaper ones", {"budget": "affordable"}),
        ("in blue", None),
        ("not red", None),
        ("only sarees", {"product_type": "saree"}),
        ("make it lightweight", None),
        ("show something more modest", None),
        ("under 10000", None),
        ("matching jewellery for this", None),
        ("no lehengas", None),
        ("more premium options", {"budget": "premium"}),
    ]
    flows = []
    for i in range(n):
        base = bases[i % len(bases)]
        follow, intent_eq = followups[(i // len(bases)) % len(followups)]
        turns = [
            turn(base, expect("occasion", needs_clarification=False, min_db_products=1)),
            turn(follow, expect(
                "occasion",
                needs_clarification=False,
                is_followup=True,
                intent_equals=intent_eq,
                allow_catalog_gap="jewellery" in follow,
            )),
        ]
        flows.append(scenario("refinement", i + 1, turns, "Post-result shopper refinement."))
    rng.shuffle(flows)
    return flows[:n]


def build_topic_switch(n, rng):
    first_queries = [
        "Show sarees for engagement women",
        "I need a lehenga for sangeet women",
        "Find a kurta for Diwali men",
        "Show gowns for cocktail women",
    ]
    switches = [
        ("Forget that, show office kurta sets for women", "place_profession"),
        ("Actually this is for my dad for Diwali, show kurta", "occasion"),
        ("Now I need a 5-day Goa vacation wardrobe women December", "vacation"),
        ("Different question: gift shawl for colleague women retirement", "gifting"),
        ("Start over, airport outfit for women", "place_profession"),
        ("Ignore that, I need breathable clothes for back pain women", "health"),
    ]
    flows = []
    for i in range(n):
        second, workflow = switches[i % len(switches)]
        turns = [
            turn(first_queries[i % len(first_queries)], expect("occasion", needs_clarification=False)),
            turn(second, expect(workflow, needs_clarification=False, is_followup=False, allow_catalog_gap="shawl" in second)),
        ]
        flows.append(scenario("topic_switch", i + 1, turns, "Conversation reset or topic switch."))
    rng.shuffle(flows)
    return flows[:n]


def build_place_profession(n, rng):
    queries = [
        "Airport outfit for women that is travel-friendly",
        "Office kurta set for women under 15000",
        "Temple visit outfit for women modest",
        "Cafe brunch dress for women",
        "Museum day outfit for women in summer",
        "Court outfit for lawyer women",
        "College outfit for student women",
        "Restaurant dinner outfit for men",
        "Teacher classroom outfit women",
        "Doctor clinic outfit women premium",
    ]
    flows = []
    for i in range(n):
        user = queries[i % len(queries)]
        flows.append(scenario("place_profession", i + 1, [
            turn(user, expect("place_profession", needs_clarification=False, min_db_products=1))
        ], "Place or profession shopping query."))
    rng.shuffle(flows)
    return flows[:n]


def build_vacation(n, rng):
    queries = [
        "5-day Goa vacation wardrobe for women December",
        "3-day Jaipur wedding trip outfits for women January",
        "4-day Kerala trip wardrobe for women August",
        "7-day Dubai summer vacation outfits for men",
        "5-day Europe summer capsule wardrobe for women",
        "3-day beach trip outfits women humid weather",
        "6-day destination wedding packing list women Udaipur December",
        "4-day London winter trip Indian outfits women",
        "5-day Sri Lanka vacation wardrobe women",
    ]
    flows = []
    for i in range(n):
        user = queries[i % len(queries)]
        flows.append(scenario("vacation", i + 1, [
            turn(user, expect("vacation", needs_clarification=False, allow_catalog_gap=i % 11 == 0))
        ], "Vacation or capsule wardrobe query."))
    rng.shuffle(flows)
    return flows[:n]


def build_activity_health(n, rng):
    queries = [
        ("Dance-friendly lehenga for sangeet women", "occasion"),
        ("Breathable summer general wedding guest outfit women", "occasion"),
        ("Comfortable kurta for back pain women", "health"),
        ("Sensitive skin festive outfit women cotton", "health"),
        ("Sweat-proof outfit for humid Mumbai music festival women", "occasion"),
        ("Lightweight outfit for dancing women", "activity"),
        ("Waterproof jacket for rainy travel women", "activity"),
        ("Yoga-friendly coord set women", "activity"),
        ("Hiking outfit for women but still stylish", "activity"),
        ("Warm shawl for winter wedding women", "occasion"),
    ]
    flows = []
    for i in range(n):
        user, workflow = queries[i % len(queries)]
        flows.append(scenario("activity_health", i + 1, [
            turn(user, expect(workflow, needs_clarification=False, allow_catalog_gap=i % 5 == 2))
        ], "Functional, activity, comfort, or health query."))
    rng.shuffle(flows)
    return flows[:n]


def build_general(n, rng):
    queries = [
        "I want something elegant for women",
        "Show festive outfits for women",
        "Need a statement piece under 30000 women",
        "Something minimalist but Indian women",
        "Show premium designer wear for men",
        "I want a classic outfit for women",
        "Show me modest ethnic wear women",
        "Need something flattering for petite women",
        "What should I wear if I want to look taller women",
        "Show bold colorful Indian outfits women",
    ]
    flows = []
    for i in range(n):
        user = queries[i % len(queries)]
        flows.append(scenario("general", i + 1, [
            turn(user, expect("general", needs_clarification=False, min_db_products=1))
        ], "Sparse discovery query."))
    rng.shuffle(flows)
    return flows[:n]


def build_comparison_accessory(n, rng):
    bases = [
        "Show sarees for general wedding guest women",
        "Show lehengas for sangeet women",
        "Show kurta sets for Diwali women",
        "Show sherwanis for groom men",
    ]
    followups = [
        "Which one is better for evening?",
        "What jewellery works with this?",
        "Do you have a matching shawl?",
        "Can I wear this to a temple also?",
        "Show a matching bag",
    ]
    flows = []
    for i in range(n):
        base = bases[i % len(bases)]
        follow = followups[(i // len(bases)) % len(followups)]
        first_expect = expect("occasion", needs_clarification=False)
        flows.append(scenario("comparison_accessory", i + 1, [
            turn(base, first_expect),
            turn(follow, expect("occasion", needs_clarification=False, is_followup=True, allow_catalog_gap="shawl" in follow)),
        ], "Comparison or accessory follow-up."))
    rng.shuffle(flows)
    return flows[:n]


def build_suite(seed):
    rng = random.Random(seed)
    sections = [
        build_occasion(100, rng),
        build_gifting(80, rng),
        build_refinement(75, rng),
        build_topic_switch(60, rng),
        build_place_profession(50, rng),
        build_vacation(45, rng),
        build_activity_health(40, rng),
        build_general(30, rng),
        build_comparison_accessory(20, rng),
    ]
    suite = [item for section in sections for item in section]
    assert len(suite) == 500, len(suite)
    return suite


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--jsonl-out", default=None)
    args = parser.parse_args()

    suite = build_suite(args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        json.dump(suite, f, indent=2)

    jsonl_out = Path(args.jsonl_out) if args.jsonl_out else out.with_suffix(".jsonl")
    with jsonl_out.open("w") as f:
        for scenario_data in suite:
            f.write(json.dumps(scenario_data) + "\n")

    print(f"Wrote {len(suite)} scenarios to {out}")
    print(f"Wrote JSONL to {jsonl_out}")


if __name__ == "__main__":
    main()
