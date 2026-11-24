"""
build_memory_index.py

Parses all files in the memory/ directory and builds a structured
index.md that describes the contents of each file — suitable for
injection into an LLM prompt for dynamic memory retrieval decisions.

Usage:
    python build_memory_index.py                        # memory/ relative to script
    python build_memory_index.py --memory-root /path/to/memory
    python build_memory_index.py --output memory/index.md
"""

import argparse
import json
import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Parsers — one per file type / known file
# ---------------------------------------------------------------------------

def parse_markdown(content: str, filename: str) -> dict:
    topics = []
    keywords = set()
    current_heading = None
    current_level = 0
    current_body = []

    for line in content.splitlines():
        heading_match = re.match(r'^(#{1,3})\s+(.+)', line)
        if heading_match:
            if current_heading:
                body = " ".join(current_body[:3]).strip()
                topics.append({
                    "heading": current_heading,
                    "level": current_level,
                    "summary": body[:150] + "..." if len(body) > 150 else body,
                })
                codes = re.findall(r'\b[A-Z]\d+\b', current_heading + " " + body)
                keywords.update(codes)
                words = re.findall(r'\b[a-z]{4,}\b', (current_heading + " " + body).lower())
                keywords.update(words[:8])

            current_heading = heading_match.group(2).strip()
            current_level = len(heading_match.group(1))
            current_body = []
        elif current_heading and line.strip() and not line.startswith("```"):
            current_body.append(line.strip())

    if current_heading:
        body = " ".join(current_body[:3]).strip()
        topics.append({
            "heading": current_heading,
            "level": current_level,
            "summary": body[:150] + "..." if len(body) > 150 else body,
        })

    return {
        "type": "markdown",
        "topic_count": len(topics),
        "topics": topics,  # now full dicts with level + summary, not just names
        "keywords": sorted(keywords),
        "retrieve_when": _infer_retrieve_when_md(filename, topics),
    }


def parse_fix_patterns_json(content: str) -> dict:
    """
    Parse fix_patterns.json specifically — extract pattern names,
    categories, and trigger phrases.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {"type": "json", "error": "Could not parse JSON"}

    patterns = data.get("patterns", [])
    categories = sorted(set(p.get("category", "") for p in patterns))
    triggers = [p.get("trigger", "") for p in patterns]
    names = [p.get("name", "") for p in patterns]

    # Extract all bug codes mentioned in triggers/names
    all_text = " ".join(triggers + names)
    codes = sorted(set(re.findall(r'\b[A-Z]\d+\b', all_text)))

    return {
        "type": "json",
        "pattern_count": len(patterns),
        "categories": categories,
        "pattern_names": names,
        "triggers": triggers,
        "bug_codes_covered": codes,
        "description": (
            f"Structured before/after fix examples for {len(patterns)} bug patterns "
            f"across categories: {', '.join(categories)}."
        ),
        "retrieve_when": (
            "A specific bug category or pattern has been identified by the LLM or "
            "static analysis. Match against trigger phrases or category names. "
            f"Covers: {', '.join(names[:6])}{'...' if len(names) > 6 else ''}."
        ),
    }


def parse_generic_json(content: str, filename: str) -> dict:
    """Fallback parser for unknown JSON files."""
    try:
        data = json.loads(content)
        keys = list(data.keys()) if isinstance(data, dict) else []
        return {
            "type": "json",
            "top_level_keys": keys,
            "description": f"JSON file with keys: {', '.join(keys)}",
            "retrieve_when": "Unknown — review file contents manually.",
        }
    except json.JSONDecodeError:
        return {"type": "json", "error": "Could not parse JSON"}


def _infer_retrieve_when_md(filename: str, topics: list[dict]) -> str:
    """Heuristic: infer retrieve_when from filename and topic headings."""
    fn = filename.lower()
    headings_text = " ".join(t["heading"].lower() for t in topics)

    if "bug_pattern" in fn or "bug pattern" in headings_text:
        return (
            "Compiler errors, simulation failures, latch warnings, "
            "blocking/non-blocking confusion, reset polarity bugs, port mismatches."
        )
    if "methodology" in fn or "debugging" in fn:
        return (
            "Always include on iteration 1. Include on later iterations "
            "when the same bug persists across multiple rounds."
        )
    if "language_reference" in fn or "language reference" in headings_text:
        return (
            "Type errors, wire/reg assignment issues, sensitivity list warnings, "
            "operator or precedence issues, bit-width mismatches."
        )
    if "template" in fn:
        return (
            "Module structure looks wrong, instantiation errors (I1-I4), "
            "FSM or counter behavior incorrect, need correct template to compare against."
        )
    if "synthesis" in fn or "constraint" in fn:
        return (
            "Synthesis warnings, timing failures, latch inferred, "
            "combinational loop detected, clock constraint issues."
        )
    if "index" in fn:
        return "Use to decide which other documents to retrieve. Read first."

    # Generic fallback: use first few topic headings
    sample = ", ".join(t["heading"] for t in topics[:4])
    return f"Topics include: {sample}."


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def parse_file(path: Path) -> dict:
    """Dispatch to the right parser based on filename/extension."""
    content = path.read_text(encoding="utf-8")
    fname = path.name.lower()

    if fname == "fix_patterns.json":
        return parse_fix_patterns_json(content)
    elif path.suffix == ".json":
        return parse_generic_json(content, fname)
    elif path.suffix in (".md", ".txt", ".rst"):
        return parse_markdown(content, fname)
    else:
        # Try markdown-style parse as fallback
        return parse_markdown(content, fname)


def build_index(memory_root: Path) -> dict:
    """Walk the memory directory and parse every file."""
    index = {}

    for dirpath, _, filenames in os.walk(memory_root):
        rel_dir = Path(dirpath).relative_to(memory_root)
        category = str(rel_dir).replace(os.sep, "/")
        if category == ".":
            category = "root"

        for fname in sorted(filenames):
            if fname.startswith("."):
                continue
            fpath = Path(dirpath) / fname
            key = f"{category}/{fname}"

            print(f"  Parsing: {key}")
            try:
                parsed = parse_file(fpath)
                parsed["key"] = key
                parsed["filename"] = fname
                parsed["category"] = category
                index[key] = parsed
            except Exception as e:
                print(f"  WARNING: Failed to parse {key}: {e}")
                index[key] = {"key": key, "error": str(e)}

    return index


def render_index_md(index: dict, memory_root: Path) -> str:
    lines = [
        "# Memory Knowledge Base Index",
        "",
        "Use this index to decide which documents to retrieve for a given debugging problem.",
        "Request documents by their exact key (shown in each entry header).",
        "",
        "---",
        "",
    ]

    by_category: dict[str, list[dict]] = {}
    for entry in index.values():
        cat = entry.get("category", "root")
        by_category.setdefault(cat, []).append(entry)

    for category in sorted(by_category.keys()):
        lines.append(f"## {category}/")
        lines.append("")

        for entry in sorted(by_category[category], key=lambda e: e.get("filename", "")):
            key = entry.get("key", "")

            lines.append(f"### `{key}`")

            if "error" in entry:
                lines.append(f"**Error parsing file:** {entry['error']}")
                lines.append("")
                continue

            desc = entry.get("description", "")
            if desc:
                lines.append(f"**Contains:** {desc}")

            if entry.get("type") == "markdown":
                topics = entry.get("topics", [])
                keywords = entry.get("keywords", [])

                # Separate top-level sections from detail entries
                sections = [t for t in topics if t["level"] <= 2]
                details  = [t for t in topics if t["level"] >= 3]

                if sections:
                    lines.append(f"**Sections:** {', '.join(t['heading'] for t in sections)}")

                # List every detail entry with its summary (the S1-S9, F1-F10 etc.)
                if details:
                    lines.append("**Entries:**")
                    for t in details:
                        summary = f" — {t['summary']}" if t['summary'] else ""
                        lines.append(f"  - **{t['heading']}**{summary}")

                if keywords:
                    lines.append(f"**Keywords:** {', '.join(keywords[:20])}")

            elif entry.get("type") == "json":
                if "pattern_count" in entry:
                    lines.append(f"**Patterns:** {entry['pattern_count']} fix patterns")
                    lines.append(f"**Categories:** {', '.join(entry.get('categories', []))}")
                    triggers = entry.get("triggers", [])
                    if triggers:
                        lines.append("**Trigger phrases:**")
                        for t in triggers:
                            lines.append(f"  - {t}")

            retrieve_when = entry.get("retrieve_when", "")
            if retrieve_when:
                lines.append(f"**Retrieve when:** {retrieve_when}")

            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build a memory index from the memory/ directory.",
    )
    parser.add_argument(
        "--memory-root", "-m",
        default=None,
        help="Path to memory/ directory (default: memory/ relative to this script)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output path for index.md (default: <memory-root>/index.md)",
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Also write a compact index.json alongside index.md",
    )
    parser.add_argument(
        "--print", "-p",
        action="store_true",
        help="Print the generated index to stdout",
    )
    args = parser.parse_args()

    # Resolve memory root
    script_dir = Path(__file__).resolve().parent
    memory_root = Path(args.memory_root) if args.memory_root else script_dir.parent / "memory"

    if not memory_root.exists():
        print(f"ERROR: memory root not found: {memory_root}")
        raise SystemExit(1)

    print(f"Building memory index from: {memory_root}")

    # Parse all files
    index = build_index(memory_root)
    print(f"\nParsed {len(index)} files.")

    # Render markdown
    md_content = render_index_md(index, memory_root)

    # Write markdown output
    out_path = Path(args.output) if args.output else memory_root / "index.md"
    out_path.write_text(md_content, encoding="utf-8")
    print(f"Written: {out_path}")

    # Optionally write JSON
    if args.json:
        json_path = out_path.with_suffix(".json")
        json_path.write_text(render_index_json(index), encoding="utf-8")
        print(f"Written: {json_path}")

    if args.print:
        print("\n" + "=" * 60)
        print(md_content)


if __name__ == "__main__":
    main()