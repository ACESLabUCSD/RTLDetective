"""
Memory Knowledge Base Loader.

Loads and indexes documents from the memory/ directory so the debug agent
can retrieve relevant knowledge during the debugging process.
"""

import os
import re
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MEMORY_ROOT = Path(__file__).resolve().parent.parent / "memory"


@dataclass
class MemoryDocument:
    """A single knowledge base document."""
    name: str
    path: str
    category: str
    content: str
    metadata: dict = field(default_factory=dict)

@dataclass
class MemorySection:
    """A single section within a document."""
    doc_key: str
    heading: str
    content: str
    level: int



class MemoryLoader:
    """Loads and provides access to the knowledge base documents."""

    def __init__(self, memory_root=None):
        self.memory_root = Path(memory_root) if memory_root else MEMORY_ROOT
        self.documents: dict[str, MemoryDocument] = {}
        self._sections: dict[str, list[MemorySection]] = {}
        self._fix_patterns: list[dict] = []
        self._pattern_index: dict[str, list[dict]] = {}
        self._loaded = False

    def _index_fix_patterns(self):
        """Build a searchable index from fix_patterns.json structure."""
        doc = self.documents.get("common_patterns/fix_patterns.json")
        if not doc or not doc.metadata:
            return

        self._fix_patterns = doc.metadata.get("patterns", [])
        self._pattern_index = {}

        for pattern in self._fix_patterns:
            cat = pattern.get("category", "unknown")
            self._pattern_index.setdefault(cat, []).append(pattern)

            # Also index by bug code if present in name or trigger
            name = pattern.get("name", "")
            trigger = pattern.get("trigger", "")
            for field in (name, trigger):
                for code in re.findall(r'\b[A-Z]\d+\b', field):
                    self._pattern_index.setdefault(code, []).append(pattern)

        logger.debug(
            "Indexed %d fix patterns across categories: %s",
            len(self._fix_patterns),
            list(self._pattern_index.keys()),
        )

    def _index_sections(self):
        """Split every markdown doc into heading-delimited sections."""
        for key, doc in self.documents.items():
            if not doc.name.endswith(".md"):
                continue
            sections = []
            current_heading = None
            current_level = 0
            current_lines = []

            for line in doc.content.splitlines():
                match = re.match(r'^(#{1,3})\s+(.+)', line)
                if match:
                    if current_heading:
                        sections.append(MemorySection(
                            doc_key=key,
                            heading=current_heading,
                            content="\n".join(current_lines).strip(),
                            level=current_level,
                        ))
                    current_heading = match.group(2).strip()
                    current_level = len(match.group(1))
                    current_lines = []
                else:
                    current_lines.append(line)

            if current_heading:
                sections.append(MemorySection(
                    doc_key=key,
                    heading=current_heading,
                    content="\n".join(current_lines).strip(),
                    level=current_level,
                ))

            self._sections[key] = sections

    def get_sections(self, doc_key: str) -> list[MemorySection]:
        return self._sections.get(doc_key, [])

    def search_sections(self, query: str) -> list[MemorySection]:
        """Search at section level rather than document level."""
        query_lower = query.lower()
        results = []
        for sections in self._sections.values():
            for section in sections:
                if (query_lower in section.heading.lower() or
                        query_lower in section.content.lower()):
                    results.append(section)
        return results

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Split on non-word chars, lowercase, drop tokens shorter than 3 chars."""
        return {t for t in re.split(r"\W+", text.lower()) if len(t) >= 3}

    def search_sections_by_tokens(
        self,
        queries: list[str],
        min_overlap: int = 1,
        content_chars: int = 200,
    ) -> list[tuple[MemorySection, int]]:
        """Rank sections by token-overlap with the union of query tokens.

        For each section, compute the count of distinct query tokens appearing
        in the heading plus the first `content_chars` of its content. Sections
        with score >= min_overlap are returned sorted by score descending,
        breaking ties by shorter heading first (more specific match).

        Bug codes like 'F1'/'S6' are matched directly against the heading text
        as a fallback (since '_tokenize' drops tokens shorter than 3 chars).
        """
        query_tokens: set[str] = set()
        bug_codes: set[str] = set()
        for q in queries:
            if not q:
                continue
            query_tokens.update(self._tokenize(q))
            for code in re.findall(r"\b[A-Z]\d+\b", q):
                bug_codes.add(code)

        if not query_tokens and not bug_codes:
            return []

        scored: list[tuple[MemorySection, int]] = []
        for sections in self._sections.values():
            for section in sections:
                head_lower = section.heading.lower()
                body_lower = section.content[:content_chars].lower()
                blob_tokens = self._tokenize(section.heading + " " + section.content[:content_chars])
                score = len(query_tokens & blob_tokens)
                for code in bug_codes:
                    if code.lower() in head_lower or code.lower() in body_lower:
                        score += 1
                if score >= min_overlap:
                    scored.append((section, score))

        scored.sort(key=lambda pair: (-pair[1], len(pair[0].heading)))
        return scored

    def load_all(self) -> int:
        """Load all documents from the memory directory tree. Returns count loaded."""
        count = 0
        for dirpath, _, filenames in os.walk(self.memory_root):
            rel_dir = Path(dirpath).relative_to(self.memory_root)
            category = str(rel_dir).replace(os.sep, "/")
            if category == ".":
                category = "root"

            for fname in filenames:
                if fname.startswith("."):
                    continue
                fpath = Path(dirpath) / fname
                try:
                    content = fpath.read_text(encoding="utf-8")
                    doc = MemoryDocument(
                        name=fname,
                        path=str(fpath),
                        category=category,
                        content=content,
                    )
                    if fname.endswith(".json"):
                        try:
                            doc.metadata = json.loads(content)
                        except json.JSONDecodeError:
                            pass

                    key = f"{category}/{fname}"
                    self.documents[key] = doc
                    count += 1
                    logger.debug("Loaded: %s", key)
                except Exception as e:
                    logger.warning("Failed to load %s: %s", fpath, e)

        logger.info("Loaded %d memory documents from %s", count, self.memory_root)
        self._index_sections()
        self._index_fix_patterns()
        self._loaded = True
        return count

    def get(self, key: str) -> MemoryDocument | None:
        """Get a document by its key (category/filename)."""
        if not self._loaded:
            self.load_all()
        return self.documents.get(key)

    def get_by_category(self, category: str) -> list[MemoryDocument]:
        """Get all documents in a category."""
        if not self._loaded:
            self.load_all()
        return [doc for doc in self.documents.values() if doc.category == category]

    def search(self, query: str) -> list[MemoryDocument]:
        """Simple keyword search across all documents."""
        if not self._loaded:
            self.load_all()
        query_lower = query.lower()
        results = []
        for doc in self.documents.values():
            if query_lower in doc.content.lower() or query_lower in doc.name.lower():
                results.append(doc)
        return results

    def get_fix_patterns(self) -> list[dict]:
        """Load the fix patterns from the common_patterns JSON file."""
        doc = self.get("common_patterns/fix_patterns.json")
        if doc and doc.metadata:
            return doc.metadata.get("patterns", [])
        return []

    def get_bug_patterns_text(self) -> str:
        """Get the common bug patterns document as text."""
        doc = self.get("debugging_manuals/common_bug_patterns.md")
        return doc.content if doc else ""

    def get_templates_text(self) -> str:
        """Get module templates as text."""
        doc = self.get("hardware_specs/module_templates.md")
        return doc.content if doc else ""

    def get_language_reference(self) -> str:
        """Get the Verilog language reference."""
        doc = self.get("hardware_specs/verilog_language_reference.md")
        return doc.content if doc else ""

    def get_synthesis_guide(self) -> str:
        """Get synthesis constraints document."""
        doc = self.get("hardware_specs/synthesis_constraints.md")
        return doc.content if doc else ""

    def get_debug_methodology(self) -> str:
        """Get the debugging methodology document."""
        doc = self.get("debugging_manuals/debugging_methodology.md")
        return doc.content if doc else ""

    def get_index(self) -> str:
        """Get the memory index (root/index.md: what each document contains)."""
        doc = self.get("root/index.md")
        return doc.content if doc else ""

    def build_context_bundle(self) -> str:
        """
        Build a combined context string from all key documents,
        suitable for injecting into an LLM system prompt.
        """
        sections = []

        sections.append("=== MEMORY INDEX ===")
        sections.append(self.get_index())

        sections.append("\n=== VERILOG LANGUAGE REFERENCE ===")
        sections.append(self.get_language_reference())

        sections.append("\n=== SYNTHESIS CONSTRAINTS ===")
        sections.append(self.get_synthesis_guide())

        sections.append("\n=== COMMON BUG PATTERNS ===")
        sections.append(self.get_bug_patterns_text())

        sections.append("\n=== DEBUGGING METHODOLOGY ===")
        sections.append(self.get_debug_methodology())

        sections.append("\n=== CORRECT MODULE TEMPLATES ===")
        sections.append(self.get_templates_text())

        patterns = self.get_fix_patterns()
        if patterns:
            sections.append("\n=== FIX PATTERNS ===")
            for p in patterns:
                sections.append(f"\nPattern: {p['name']} ({p['category']})")
                sections.append(f"Trigger: {p['trigger']}")
                sections.append(f"Fix: {p['description']}")
                sections.append(f"Before:\n{p['example_before']}")
                sections.append(f"After:\n{p['example_after']}")

        return "\n".join(sections)

    def list_documents(self) -> list[str]:
        """List all loaded document keys."""
        if not self._loaded:
            self.load_all()
        return sorted(self.documents.keys())

    def pool_stats(self, exclude_keys: set[str] | None = None) -> dict:
        """Return totals for the dynamic-retrieval pool.

        Counts docs, markdown sections, and content bytes across the memory
        directory, excluding documents listed in `exclude_keys` (e.g. files
        already pinned in the system prompt). Useful as the denominator for
        per-iteration "X / Y loaded" reporting.
        """
        if not self._loaded:
            self.load_all()
        exclude = exclude_keys or set()
        total_docs = 0
        total_sections = 0
        total_bytes = 0
        for key, doc in self.documents.items():
            if key in exclude:
                continue
            total_docs += 1
            total_bytes += len(doc.content)
            total_sections += len(self._sections.get(key, []))
        return {
            "total_docs": total_docs,
            "total_sections": total_sections,
            "total_bytes": total_bytes,
        }
