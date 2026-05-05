"""Knowledge graph loader and semantic lookup engine.

SuperSearch stores fashion knowledge as rows keyed by entity/value pairs such
as `(occasion, sangeet)` or `(bodytype, petite)`. This module loads that graph,
resolves aliases, merges recommendations/avoid rules, and formats the context
used by workflows and SQL generation.
"""

import openpyxl
from collections import defaultdict
from taxonomy import GENERIC_ENTITY_EXPANSIONS, INTENT_ALIASES


class KnowledgeGraph:
    def __init__(self, xlsx_path):
        self.graph = defaultdict(list)
        self.entity_values = defaultdict(set)
        self._load(xlsx_path)

    def _load(self, xlsx_path):
        wb = openpyxl.load_workbook(xlsx_path, read_only=True)
        ws = wb["graph"]
        for row in ws.iter_rows(min_row=2, values_only=True):
            entity, entity_value, category, tag, name, rank, gender = row
            key = (entity, entity_value)
            self.graph[key].append({
                "category": category,
                "tag": tag,
                "name": name,
                "rank": rank if rank else 0,
                "gender": gender,
            })
            self.entity_values[entity].add(entity_value)
        wb.close()

    def _resolve_alias(self, entity, value):
        aliases = INTENT_ALIASES.get(entity, {})
        if value in aliases:
            return aliases[value]
        value_lower = str(value).lower()
        for alias, resolved in aliases.items():
            if str(alias).lower() == value_lower:
                return resolved
        return value

    def lookup(self, intents, gender=None):
        """Look up the knowledge graph for a set of intents.

        Args:
            intents: dict of {entity: entity_value} e.g. {"place": "beach", "weather": "summer"}
            gender: optional gender filter ("male", "female")

        Returns:
            dict with recommended/acceptable/avoid items grouped by tag type
        """
        result, _trace = self.lookup_with_trace(intents, gender=gender)
        return result

    def lookup_with_trace(self, intents, gender=None):
        """Look up the graph and return both aggregate context and provenance."""
        all_items = defaultdict(lambda: defaultdict(list))
        trace = {
            "input_intents": dict(intents or {}),
            "gender_filter": gender,
            "lookup_keys": [],
            "matched_entries": [],
            "skipped_entries": [],
            "conflicts": [],
            "missing_keys": [],
        }

        for entity, value in intents.items():
            if str(entity).startswith("_"):
                continue
            if entity in ("location", "month", "budget", "time",
                         "price_max", "price_min", "product_type",
                         "avoid_product_type", "functional_needs", "style_goals",
                         "colour", "color"):
                continue

            resolved = self._resolve_alias(entity, value)
            lookup_values = [resolved]
            expansion = GENERIC_ENTITY_EXPANSIONS.get((entity, resolved))
            if expansion:
                lookup_values = expansion

            entries = []
            for lookup_value in lookup_values:
                key = (entity, lookup_value)
                trace["lookup_keys"].append({
                    "entity": entity,
                    "value": value,
                    "resolved_value": resolved,
                    "lookup_value": lookup_value,
                    "expanded": bool(expansion),
                    "matched_count": len(self.graph.get(key, [])),
                })
                if key not in self.graph:
                    trace["missing_keys"].append({
                        "entity": entity,
                        "value": value,
                        "resolved_value": resolved,
                        "lookup_value": lookup_value,
                    })
                entries.extend(self.graph.get(key, []))

            for entry in entries:
                if gender and entry["gender"] and entry["gender"] not in (gender, "both"):
                    trace["skipped_entries"].append({
                        "source": f"{entity}:{resolved}",
                        "category": entry["category"],
                        "tag": entry["tag"],
                        "name": entry["name"],
                        "rank": entry["rank"],
                        "gender": entry["gender"],
                        "reason": "gender_filter",
                    })
                    continue
                tag = entry["tag"]
                name = entry["name"]
                trace["matched_entries"].append({
                    "source": f"{entity}:{resolved}",
                    "category": entry["category"],
                    "tag": tag,
                    "name": name,
                    "rank": entry["rank"],
                    "gender": entry["gender"],
                })
                all_items[tag][name].append({
                    "rank": entry["rank"],
                    "source": f"{entity}:{resolved}",
                    "gender": entry["gender"],
                })

        result = {}
        for tag, names in all_items.items():
            recommended = []
            acceptable = []
            avoid = []
            for name, sources in names.items():
                ranks = [s["rank"] for s in sources]
                # When multiple intents contribute ranks for the same attribute,
                # an avoid (-1) from ANY intent overrides everything (safety first).
                # Otherwise take the best (max) rank — if one intent recommends it
                # and another is silent, it's still recommended.
                if -1 in ranks:
                    final_rank = -1
                else:
                    final_rank = max(ranks)
                if len(set(ranks)) > 1:
                    trace["conflicts"].append({
                        "tag": tag,
                        "name": name,
                        "ranks": ranks,
                        "sources": sources,
                        "final_rank": final_rank,
                    })

                if final_rank == 1:
                    recommended.append(name)
                elif final_rank == 2:
                    acceptable.append(name)
                elif final_rank == -1:
                    avoid.append(name)

            result[tag] = {
                "recommended": recommended,
                "acceptable": acceptable,
                "avoid": avoid,
            }

        trace["result_summary"] = result
        return result, trace

    def format_context(self, kg_result):
        """Format KG lookup results into a readable context string for the LLM."""
        lines = []

        priority_tags = ["product", "colour", "pattern", "material", "fit",
                         "sleeve", "neck", "length", "silhouette"]

        for tag in priority_tags:
            if tag not in kg_result:
                continue
            data = kg_result[tag]
            if data["recommended"]:
                lines.append(f"Recommended {tag}s: {', '.join(data['recommended'])}")
            if data["acceptable"]:
                lines.append(f"Acceptable {tag}s: {', '.join(data['acceptable'])}")
            if data["avoid"]:
                lines.append(f"Avoid {tag}s: {', '.join(data['avoid'])}")

        for tag, data in kg_result.items():
            if tag in priority_tags:
                continue
            if data["recommended"]:
                lines.append(f"Recommended {tag}: {', '.join(data['recommended'])}")
            if data["avoid"]:
                lines.append(f"Avoid {tag}: {', '.join(data['avoid'])}")

        return "\n".join(lines) if lines else "No specific knowledge graph context found for these intents."
