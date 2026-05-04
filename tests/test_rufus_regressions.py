import unittest

from conversation import ConversationManager
from db_query import _deterministic_sql, get_available_types
from intent_extractor import _enrich_intents
from knowledge_graph import KnowledgeGraph
from session import Session
from config import KG_PATH


class RufusRegressionTests(unittest.TestCase):
    def test_budget_product_event_fallback(self):
        intents = _enrich_intents({}, "Kurta set under 2000 for Diwali women")
        self.assertEqual(intents["event"], "Diwali")
        self.assertEqual(intents["product_type"], "kurta")
        self.assertEqual(intents["price_max"], 2000)
        self.assertEqual(intents["gender"], "female")

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


if __name__ == "__main__":
    unittest.main()
