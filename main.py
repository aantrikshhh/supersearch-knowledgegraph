"""CLI entry point — interactive conversation or single-query mode.

Usage:
    python3 main.py                              # Interactive mode (default brand: aza)
    python3 main.py -b masaba                    # Interactive with Masaba
    python3 main.py -q "What to wear to sangeet?" -b kalki   # Single query
    python3 main.py --trace -q "outfit for beach"             # Single query with debug trace
"""

import argparse
import json
import sys
from conversation import ConversationManager
from session import Session


def run_interactive(brand):
    """Multi-turn interactive REPL."""
    session = Session(brand=brand)
    conv = ConversationManager(session=session, brand=brand)

    print(f"\n{'='*60}")
    print(f"  Fashion Assistant — {brand.upper()}")
    print(f"  Type your query, or 'quit' to exit")
    print(f"{'='*60}\n")

    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not query:
            continue
        if query.lower() in ("quit", "exit", "bye", "q"):
            print("Goodbye!")
            break

        print("  Thinking...\n")
        try:
            result = conv.process(query)
            print(f"Assistant: {result['response_text']}\n")

            # Show debug info if verbose
            if "--debug" in sys.argv:
                print(f"  [workflow: {result['workflow']}]")
                print(f"  [intents: {result['intents']}]")
                print(f"  [products: {len(result['outfit'].primary_products)}]")
                print(f"  [shoes: {result['outfit'].shoes}]")
                print(f"  [bags: {result['outfit'].bags}]")
                print(f"  [jewellery: {result['outfit'].jewellery}]")
                print()

        except Exception as e:
            print(f"  Sorry, something went wrong: {e}\n")

    # Save session
    session.save()
    print(f"Session saved: sessions/{session.session_id}.json")


def run_single(query, brand, trace=False):
    """Single query mode."""
    conv = ConversationManager(brand=brand)
    result = conv.process(query)

    if trace:
        print(f"\nWorkflow: {result['workflow']}")
        print(f"Intents: {json.dumps(result['intents'], indent=2)}")
        print(f"Follow-up: {result['is_followup']}")
        outfit = result["outfit"]
        print(f"\nProducts ({len(outfit.primary_products)}):")
        for p in outfit.primary_products[:5]:
            print(f"  [{p.get('product_type','')}] {p.get('title','')}")
            print(f"    colors={p.get('colors','')} materials={p.get('materials','')} price={p.get('price','')}")
        print(f"\nAccessories:")
        print(f"  Shoes: {outfit.shoes}")
        print(f"  Bags: {outfit.bags}")
        print(f"  Jewellery: {outfit.jewellery}")
        print(f"\nColor palette: {outfit.color_palette.get('palette', [])}")
        print(f"Styling notes: {outfit.styling_notes}")
        if outfit.db_debug:
            print("\nDB / SQL:")
            if "days" in outfit.db_debug:
                for idx, day_debug in enumerate(outfit.db_debug.get("days", [])[:3], 1):
                    print(f"  Day {idx}: products={day_debug.get('product_count', 0)} errors={day_debug.get('errors', [])[:1]}")
                    if day_debug.get("sql"):
                        print(f"    SQL: {day_debug['sql'].splitlines()[0][:140]}")
            else:
                print(f"  Products: {outfit.db_debug.get('product_count', 0)}")
                print(f"  Timings: {outfit.db_debug.get('timings', {})}")
                if outfit.db_debug.get("errors"):
                    short_errors = [e[:300] for e in outfit.db_debug["errors"]]
                    print(f"  Errors: {short_errors}")
                if outfit.db_debug.get("sql"):
                    print("  SQL:")
                    print(outfit.db_debug["sql"])
        print(f"\n{'─'*60}")

    print(f"\n{result['response_text']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fashion Recommendation Assistant")
    parser.add_argument("-q", "--query", help="Single query mode")
    parser.add_argument("-b", "--brand", default="aza", choices=["masaba", "kalki", "aza"])
    parser.add_argument("--trace", action="store_true", help="Show debug trace")
    parser.add_argument("--debug", action="store_true", help="Show debug info in interactive mode")
    args = parser.parse_args()

    if args.query:
        run_single(args.query, args.brand, trace=args.trace)
    else:
        run_interactive(args.brand)
