"""Mine conversation eval artifacts into actionable product/engineering insights."""

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


FAILURE_BUCKETS = [
    ("workflow", "workflow"),
    ("needs_clarification", "workflow"),
    ("clarifying", "workflow"),
    ("is_followup", "conversation_memory"),
    ("intent", "prompt_or_config"),
    ("SQL", "sql_retrieval"),
    ("DB products", "catalog_or_retrieval"),
    ("primary products", "catalog_or_retrieval"),
    ("product type", "ranking_or_taxonomy"),
    ("response", "response_prompt"),
]


def load(path):
    with open(path) as f:
        return json.load(f)


def bucket_failure(text):
    for needle, bucket in FAILURE_BUCKETS:
        if needle.lower() in text.lower():
            return bucket
    return "unknown"


def iter_turns(result):
    for scenario in result.get("results", []):
        for idx, turn in enumerate(scenario.get("turns", []), 1):
            yield scenario, idx, turn


def summarize(result):
    summary = result.get("summary", {})
    counts = {
        "scenarios": summary.get("scenarios", 0),
        "passed_scenarios": summary.get("passed_scenarios", 0),
        "failed_scenarios": summary.get("failed_scenarios", 0),
        "turns": summary.get("turns", 0),
        "failed_turns": summary.get("failed_turns", 0),
    }
    category_counts = Counter()
    workflow_counts = Counter()
    catalog_counts = Counter()
    failure_buckets = Counter()
    judge_classifications = Counter()
    examples = defaultdict(list)
    catalog_gap_examples = []

    for scenario, idx, turn in iter_turns(result):
        category_counts[scenario.get("category", "unknown")] += 1
        workflow_counts[turn.get("workflow", "unknown")] += 1
        catalog_label = turn.get("catalog_status", {}).get("label", "unknown")
        catalog_counts[catalog_label] += 1
        if catalog_label == "catalog_gap_expected" and len(catalog_gap_examples) < 12:
            catalog_gap_examples.append({
                "scenario_id": scenario.get("id"),
                "turn": idx,
                "user": turn.get("user"),
                "reason": turn.get("catalog_status", {}).get("reason"),
            })

        for failure in turn.get("failures", []):
            bucket = bucket_failure(failure)
            failure_buckets[bucket] += 1
            if len(examples[bucket]) < 10:
                examples[bucket].append({
                    "scenario_id": scenario.get("id"),
                    "turn": idx,
                    "user": turn.get("user"),
                    "failure": failure,
                    "catalog_status": catalog_label,
                    "workflow": turn.get("workflow"),
                })

        for judge_name, judge in turn.get("judges", {}).items():
            classification = judge.get("classification", "unknown")
            judge_classifications[(judge_name, classification)] += 1
            if classification != "ok" and len(examples[f"judge:{classification}"]) < 10:
                examples[f"judge:{classification}"].append({
                    "scenario_id": scenario.get("id"),
                    "turn": idx,
                    "user": turn.get("user"),
                    "judge": judge_name,
                    "score": judge.get("score"),
                    "labels": judge.get("labels", []),
                    "rationale": judge.get("rationale"),
                })

    return {
        "counts": counts,
        "category_counts": dict(category_counts),
        "workflow_counts": dict(workflow_counts),
        "catalog_status_counts": dict(catalog_counts),
        "failure_buckets": dict(failure_buckets),
        "judge_classifications": {
            f"{judge}:{classification}": count
            for (judge, classification), count in judge_classifications.items()
        },
        "examples": dict(examples),
        "catalog_gap_examples": catalog_gap_examples,
    }


def render_markdown(artifact_path, mined, extra_notes=None):
    counts = mined["counts"]
    lines = [
        "# Aza 500 Conversation Eval Mining Report",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Artifact: `{artifact_path}`",
        "",
        "## Summary",
        "",
        f"- Scenarios: {counts['scenarios']}",
        f"- Passed scenarios: {counts['passed_scenarios']}",
        f"- Failed scenarios: {counts['failed_scenarios']}",
        f"- Turns: {counts['turns']}",
        f"- Failed turns: {counts['failed_turns']}",
        "",
        "## Grounding Sources",
        "",
        "- Workflow spec: `docs/specs/Workflows.docx`",
        "- Aza official category/wedding/menswear pages",
        "- Google Conversational Commerce UX/testing guidance",
        "- Baymard ecommerce search/filter UX guidance",
        "- Microsoft RAG groundedness/relevance/retrieval evaluator concepts",
        "",
        "## Flow Mix",
        "",
    ]
    for key, value in sorted(mined["category_counts"].items()):
        lines.append(f"- {key}: {value} turns")

    lines.extend(["", "## Catalog Coverage Classification", ""])
    for key, value in sorted(mined["catalog_status_counts"].items()):
        lines.append(f"- {key}: {value}")

    if mined["catalog_gap_examples"]:
        lines.extend(["", "Catalog gaps documented as non-system failures:"])
        for ex in mined["catalog_gap_examples"]:
            lines.append(
                f"- `{ex['scenario_id']}` turn {ex['turn']}: {ex['user']} ({ex['reason']})"
            )

    lines.extend(["", "## Deterministic Failure Buckets", ""])
    if mined["failure_buckets"]:
        for key, value in sorted(mined["failure_buckets"].items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- None")

    lines.extend(["", "## Judge Classifications", ""])
    if mined["judge_classifications"]:
        for key, value in sorted(mined["judge_classifications"].items(), key=lambda x: (-x[1], x[0])):
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- No judge outputs attached in this artifact.")

    lines.extend(["", "## Example Clusters", ""])
    if mined["examples"]:
        for bucket, examples in sorted(mined["examples"].items()):
            lines.append(f"### {bucket}")
            for ex in examples[:6]:
                detail = ex.get("failure") or ex.get("rationale") or ""
                lines.append(f"- `{ex['scenario_id']}` turn {ex['turn']}: {ex['user']} -> {detail}")
            lines.append("")
    else:
        lines.append("- No failures or non-ok judge classifications.")

    lines.extend([
        "## Change Log",
        "",
        "This section should be completed after fixes are made in the same run.",
    ])
    if extra_notes:
        lines.extend(["", extra_notes])
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--markdown-out", default=None)
    args = parser.parse_args()

    artifact = Path(args.artifact)
    result = load(artifact)
    mined = summarize(result)

    json_out = Path(args.json_out) if args.json_out else artifact.with_name(artifact.stem + "_mined.json")
    with json_out.open("w") as f:
        json.dump(mined, f, indent=2)
    print(f"Wrote {json_out}")

    markdown_out = Path(args.markdown_out) if args.markdown_out else artifact.with_name(artifact.stem + "_insights.md")
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    with markdown_out.open("w") as f:
        f.write(render_markdown(str(artifact), mined))
    print(f"Wrote {markdown_out}")


if __name__ == "__main__":
    main()
