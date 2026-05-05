import json
import unittest
from html.parser import HTMLParser

from scripts.eval.conversation_eval_visualizer import compact_product, normalize_image_src, render_html


class PayloadScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_payload = False
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "payload":
            self.in_payload = True

    def handle_endtag(self, tag):
        if tag == "script" and self.in_payload:
            self.in_payload = False

    def handle_data(self, data):
        if self.in_payload:
            self.text.append(data)


class ConversationEvalVisualizerTests(unittest.TestCase):
    def test_payload_script_contains_parseable_json(self):
        payload = {
            "summary": {"scenarios": 1, "turns": 1},
            "scenarios": [],
            "turns": [
                {
                    "user": "gift <dress> & \"shawl\" </script>",
                    "sql": "SELECT * FROM products WHERE title LIKE '%dress%'",
                }
            ],
        }

        html = render_html(payload, "Visualizer")
        parser = PayloadScriptParser()
        parser.feed(html)

        self.assertIn('id="queryList"', html)
        self.assertIn('id="chatPane"', html)
        self.assertIn('id="tracePane"', html)
        raw_payload = "".join(parser.text)
        self.assertNotIn("&quot;", raw_payload)
        parsed = json.loads(raw_payload)
        self.assertEqual(parsed["turns"][0]["user"], payload["turns"][0]["user"])

    def test_product_media_prefers_enriched_image_fields(self):
        product = {"id": "123", "title": "Test Kurta"}
        media = {
            ("kalki", "123"): {
                "image_url": "file:///tmp/test.jpg",
                "url": "https://www.kalkifashion.com/products/test-kurta",
                "image_source": "/Users/aant/repos/scraper-infra/data/pdp_images/kalki_fashion/test/001.jpg",
                "image_source_type": "local_file",
                "remote_image_url": "https://cdn.shopify.com/test.jpg",
            }
        }

        compact = compact_product(product, media=media, brand="kalki")

        self.assertEqual(compact["image_url"], "file:///tmp/test.jpg")
        self.assertEqual(compact["image_source_type"], "local_file")
        self.assertEqual(compact["remote_image_url"], "https://cdn.shopify.com/test.jpg")

    def test_normalize_image_src_accepts_catalog_lists(self):
        self.assertEqual(normalize_image_src(["", "https://example.com/a.jpg"]), "https://example.com/a.jpg")


if __name__ == "__main__":
    unittest.main()
