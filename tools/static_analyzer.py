"""
Verilog Static Analyzer.

Performs regex-based and structural lint checks on Verilog source files.
Detects common bug patterns without requiring simulation or synthesis tools.

Usage:
    python tools/static_analyzer.py <file.v> [--json]
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class Finding:
    line: int
    severity: str
    category: str
    message: str
    code_snippet: str


class VerilogStaticAnalyzer:
    """Regex and structural analysis for common Verilog bugs."""

    def __init__(self, source: str, filename: str = "<stdin>"):
        self.source = source
        self.filename = filename
        self.lines = source.split("\n")
        self.findings: list[Finding] = []
        self._clean = self._make_clean_source(source)

    @staticmethod
    def _make_clean_source(source: str) -> str:
        """Replace comments with spaces, preserving character positions and newlines."""
        def _space_preserving(m: re.Match) -> str:
            return re.sub(r"[^\n]", " ", m.group(0))
        result = re.sub(r"//[^\n]*", _space_preserving, source)
        result = re.sub(r"/\*.*?\*/", _space_preserving, result, flags=re.DOTALL)
        return result

    def analyze(self) -> list[Finding]:
        """Run all checks and return findings."""
        self.findings = []
        self._check_blocking_in_sequential()
        self._check_nonblocking_in_combinational()
        self._check_incomplete_sensitivity()
        self._check_missing_case_default()
        self._check_multiple_drivers()
        self._check_width_mismatches()
        self._check_missing_posedge()
        self._check_latch_risk()
        return self.findings

    def _add(self, line: int, severity: str, category: str, message: str):
        snippet = self.lines[line - 1].strip() if 0 < line <= len(self.lines) else ""
        self.findings.append(Finding(line, severity, category, message, snippet))

    def _find_always_blocks(self) -> list[dict]:
        """Parse out always blocks with their sensitivity and body ranges."""
        blocks = []
        pattern = re.compile(
            r"always\s*@\s*\(([^)]*)\)\s*(begin)?",
            re.IGNORECASE,
        )
        for match in pattern.finditer(self._clean):
            sensitivity = match.group(1).strip()
            start_pos = match.start()
            start_line = self._clean[:start_pos].count("\n") + 1

            is_sequential = bool(re.search(r"\b(pos|neg)edge\b", sensitivity))
            is_star = sensitivity.strip() == "*"

            body_start = match.end()
            if match.group(2):  # has 'begin'
                depth = 1
                pos = body_start
                while pos < len(self._clean) and depth > 0:
                    if self._clean[pos:pos+5] == "begin":
                        depth += 1
                    elif self._clean[pos:pos+3] == "end" and (
                        pos + 3 >= len(self._clean) or not self._clean[pos+3].isalnum()
                    ):
                        depth -= 1
                    pos += 1
                body_end = pos
            else:
                next_semi = self._clean.find(";", body_start)
                body_end = next_semi + 1 if next_semi != -1 else len(self._clean)

            body = self._clean[body_start:body_end]

            blocks.append({
                "sensitivity": sensitivity,
                "is_sequential": is_sequential,
                "is_star": is_star,
                "start_line": start_line,
                "body": body,
                "body_start": body_start,
                "body_end": body_end,
            })
        return blocks

    def _check_blocking_in_sequential(self):
        """Detect blocking assignments (=) in edge-triggered always blocks."""
        for block in self._find_always_blocks():
            if not block["is_sequential"]:
                continue
            for m in re.finditer(r"(\w+)\s*(?<![<>=!])=(?!=)\s*", block["body"]):
                lhs = m.group(1)
                if lhs in ("if", "else", "case", "begin", "end", "for", "while"):
                    continue
                abs_pos = block["body_start"] + m.start()
                line = self.source[:abs_pos].count("\n") + 1
                self._add(
                    line, "critical", "blocking_nonblocking",
                    f"Blocking assignment to '{lhs}' in sequential always block. "
                    "Use '<=' for non-blocking assignment.",
                )

    def _check_nonblocking_in_combinational(self):
        """Detect non-blocking assignments (<=) in combinational always blocks."""
        for block in self._find_always_blocks():
            if block["is_sequential"]:
                continue
            for m in re.finditer(r"(\w+)\s*<=\s*", block["body"]):
                lhs = m.group(1)
                if lhs in ("if", "else", "case", "begin", "end"):
                    continue
                abs_pos = block["body_start"] + m.start()
                line = self.source[:abs_pos].count("\n") + 1
                self._add(
                    line, "major", "blocking_nonblocking",
                    f"Non-blocking assignment to '{lhs}' in combinational always block. "
                    "Use '=' for blocking assignment.",
                )

    def _check_incomplete_sensitivity(self):
        """Detect manually-specified sensitivity lists that may be incomplete."""
        for block in self._find_always_blocks():
            if block["is_sequential"] or block["is_star"]:
                continue
            sensitivity = block["sensitivity"]
            sens_signals = set(re.findall(r"\b(\w+)\b", sensitivity))
            sens_signals -= {"or", "posedge", "negedge"}

            rhs_signals = set()
            for m in re.finditer(r"\b(\w+)\b", block["body"]):
                rhs_signals.add(m.group(1))
            rhs_signals -= {
                "begin", "end", "if", "else", "case", "endcase", "default",
                "assign", "wire", "reg", "input", "output",
            }

            body_lhs = set()
            for m in re.finditer(r"(\w+)\s*(?:<=|(?<![<>=!])=(?!=))", block["body"]):
                body_lhs.add(m.group(1))
            rhs_only = rhs_signals - body_lhs

            missing = rhs_only - sens_signals
            missing = {
                s for s in missing
                if not s.isdigit()
                and len(s) > 1
                and not re.match(r"^[bodh]\d*$", s)  # filter Verilog literal fragments
                and not re.match(r"^\d+$", s)
            }

            if missing:
                self._add(
                    block["start_line"], "major", "sensitivity_list",
                    f"Incomplete sensitivity list. Potentially missing: {', '.join(sorted(missing))}. "
                    "Consider using 'always @(*)'.",
                )

    def _check_missing_case_default(self):
        """Detect case statements without a default branch."""
        case_pattern = re.compile(r"\bcase[zx]?\s*\(", re.IGNORECASE)
        for m in case_pattern.finditer(self._clean):
            start = m.start()
            line = self._clean[:start].count("\n") + 1
            endcase_pos = self._clean.find("endcase", start)
            if endcase_pos == -1:
                continue
            case_body = self._clean[start:endcase_pos]
            if "default" not in case_body:
                self._add(
                    line, "major", "incomplete_case",
                    "Case statement without 'default' branch. May infer a latch.",
                )

    def _check_multiple_drivers(self):
        """Detect signals driven by multiple always blocks."""
        blocks = self._find_always_blocks()
        signal_drivers: dict[str, list[int]] = {}

        for block in blocks:
            for m in re.finditer(r"(\w+)\s*(?:<=|(?<![<>=!])=(?!=))", block["body"]):
                sig = m.group(1)
                if sig in ("if", "else", "case", "begin", "end"):
                    continue
                if sig not in signal_drivers:
                    signal_drivers[sig] = []
                if block["start_line"] not in signal_drivers[sig]:
                    signal_drivers[sig].append(block["start_line"])

        for sig, drivers in signal_drivers.items():
            if len(drivers) > 1:
                self._add(
                    drivers[0], "critical", "multiple_drivers",
                    f"Signal '{sig}' driven by multiple always blocks "
                    f"(lines: {', '.join(map(str, drivers))}). "
                    "Consolidate into a single always block.",
                )

    def _check_width_mismatches(self):
        """Detect obvious width mismatches in parameter and signal declarations."""
        param_widths: dict[str, int] = {}
        for m in re.finditer(r"parameter\s+(\w+)\s*=\s*(\d+)'[bodh]", self._clean):
            param_widths[m.group(1)] = int(m.group(2))

        reg_widths: dict[str, int] = {}
        for m in re.finditer(r"reg\s+\[(\d+):(\d+)\]\s+(\w+)", self._clean):
            high, low = int(m.group(1)), int(m.group(2))
            reg_widths[m.group(3)] = high - low + 1

        wire_widths: dict[str, int] = {}
        for m in re.finditer(r"wire\s+\[(\d+):(\d+)\]\s+(\w+)", self._clean):
            high, low = int(m.group(1)), int(m.group(2))
            wire_widths[m.group(3)] = high - low + 1

        for m in re.finditer(r"assign\s+(\w+)\s*=\s*(\w+)\s*;", self._clean):
            lhs, rhs = m.group(1), m.group(2)
            lhs_w = wire_widths.get(lhs, reg_widths.get(lhs))
            rhs_w = wire_widths.get(rhs, reg_widths.get(rhs))
            if lhs_w and rhs_w and lhs_w != rhs_w:
                line = self._clean[:m.start()].count("\n") + 1
                self._add(
                    line, "warning", "width_mismatch",
                    f"Width mismatch: '{lhs}' is {lhs_w}-bit but '{rhs}' is {rhs_w}-bit. "
                    "Implicit truncation or zero-extension.",
                )

    def _check_missing_posedge(self):
        """Detect always blocks that reference clk/rst without posedge/negedge."""
        pattern = re.compile(r"always\s*@\s*\(([^)]*)\)", re.IGNORECASE)
        for m in pattern.finditer(self._clean):
            sens = m.group(1)
            line = self._clean[:m.start()].count("\n") + 1
            if re.search(r"\b(clk|clock|rst|reset)\b", sens, re.IGNORECASE):
                if not re.search(r"\b(pos|neg)edge\b", sens, re.IGNORECASE):
                    self._add(
                        line, "critical", "sensitivity_list",
                        "Clock/reset signal in sensitivity list without posedge/negedge. "
                        "This creates level-sensitive (combinational) behavior.",
                    )

    def _check_latch_risk(self):
        """Detect combinational always blocks with if-without-else (latch risk)."""
        for block in self._find_always_blocks():
            if block["is_sequential"]:
                continue
            body = block["body"]
            if_count = len(re.findall(r"\bif\b", body))
            else_count = len(re.findall(r"\belse\b", body))
            if if_count > 0 and else_count < if_count:
                has_default_assign = bool(re.match(
                    r"\s*\w+\s*=\s*", body.lstrip()
                ))
                if not has_default_assign:
                    self._add(
                        block["start_line"], "major", "latch_inference",
                        "Combinational always block has 'if' without matching 'else' "
                        "and no default pre-assignment. May infer a latch.",
                    )


def main():
    parser = argparse.ArgumentParser(description="Verilog Static Analyzer")
    parser.add_argument("file", help="Verilog source file to analyze")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    source = path.read_text(encoding="utf-8")
    analyzer = VerilogStaticAnalyzer(source, str(path))
    findings = analyzer.analyze()

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        if not findings:
            print(f"[OK] No issues found in {path.name}")
        else:
            print(f"[ANALYSIS] {path.name} — {len(findings)} finding(s)\n")
            for f in sorted(findings, key=lambda x: x.line):
                print(f"  Line {f.line:4d} [{f.severity:8s}] ({f.category})")
                print(f"           {f.message}")
                if f.code_snippet:
                    print(f"           >>> {f.code_snippet}")
                print()

    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
