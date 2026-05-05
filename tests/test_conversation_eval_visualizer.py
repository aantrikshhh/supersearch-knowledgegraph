import json
import unittest
from html.parser import HTMLParser

from scripts.eval.conversation_eval_visualizer import render_html


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


if __name__ == "__main__":
    unittest.main()
