"""Regression tests for core SuperSearch runtime query handling.

These tests lock down base-flow behavior: budget extraction, explicit product
types, cultural event aliases, negated product requests, follow-up detection,
clarification state, and deterministic SQL constraints.
"""

import unittest
from unittest.mock import patch

from conversation import ConversationManager
from brand_adapters import AzaAdapter
from db_query import _deterministic_sql, generate_sql, get_available_types
from intent_extractor import _enrich_intents, normalize_intents
from knowledge_graph import KnowledgeGraph
from outfit_builder import OutfitResult
from router import classify
from scripts.eval.run_conversation_eval import check_expectations, product_type_matches
from session import Session
from config import KG_PATH


class RuntimeFlowRegressionTests(unittest.TestCase):
    def test_budget_product_event_fallback(self):
        intents = _enrich_intents({}, "Kurta set under 2000 for Diwali women")
        self.assertEqual(intents["event"], "Diwali")
        self.assertEqual(intents["product_type"], "kurta")
        self.assertEqual(intents["price_max"], 2000)
        self.assertEqual(intents["gender"], "female")

    def test_not_too_heavy_maps_to_lightweight_functional_need(self):
        intents = _enrich_intents({}, "Coord set for office party women not too heavy")
        self.assertIn("lightweight", intents["functional_needs"])

    def test_llm_product_alias_is_canonicalized(self):
        intents = _enrich_intents({"product_type": "bandi"}, "Bandi for Diwali men")
        self.assertEqual(intents["product_type"], "jacket")

    def test_chaniya_choli_does_not_trigger_holi(self):
        intents = _enrich_intents({}, "Navratri garba chaniya choli")
        self.assertEqual(intents["event"], "Navratri")
        self.assertEqual(intents["product_type"], "lehenga")

    def test_negated_product_type_is_not_requested_type(self):
        intents = _enrich_intents({}, "not a saree, show me lehengas")
        self.assertEqual(intents["product_type"], "lehenga")
        self.assertEqual(intents["avoid_product_type"], "saree")

    def test_hyphenated_vacation_duration(self):
        intents = _enrich_intents({}, "5-day Goa vacation wardrobe set women December")
        self.assertTrue(intents["_is_vacation"])
        self.assertEqual(intents["duration"], 5)
        self.assertEqual(intents["location"], "Goa")
        self.assertEqual(intents["month"], "December")

    def test_full_query_after_turn_is_not_followup(self):
        session = Session()
        session.add_turn(
            "show sarees for sangeet",
            {"product_type": "saree", "occasion": "sangeet"},
            "occasion",
        )
        conv = ConversationManager(session=session)
        self.assertFalse(conv._is_followup("Kurta set under 2000 for Diwali women"))
        self.assertTrue(conv._is_followup("in red"))

    def test_clarification_answer_preserves_previous_constraints(self):
        session = Session()
        session.active_intents = {
            "occasion": "wedding",
            "budget": "affordable",
            "_needs_religion": True,
        }
        session.merge_intents({"gender": "female"})
        self.assertEqual(session.active_intents["budget"], "affordable")
        self.assertTrue(session.active_intents["_needs_religion"])
        session.merge_intents({"religion": "Hinduism", "_needs_religion": False})
        self.assertEqual(session.active_intents["budget"], "affordable")
        self.assertNotIn("_needs_religion", session.active_intents)

    def test_religion_clarification_answer_is_deterministic(self):
        intents = normalize_intents("Sikh", {"occasion": "wedding", "_needs_religion": True})
        self.assertEqual(intents["religion"], "Sikhism")
        self.assertFalse(intents["_needs_religion"])

    def test_gifting_queries_route_to_gifting(self):
        queries = [
            "Birthday gift saree for mom 50 years traditional",
            "Baby shower gift dress women",
            "Anniversary gift dress for wife elegant",
            "Diwali gift kurta set teenage girl",
            "Retirement gift shawl women colleague",
        ]
        for query in queries:
            intents = normalize_intents(query)
            self.assertTrue(intents.get("_is_gift"), query)
            self.assertEqual(classify(intents, query).value, "gifting", query)

    def test_relation_without_gift_does_not_route_to_gifting(self):
        query = "Suggest an outfit for my friend's bachelorette party"
        intents = normalize_intents(query)
        self.assertNotIn("_is_gift", intents)
        self.assertEqual(classify(intents, query).value, "occasion")

    def test_generic_wedding_needs_religion_clarification(self):
        query = "Affordable wedding guest outfit - don't want to spend too much"
        intents = normalize_intents(query)
        self.assertEqual(intents["occasion"], "wedding")
        self.assertTrue(intents["_needs_religion"])
        conv = ConversationManager()
        questions = conv._get_clarifying_questions(query, intents, classify(intents, query))
        self.assertTrue(any("wedding context" in q for q in questions))

    def test_explicit_product_wedding_can_proceed_without_religion(self):
        query = "Sherwani for wedding men"
        intents = normalize_intents(query)
        conv = ConversationManager()
        questions = conv._get_clarifying_questions(query, intents, classify(intents, query))
        self.assertEqual(intents["product_type"], "sherwani")
        self.assertFalse(any("wedding context" in q for q in questions))

    def test_general_wedding_answer_does_not_reask_religion(self):
        query = "general wedding guest outfit women"
        intents = normalize_intents(query, {"occasion": "wedding", "_needs_religion": True})
        self.assertFalse(intents["_needs_religion"])
        conv = ConversationManager()
        questions = conv._get_clarifying_questions(query, intents, classify(intents, query))
        self.assertFalse(any("wedding context" in q for q in questions))

    def test_generic_wedding_gets_kg_context(self):
        kg = KnowledgeGraph(KG_PATH)
        result = kg.lookup({"occasion": "wedding"}, gender="female")
        self.assertTrue(result.get("product", {}).get("recommended"))

    def test_missing_gender_gets_clarification_question(self):
        query = "Affordable music festival outfit for humid Mumbai"
        intents = normalize_intents(query)
        conv = ConversationManager()
        questions = conv._get_clarifying_questions(query, intents, classify(intents, query))
        self.assertTrue(any("women, men, or kids" in q for q in questions))

    def test_neutral_gift_recipient_gets_gender_clarification(self):
        query = "Birthday gift kurta for colleague"
        intents = normalize_intents(query)
        conv = ConversationManager()
        questions = conv._get_clarifying_questions(query, intents, classify(intents, query))
        self.assertTrue(any("women, men, or kids" in q for q in questions))

    def test_retirement_does_not_count_as_men_gender_signal(self):
        query = "Retirement gift shawl for colleague"
        intents = normalize_intents(query)
        conv = ConversationManager()
        questions = conv._get_clarifying_questions(query, intents, classify(intents, query))
        self.assertNotIn("gender", intents)
        self.assertTrue(any("women, men, or kids" in q for q in questions))

    def test_traveling_activity_does_not_steal_explicit_place(self):
        query = "Travel-friendly coord for airport women"
        intents = normalize_intents(query)
        self.assertEqual(classify(intents, query).value, "place_profession")

    def test_cafe_brunch_routes_as_place_not_date_night(self):
        query = "Cafe brunch dress for women"
        intents = normalize_intents(query)
        self.assertEqual(intents["place"], "cafe")
        self.assertNotIn("occasion", intents)
        self.assertEqual(classify(intents, query).value, "place_profession")

    def test_accessory_and_temple_deictic_followups_are_followups(self):
        session = Session()
        session.add_turn(
            "Show lehengas for sangeet women",
            {"product_type": "lehenga", "occasion": "sangeet", "gender": "female"},
            "occasion",
        )
        conv = ConversationManager(session=session)
        self.assertTrue(conv._is_followup("What jewellery works with this?"))
        self.assertTrue(conv._is_followup("Can I wear this to a temple also?"))

    def test_unknown_recipient_gift_does_not_default_female(self):
        query = "Gift dress for colleague"
        intents = normalize_intents(query)
        self.assertEqual(intents["relation"], "colleague")
        self.assertTrue(intents["_is_gift"])
        self.assertNotIn("gender", intents)

    def test_deterministic_sql_does_not_add_gender_without_intent(self):
        sql = _deterministic_sql(
            "Comfortable music festival outfit for humid Mumbai",
            {"event": "music festival", "location": "Mumbai", "weather": "humid"},
            "Recommended products: top, dress\nAcceptable products: kurta, coord\nRecommended colours: all",
            ["topwear", "dress", "kurta", "coord"],
        )
        self.assertNotIn("gender IN", sql)

    def test_unresolved_wedding_guest_does_not_rank_bridal_red(self):
        sql = _deterministic_sql(
            "Affordable wedding guest outfit - don't want to spend too much",
            {"occasion": "wedding", "_needs_religion": True, "budget": "affordable"},
            "\n".join([
                "Recommended products: saree, kurta",
                "Acceptable products: dress",
                "Recommended colours: red, pink",
                "Avoid colours: white, black",
            ]),
            ["saree", "kurta", "dress"],
        )
        where, order_by = sql.split("ORDER BY", 1)
        self.assertIn("LIKE '%,red,%'", where)
        self.assertNotIn("red", order_by)

    def test_response_guardrails_include_wedding_guest_avoid_colors(self):
        conv = ConversationManager()
        outfit = OutfitResult(
            kg_context={"colour": {"avoid": ["white", "black"]}},
            color_palette={"palette": ["pink", "black", "gold"]},
        )
        avoid = conv._avoid_colours_for_response(
            "general wedding guest outfit women",
            outfit,
            {"occasion": "wedding", "gender": "female"},
        )
        self.assertTrue({"red", "white", "ivory", "black"}.issubset(avoid))

    def test_conversation_eval_checks_runtime_turn_contract(self):
        summary = {
            "workflow": "gifting",
            "is_followup": True,
            "needs_clarification": False,
            "clarifying_questions": [],
            "intents": {
                "occasion": "baby shower",
                "product_type": "dress",
                "gender": "female",
                "functional_needs": "breathable,quick-dry",
                "_is_gift": True,
            },
            "primary_product_count": 1,
            "primary_products": [{"product_type": "ethnic dresses"}],
            "db_product_count": 20,
            "sql": "SELECT * FROM products WHERE product_type IN ('dress', 'ethnic dresses')",
            "response_text": "Here are dresses.",
        }
        failures = check_expectations(summary, {
            "workflow": "gifting",
            "is_followup": True,
            "needs_clarification": False,
            "intent_equals": {
                "occasion": "baby shower",
                "product_type": "dress",
                "gender": "female",
            },
            "intent_truthy": ["_is_gift"],
            "intent_contains": {"functional_needs": ["breathable", "quick-dry"]},
            "min_primary_products": 1,
            "min_db_products": 1,
            "product_types_all": ["dress"],
            "sql_contains": ["product_type"],
        })
        self.assertEqual(failures, [])
        self.assertTrue(product_type_matches("scarf", "scarves & stoles"))

    def test_deterministic_sql_preserves_hard_constraints(self):
        kg = KnowledgeGraph(KG_PATH)
        intents = {
            "event": "Diwali",
            "product_type": "kurta",
            "price_max": 2000,
            "gender": "female",
        }
        kg_context = kg.format_context(kg.lookup(intents, gender="female"))
        available = get_available_types("kalki_products.db")
        sql = _deterministic_sql(
            "Kurta set under 2000 for Diwali women",
            intents,
            kg_context,
            available,
        )
        self.assertIn("product_type IN ('kurta')", sql)
        self.assertIn("price <= 2000.0", sql)

    def test_negated_colour_becomes_hard_avoidance(self):
        intents = normalize_intents("Show sarees for sangeet women not red")
        self.assertEqual(intents["avoid_colour"], "red")
        sql = _deterministic_sql(
            "Show sarees for sangeet women not red",
            intents,
            "Recommended products: saree\nRecommended colours: red, pink",
            get_available_types("aza_products.db"),
        )
        self.assertIn("NOT", sql)
        self.assertIn("red", sql)

    def test_deterministic_sql_uses_title_backed_broad_catalog_buckets(self):
        sql = _deterministic_sql(
            "Hindu wedding groom sherwani men",
            {"occasion": "wedding", "religion": "Hinduism", "gender": "male", "product_type": "sherwani"},
            "Recommended products: sherwani, kurta\nRecommended colours: all",
            get_available_types("kalki_products.db"),
        )
        self.assertIn("'men'", sql)
        self.assertIn("LOWER(title) LIKE '%sherwani%'", sql)

    def test_deterministic_sql_prioritizes_kidswear_for_child_queries(self):
        sql = _deterministic_sql(
            "Kids lehenga for Diwali girl",
            {"event": "Diwali", "agegroup": "child", "gender": "female", "product_type": "lehenga"},
            "Recommended products: lehenga, kurta\nRecommended colours: red, gold",
            get_available_types("kalki_products.db"),
        )
        self.assertIn("product_type IN ('kids', 'kidswear', 'lehenga')", sql)
        self.assertIn("gender IN ('kids', 'both')", sql)
        self.assertIn("LOWER(title) LIKE '%lehenga%'", sql)

    def test_deterministic_sql_excludes_kids_for_adult_women_queries(self):
        sql = _deterministic_sql(
            "coord set for office party women not too heavy",
            {"occasion": "office party", "gender": "female", "product_type": "coord", "functional_needs": "lightweight"},
            "Recommended products: coord, dress\nRecommended colours: all",
            get_available_types("aza_products.db"),
        )
        self.assertIn("gender IN ('female', 'both')", sql)
        self.assertNotIn("'kids'", sql)

    def test_aza_adapter_maps_child_audience_to_kids(self):
        adapter = object.__new__(AzaAdapter)
        base_product = {
            "id": "test",
            "title": "Test Product",
            "category": {"level1": "Westernwear", "level2": "Co-Ord Sets"},
            "color": ["Black"],
            "attributes": {},
            "selling_price": 1000,
            "image_url": [],
            "product_url": "/products/test/1",
        }

        girls_product = adapter._normalize({**base_product, "audience": ["Girls"]})
        boys_product = adapter._normalize({**base_product, "audience": ["Boys"]})
        women_product = adapter._normalize({**base_product, "audience": ["Women"]})
        men_product = adapter._normalize({**base_product, "audience": ["Men"]})

        self.assertEqual(girls_product.gender, "kids")
        self.assertEqual(boys_product.gender, "kids")
        self.assertEqual(women_product.gender, "female")
        self.assertEqual(men_product.gender, "male")

    def test_generate_sql_accepts_list_style_and_functional_intents(self):
        with patch(
            "db_query.call_llm",
            return_value="SELECT * FROM products WHERE product_type IN ('coord') AND gender IN ('female', 'both') LIMIT 20",
        ):
            sql, _, _ = generate_sql(
                "coord set for office party women not too heavy",
                {
                    "product_type": "coord",
                    "gender": "female",
                    "functional_needs": ["lightweight"],
                    "style_goals": ["classic"],
                },
                "Recommended products: coord\nRecommended colours: all",
                "aza",
                ["coord"],
            )
        self.assertIn("gender IN ('female', 'both')", sql)

    def test_conversation_eval_flags_kids_products_for_adult_queries(self):
        failures = check_expectations(
            {
                "workflow": "occasion",
                "needs_clarification": False,
                "is_followup": False,
                "intents": {"gender": "female", "product_type": "coord"},
                "primary_product_count": 1,
                "unique_primary_product_count": 1,
                "day_plan_count": 0,
                "db_product_count": 1,
                "primary_products": [
                    {
                        "id": "721090",
                        "title": "Ruffle Sleeve Top & Skirt Set",
                        "product_type": "coord",
                        "gender": "kids",
                    }
                ],
                "sql": "",
                "response_text": "",
                "clarifying_questions": [],
                "suggested_followups": [],
            },
            {},
        )
        self.assertTrue(any("audience mismatch" in failure for failure in failures))

    def test_deterministic_sql_uses_apparel_fallback_when_type_only_in_title(self):
        sql = _deterministic_sql(
            "Eid gift kaftan for mom under 20000",
            {"event": "Eid", "relation": "mom", "gender": "female", "product_type": "kaftan", "price_max": 20000},
            "Recommended products: kaftan, kurta\nRecommended colours: gold",
            get_available_types("aza_products.db"),
        )
        self.assertIn("LOWER(title) LIKE '%kaftan%'", sql)
        self.assertNotIn("'anklets'", sql)


if __name__ == "__main__":
    unittest.main()
