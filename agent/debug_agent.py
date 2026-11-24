"""
Verilog Debug Agent — Multi-Loop Agentic Debugger.

Orchestrates an iterative debugging pipeline:
  1. Load buggy Verilog source
  2. Load knowledge base into context
  3. Run static analysis + testbench (if available)
  4. Send to LLM for hypothesis generation, evidence analysis, and fix proposal
  5. Validate the proposed fix via:
     a. Static analysis (no new warnings)
     b. Testbench simulation (all assertions pass)  ← primary metric
  6. If issues remain, loop back — feed the validation results to the LLM
     for a follow-up refinement pass (up to --max-loops iterations)
  7. Produce a structured report with testbench pass rate

Supports both OpenRouter (remote) and local HuggingFace models.
"""

import argparse
import json
import logging
import re
import subprocess
import sys
import textwrap
import statistics
import time
import csv
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
import os


from agent.backends.llm_client import (
    create_client,
    resolve_model_name,
    ALL_MODELS,
    BACKENDS,
    DEFAULT_BACKEND,
)
from agent.memory_loader import MemoryLoader
from agent.verilog_tracer import VerilogTracer
from agent.waveform import waveform_context as build_waveform_context
from agent.predict_force import localize as predict_force_localize, load_settings as localizer_settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_PATH = PROJECT_ROOT / "skills" / "SKILL.md"
OUTPUT_DIR = PROJECT_ROOT / "output"
BENCHMARKS_DIR = PROJECT_ROOT / "samples" / "benchmarks"

DEFAULT_MAX_LOOPS = 3


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def build_system_prompt(memory: MemoryLoader | None) -> str:
    """System prompt: the full knowledge base and the two reply formats (COMPILE FIX when the
    code does not compile, FULL ANALYSIS when it compiles but fails testbench checks)."""
    if memory is not None:
        context_bundle = memory.build_context_bundle()
        kb_intro = ("You have access to a knowledge base of Verilog references, common bug patterns,\n"
                    "    fix templates, and correct module templates. Use this knowledge to guide your analysis.\n\n    ")
        kb_block = f"=== KNOWLEDGE BASE ===\n    {context_bundle}\n\n    "
        kb_xref = "- Cross-reference against the known bug patterns and templates.\n    "
        verify = "Pattern matches from memory are hints only —\n    always verify"
    else:
        context_bundle = ""
        kb_intro = ""
        kb_block = ""
        kb_xref = ""
        verify = "Always verify"
    return textwrap.dedent(f"""\
    You are VerilogDebugAgent, an expert hardware verification and debugging AI.
    Your task is to systematically find and fix bugs in Verilog/SystemVerilog code.

    {kb_intro}Before you are called, the buggy code has already been COMPILED against its
    testbench and SIMULATED. You will receive the full compile log (errors and
    warnings) and simulation results (which test cases passed/failed with expected
    vs actual values). READ THESE CAREFULLY — they are the most direct evidence
    of what is wrong.

    You may be called for MULTIPLE ROUNDS of debugging. In the first round you
    receive the original buggy code plus its compile + simulation results. In
    subsequent rounds you receive the fixed code from your previous attempt along
    with fresh compile + simulation results from running it. You must:
      1. Fix any compilation errors first (your code must compile cleanly)
      2. Then fix functional bugs so all testbench assertions pass
      3. Return the COMPLETE updated module in the specified JSON format

    IMPORTANT: The primary success metric is the testbench pass rate. A fix is only
    considered correct when ALL testbench assertions pass (100% pass rate).

    {kb_block}=== OUTPUT FORMAT ===
    Every message tells you which of the two formats below to use.

    1) COMPILE FIX — when the code does not compile:
    {{
        "compile_fixes": [
            {{
                "line": <line number>,
                "error": "<the compile error this fixes>",
                "fix": "<what you changed>"
            }}
        ],
        "fixed_code": "<complete corrected module — always return the FULL module>"
    }}

    COMPILE FIX RULES:
    - Change only what is needed to make the code compile: fix every error in the compile
      report and any other syntax error you see
    - Keep the design's behavior and structure; do NOT redesign, refactor or reformat the code

    2) FULL ANALYSIS — when the code compiles but testbench checks fail:
    {{
        "module_name": "<name>",
        "design_intent": "<brief description of what the module should do>",
        "iteration": <which debugging round this is, starting at 1>,
        "bugs": [
            {{
                "bug_id": "BUG-NNN",
                "line": <line number>,
                "severity": "critical|major|minor|warning",
                "category": "<e.g. F1, S3, I2>",
                "description": "<what is wrong>",
                "intent_analysis": "<what should this line do, based on the design description and surrounding code>",
                "root_cause": "<why the current code fails to do that>",
                "impact": "<behavioral consequence>",
                "buggy_code": "<exact line(s) as they appear in source>",
                "fix_reasoning": "<derive the fix solely from intent_analysis — if intent_analysis says X, the fix must implement X>",
                "fixed_code": "<corrected line(s) that directly implements fix_reasoning>"
            }}
        ],
        "fixed_code": "<complete corrected module — always return the FULL module>",
        "summary": {{
            "total_bugs": <N>,
            "critical": <N>,
            "major": <N>,
            "minor": <N>,
            "warning": <N>,
            "confidence": "high|medium|low",
            "remaining_issues": "<description of any issues you were unable to resolve, or 'none'>"
        }}
    }}

    FULL ANALYSIS RULES:
    Before proposing any fix, you MUST:
    1. Read the DESIGN DESCRIPTION to understand what the module is supposed to compute
    2. Trace the algorithm around each bug to understand the intended behavior at that point
    3. Ask "what change would make this code match the design description?" not
    "what is the most common fix for this pattern?"

    The design description is ground truth. {verify} a proposed fix makes sense given the described behavior before applying it.

    - Be thorough. Check every always block, every assignment, every case statement.
    {kb_xref}- GROUP similar/repeated bugs into ONE entry. For example if the same kind of
      error (e.g. out-of-bounds bit-select) occurs on lines 124, 152, 157, 162, ...
      report it ONCE with all affected lines: "line": [124, 152, 157, 162, ...].
      Do NOT create separate BUG entries for each repetition of the same pattern.
    - Keep the bugs list concise — prefer fewer, well-described entries over many
      near-duplicate entries.  This is critical for staying within output limits.
    - Return ONLY valid JSON — no markdown fences, no extra text.
    """)

def format_waveform_block(waveform_context: str, subject: str) -> str:
    return (
        f"\n=== SIMULATION WAVEFORM ({subject}) ===\n{waveform_context}\n"
        "Each line `cN: sig old→new` lists only the signals that changed in that cycle; "
        "unchanged cycles are omitted. `| TB: ...` is the testbench output in that cycle. "
        "Values are sampled just before each clock edge. "
        "Find the first cycle where a signal deviates from the intended behavior the design should have, then trace "
        "the signal back to the code that drives it."
    )

def format_dependency_block(dependency_context: str) -> str:
    return (
        "\n=== SIGNAL DEPENDENCY CONTEXT ===\n"
        f"{dependency_context}\n"
        "The above subgraph shows the signals that influence the failing signals. "
        "Path entries are ordered from the failing signal back to its roots, "
        "are suspicious line/code pointers for each signal on those dependency paths. "
        "Inspect those pointed-to assignments first, especially assignments to "
        "intermediate signals shared by multiple failing paths, before considering "
        "unrelated code."
    )


COMPILE_FIX_ASK = ("The code does not compile. Fix only the compile errors, without changing the design's "
                   "behavior or structure. Reply in the COMPILE FIX format. Return ONLY valid JSON.")
FULL_ANALYSIS_ASK = ("The code compiles but fails testbench checks. Find and fix the functional bugs. "
                     "Reply in the FULL ANALYSIS format. Return ONLY valid JSON.")


def build_initial_user_prompt(
    filename, source, static_report, tb_report="", design_description="",
    compile_error=False, waveform_context="", suspicious_lines="",
) -> str:
    """Prompt for iteration 1. compile_error: the code does not compile, so the prompt has
    no root-cause instructions, localizer or waveform, and asks for a COMPILE FIX."""
    parts = ["Please fix the compile errors in the following Verilog file." if compile_error else
             "Please analyze the following Verilog file for bugs and provide fixes."]
    if design_description:
        parts.append(f"\n=== DESIGN DESCRIPTION ===\n{design_description}" + ("" if compile_error else
            "\n\nBefore identifying any bug, first re-read the design description above "
            "and write down in intent_analysis what each suspicious line is supposed "
            "to accomplish according to that description. Your fix must implement that intent."))
    parts += [f"\n=== FILE: {filename} ===", source]
    if static_report:
        parts.append(f"\n=== STATIC ANALYSIS RESULTS ===\n{static_report}")
    if tb_report:
        parts.append(f"\n=== COMPILE & TESTBENCH RESULTS ===\n{tb_report}")
    if not compile_error:
        if waveform_context:
                    parts.append(format_waveform_block(waveform_context, "testbench run on the file above"))
        if suspicious_lines:
            parts.append(f"\n=== SUSPICIOUS LINES (predict-and-force localization) ===\n{suspicious_lines}")

    parts.append("\n" + (COMPILE_FIX_ASK if compile_error else FULL_ANALYSIS_ASK))
    return "\n".join(parts)


def build_refinement_prompt(
    iteration, previous_fixed_code, static_validation, tb_validation="",
    compile_error=False, waveform_context="", suspicious_lines="", rollback_note="", reverted_results="",
) -> str:
    """Prompt for iterations 2+. previous_fixed_code is the code the next fix applies to: the last
    fix, or the best version so far if the last fix was reverted (rollback_note). compile_error:
    that code does not compile, so the prompt has no localizer or waveform and asks for a COMPILE FIX."""
    if rollback_note:
        status, code_title, wave_on = rollback_note, "BEST CODE SO FAR (your last fix was reverted)", "the best code so far"
    else:
        status = "Your previous fix does not compile." if compile_error else "Your previous fix still has issues."
        code_title, wave_on = "YOUR PREVIOUS FIXED CODE", "your previous fixed code"

    parts = [f"This is debugging iteration {iteration}.", status]
    if reverted_results:
        parts.append(f"\n=== RESULTS OF YOUR REVERTED FIX (from your previous reply) ===\n{reverted_results}\n"
                     "Use these results to avoid repeating the same mistake.")
    parts += [f"\n=== {code_title} ===", previous_fixed_code]
    results_of = " (best code so far)" if rollback_note else ""
    if static_validation:
        parts.append(f"\n=== STATIC ANALYSIS ISSUES{results_of} ===\n{static_validation}")
    if tb_validation:
        parts.append(f"\n=== COMPILE & TESTBENCH RESULTS{results_of} ===\n{tb_validation}")
    if not compile_error:
        if waveform_context:
            parts.append(format_waveform_block(waveform_context, f"testbench run on {wave_on}"))
        if suspicious_lines:
            parts.append(f"\n=== SUSPICIOUS LINES (predict-and-force localization) ===\n{suspicious_lines}")
        
    parts.append("\n" + (COMPILE_FIX_ASK if compile_error else FULL_ANALYSIS_ASK))
    return "\n".join(parts)

# ---------------------------------------------------------------------------
# Tool runners
# ---------------------------------------------------------------------------

def run_static_analysis(verilog_file: str) -> str:
    """Run the static analyzer tool and capture its output."""
    analyzer = PROJECT_ROOT / "tools" / "static_analyzer.py"
    try:
        result = subprocess.run(
            [sys.executable, str(analyzer), verilog_file],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout
        if result.stderr:
            output += "\n[STDERR]\n" + result.stderr
        return output
    except subprocess.TimeoutExpired:
        return "[ERROR] Static analysis timed out."
    except FileNotFoundError:
        return "[ERROR] Static analyzer not found."


def static_analysis_is_clean(report: str) -> bool:
    """Return True if the static analysis report has no findings."""
    return report.lstrip().startswith("[OK] No issues found")


def find_testbench(verilog_file: str) -> str | None:
    """Auto-discover a benchmark testbench for the given source file."""
    stem = Path(verilog_file).stem
    tb_path = BENCHMARKS_DIR / f"{stem}_tb.v"
    return str(tb_path) if tb_path.exists() else None


def run_testbench(source_file: str, testbench_file: str, waveform_dir: str | Path | None = None) -> dict:
    runner = PROJECT_ROOT / "tools" / "benchmark_runner.py"
    extra = ["--waveform-dir", str(waveform_dir)] if waveform_dir else []
    try:
        result = subprocess.run(
            [sys.executable, str(runner), source_file, testbench_file, "--json", *extra],
            capture_output=True, text=True, timeout=60,
        )
        stdout = result.stdout

        # Runner prints a status line before the JSON — find where JSON starts
        json_start = stdout.find("{")
        if json_start != -1:
            try:
                return json.loads(stdout[json_start:])
            except json.JSONDecodeError:
                pass

        return {
            "error": "Failed to parse benchmark output",
            "raw": stdout + result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Benchmark timed out"}
    except FileNotFoundError:
        return {"error": "Benchmark runner not found"}

def _parse_signal_values(text: str) -> dict[str, str]:
    """Extract signal=value pairs from testbench messages."""
    return dict(re.findall(r"\b(\w+)\s*=\s*([^\s,)]+)", text))


def extract_buggy_signals(tb_result, valid_signals):
    buggy_signals = set()

    for td in tb_result.get("test_details", []):
        if td.get("status") != "fail":
            continue

        msg = td.get("message", "")

        exp_match = re.search(r'expected\s+(.*?)\s+got', msg)
        got_match = re.search(r'got\s+(.*)', msg)

        if exp_match and got_match:
            exp_pairs = _parse_signal_values(exp_match.group(1))
            got_pairs = _parse_signal_values(got_match.group(1))

            for sig in exp_pairs:
                if sig in valid_signals and sig in got_pairs:
                    if exp_pairs[sig] != got_pairs[sig]:
                        buggy_signals.add(sig)

        paren_expected = re.search(r"\(expected\s+([^)]+)\)", msg)
        if paren_expected:
            actual_pairs = _parse_signal_values(msg[:paren_expected.start()])
            exp_pairs = _parse_signal_values(paren_expected.group(1))

            for sig in exp_pairs:
                if sig in valid_signals and sig in actual_pairs:
                    if exp_pairs[sig] != actual_pairs[sig]:
                        buggy_signals.add(sig)

            if not exp_pairs and len(actual_pairs) == 1:
                sig, actual_value = next(iter(actual_pairs.items()))
                expected_value = paren_expected.group(1).strip()
                if sig in valid_signals and actual_value != expected_value:
                    buggy_signals.add(sig)

    return sorted(buggy_signals)


def format_tb_feedback(tb_result: dict) -> str:
    """Format testbench results into structured feedback for the LLM.

    Includes compilation errors/warnings, simulation log, and per-test results
    so the LLM has full visibility into what went wrong at each stage.
    """
    lines = []

    # --- Compilation stage ---
    compile_errors = tb_result.get("compile_errors", [])
    compile_warnings = tb_result.get("compile_warnings", [])
    compiled = tb_result.get("compiled", False)

    if not compiled:
        lines.append("[COMPILATION FAILED]")
        if compile_errors:
            lines.append("Compile errors:")
            for e in compile_errors:
                lines.append(f"  {e}")
        raw = tb_result.get("sim_stdout", "") or tb_result.get("raw", "")
        if raw:
            lines.append(f"Compiler output:\n{raw}")
        return "\n".join(lines)

    lines.append("[COMPILATION OK]")
    if compile_warnings:
        lines.append(f"Compile warnings ({len(compile_warnings)}):")
        for w in compile_warnings:
            lines.append(f"  {w}")

    # --- Simulation stage ---
    if not tb_result.get("simulated", False):
        lines.append(f"[SIMULATION FAILED] {tb_result.get('error', '')}")
        return "\n".join(lines)

    passed = tb_result.get("passed", 0)
    failed = tb_result.get("failed", 0)
    total = tb_result.get("total", 0)
    rate = tb_result.get("pass_rate", 0.0)

    lines.append(f"\n[SIMULATION RESULTS]")
    lines.append(f"Pass rate: {rate:.1%} ({passed}/{total} tests passed)")

    if failed > 0:
        lines.append(f"\nFailed tests ({failed}):")
        for td in tb_result.get("test_details", []):
            if td["status"] == "fail":
                lines.append(f"  [FAIL] {td['message']}")

    if total > 0 and failed == 0:
        lines.append("All tests passed.")

    return "\n".join(lines)


def tb_all_pass(tb_result: dict) -> bool:
    """Return True if every testbench assertion passed."""
    return tb_result.get("all_pass", False)


def save_report(report: dict, output_path: Path):
    """Save the debugging report as JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2)
    logger.info("Report saved to %s", output_path)


def _extract_json(text: str) -> str:
    """Extract a JSON object from *text*, handling preamble and code fences.

    Strategies (tried in order):
    1. Direct parse (text is already pure JSON)
    2. Strip markdown code fences (```json ... ``` or ``` ... ```)
    3. Find the first top-level '{' and last '}' and extract that substring
    """
    text = text.strip()

    # Strategy 1: already valid JSON
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract content from markdown code fences anywhere in text
    import re
    fence_match = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # Strategy 3: find outermost { ... }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace : last_brace + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    return text


def parse_llm_json(client, label: str) -> dict:
    """
    Attempt to parse the last assistant message as JSON.
    Handles preamble text and markdown fences.
    If extraction fails, sends a repair prompt once.
    """
    raw = client.conversation_history[-1]["content"]
    text = _extract_json(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("[%s] LLM returned non-JSON. Attempting repair prompt...", label)

        # TODO: Changed client chat for benchmarking
        if hasattr(client, "log_context"):
            client.log_context = dict(client.log_context, purpose="json_repair")
        repair_response, _ = client.chat(
            "Your previous response was not valid JSON. "
            "Please return ONLY the JSON object as specified, no other text."
        )
        text2 = _extract_json(repair_response)
        try:
            return json.loads(text2)
        except json.JSONDecodeError:
            logger.error("[%s] Failed to get valid JSON after retry.", label)
            return {"error": "LLM did not return valid JSON", "raw": repair_response}

def number_lines(source: str) -> str:
    return "\n".join(
        f"{i+1:4d} | {line}"
        for i, line in enumerate(source.splitlines())
    )

## TODO: Add to format verilog code, verible-verilog-format needs to be installed
## TODO: Add formatting to loop: format_verilog_file(str(tmp_path))
# import subprocess

# def format_verilog_file(file_path: str):
#     subprocess.run(
#         ["verible-verilog-format", "--inplace", file_path],
#         check=True
#     )


# ---------------------------------------------------------------------------
# Per-iteration prompt context: tracer, waveform, localizer
# ---------------------------------------------------------------------------

def _trace_design(source: Path, tb_result: dict, graph_save_path: Path | None = None) -> dict:
    """Build the signal dependency graph for `source`.

    Returns {"tracer", "cone", "buggy_signals", "dependency_context", "dependency_text"}.
    """
    ctx = {"tracer": None, "cone": None, "buggy_signals": [],
           "dependency_context": "", "dependency_text": ""}
    try:
        tracer = VerilogTracer(str(source))
        ctx["tracer"] = tracer
        ctx["cone"] = tracer.output_cone()

        buggy = extract_buggy_signals(tb_result, set(tracer.graph.nodes()))
        if not buggy and tb_result.get("failed", 0) > 0:
            buggy = tracer.get_output_ports()
            logger.info("No named signals in testbench failures — falling back to output ports: %s", buggy)
        ctx["buggy_signals"] = buggy

        if graph_save_path is not None:
            graph_save_path.parent.mkdir(parents=True, exist_ok=True)
        dep = tracer.build_dependency_context(buggy, graph_save_path=graph_save_path)
        ctx["dependency_context"] = dep
        ctx["dependency_text"] = json.dumps(dep, indent=2)
    except Exception as e:
        logger.warning("Verilog tracer failed on %s: %s — no dependency context", source.name, e)
        ctx["buggy_signals"], ctx["dependency_context"], ctx["dependency_text"] = [], "", ""
    return ctx


def _run_localizer(source: Path, tb_file, description: str, trace: dict, iteration: int,
                   forcing: bool = True) -> dict:
    """Run predict-force localization; returns its result dict (or {"error": ...})."""
    try:
        result = predict_force_localize(
            str(source), tb_file, description,
            trace["buggy_signals"], trace["dependency_text"],
            settings=None if forcing else {**localizer_settings(), "force": False},
            tracer=trace["tracer"],
        )
    except Exception as e:
        logger.warning("Localizer failed (iter %d): %s", iteration, e)
        return {"error": str(e)}

    summary = result.get("skipped") or {
        k: result.get(k) for k in ("accepted", "rounds", "lines", "seconds")
    }
    logger.info("Localizer (iter %d): %s", iteration, summary)
    return result


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def version_score(tb_result: dict, has_testbench: bool) -> float:
    """Testbench pass rate; -1 if the design does not compile or simulate."""
    if not has_testbench:
        return 0.0
    total = tb_result.get("total") or 0
    return (tb_result.get("passed") or 0) / total if total else -1.0


def _fmt_score(score: float, tb_result: dict) -> str:
    if score < 0:
        return "does not compile/simulate"
    return f"{tb_result.get('passed', 0)}/{tb_result.get('total', 0)} tests pass"


def run_agent(
    verilog_file: str,
    model: str = "qwen-coder-30b",
    backend: str = DEFAULT_BACKEND,
    testbench: str | None = None,
    output_file: str | None = None,
    output_dir: str | None = None,
    max_loops: int = DEFAULT_MAX_LOOPS,
    verbose: bool = False,
    design_description: str | None = None,
    waveform: bool = False,
    localize: bool = False,
    save_prompts: bool = False,
    use_kb: bool = True,
    forcing: bool = True,
):
    """Execute the iterative multi-loop debugging pipeline.
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )

    logger.info("Starting VerilogDebugAgent")

    verilog_path = Path(verilog_file).resolve()
    if not verilog_path.exists():
        logger.error("File not found: %s", verilog_path)
        sys.exit(1)

    resolved_output_dir = Path(output_dir) if output_dir else OUTPUT_DIR

    verilog_source = verilog_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    current_code = verilog_source  # the code the next fix applies to
    logger.info(
        "Loaded source: %s (%d lines)",
        verilog_path.name, verilog_source.count("\n") + 1,
    )

    # --- Step 1: Load knowledge base ---
    if use_kb:
        logger.info("Loading knowledge base...")
        memory = MemoryLoader()
        memory.load_all()
    else:
        logger.info("--no-kb: no knowledge base in the system prompt")
        memory = None

    # --- Step 2: Run static analysis on original ---
    logger.info("Running static analysis on original source...")
    static_report = run_static_analysis(verilog_file)
    logger.info("Static analysis complete:\n%s", static_report)

    # --- Step 3: Testbench discovery and initial run ---
    tb_file = testbench or find_testbench(verilog_file)
    has_testbench = tb_file is not None

    wave_dir = Path(tempfile.mkdtemp(prefix="vdebug_wave_")) if waveform else None

    initial_tb_report = ""
    initial_tb_result = {}
    if has_testbench:
        logger.info("Testbench found: %s", tb_file)
        logger.info("Running testbench on original buggy code...")
        initial_tb_result = run_testbench(verilog_file, tb_file, waveform_dir=wave_dir)
        initial_tb_report = format_tb_feedback(initial_tb_result)
        logger.info("Initial testbench results:\n%s", initial_tb_report)
    already_passes = bool(has_testbench and initial_tb_result.get("all_pass"))
    if already_passes:
        logger.info("The buggy file already passes every testbench check — "
                    "no debugging loop (early stop); the file is kept unchanged.")
    else:
        logger.info("No testbench found — evaluation will use static analysis only.")
        if testbench:
            logger.warning("Specified testbench not found: %s", testbench)

    # --- Step 3b: Load design description if available ---
    # Precedence:
    #   1. Explicit --description PATH from the CLI (treated as a file path).
    #   2. Auto-discovered <verilog>/../../design_description.txt next to the module.
    desc_cli_arg = design_description  # may be a path string or None
    design_description = ""

    if desc_cli_arg:
        desc_path = Path(desc_cli_arg)
        if desc_path.exists():
            design_description = desc_path.read_text(encoding="utf-8").strip()
            logger.info(
                "Loaded design description from %s (%d chars)",
                desc_path, len(design_description),
            )
        else:
            logger.warning("Description file not found: %s — ignoring", desc_path)
    else:
        desc_path = verilog_path.parent.parent / "design_description.txt"
        if desc_path.exists():
            design_description = desc_path.read_text(encoding="utf-8").strip()
            logger.info("Loaded design description from %s", desc_path)
        else:
            logger.info("No design_description.txt found (checked %s)", desc_path)

    if not design_description:
        logger.info("No design description provided. ------------------")

    # --- Step 4: Initialize LLM; the whole knowledge base is in the system prompt ---
    resolved_model = resolve_model_name(model)
    client = create_client(model, backend=backend)
    client.set_system_prompt(build_system_prompt(memory))
    if save_prompts and hasattr(client, "prompt_log_path"):
        # --save-prompts: save every LLM request/response of this file 
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
        prompt_log = resolved_output_dir / f"{verilog_path.stem}_prompts.jsonl"
        prompt_log.unlink(missing_ok=True)
        client.prompt_log_path = prompt_log
        client.log_context = {"file": verilog_path.name}

    # --- Step 5: Iterative loop ---
    loop_history = []
    analysis = None
    fixed_code = ""
    bugs_found = []
    temp_files: list[Path] = []
    iter_dir = Path(tempfile.mkdtemp(prefix="vdebug_iter_"))
    tb_result = {}
    static_validation = tb_validation = ""

    if already_passes:
        fixed_code = verilog_source

    best = {
        "iteration": 0, "code": verilog_source, "path": verilog_path, "tb": initial_tb_result,
        "static": static_report, "tb_report": initial_tb_report,
        "score": version_score(initial_tb_result, has_testbench),
    }
    rollback_note = ""
    reverted_results = ""

    for iteration in range(1, (0 if already_passes else max_loops) + 1):
        logger.info("=" * 50)
        logger.info("  DEBUGGING LOOP %d / %d", iteration, max_loops)
        logger.info("=" * 50)

        t_wall_start = time.perf_counter()

        is_first = (iteration == 1)
        trace_source = best["path"]   # the code this iteration's prompt shows
        current_tb = initial_tb_result if is_first else tb_result

        # compile error -> COMPILE FIX (no tracer, localizer, waveform or root-cause analysis);
        # compiles but fails tests -> FULL ANALYSIS with localizer and waveform.
        
        compile_error = has_testbench and not current_tb.get("compiled", False)
        prompt_kind = "compile" if compile_error else "functional"
        logger.info("Iteration %d prompt: %s", iteration,
                    "COMPILE FIX (code does not compile)" if compile_error else "FULL ANALYSIS")

        trace = {"cone": None, "buggy_signals": [], "dependency_context": "", "dependency_text": ""}
        waveform_text, waveform_info, localizer = "", {}, {}
        if not compile_error:
            trace = _trace_design(
                trace_source, current_tb,
                graph_save_path=resolved_output_dir / f"iter{iteration}_{verilog_path.stem}_dg.png",
            )
            if waveform:
                waveform_text, waveform_info = build_waveform_context(current_tb, cone=trace["cone"])
                logger.info("Waveform (iter %d): %s", iteration,
                            waveform_info.get("skipped") or waveform_info.get("error")
                            or f'{waveform_info.get("chars")} chars')
                logger.info("Waveform table (iter %d):\n%s", iteration, waveform_text or "(none)")
            if localize and has_testbench:
                localizer = _run_localizer(trace_source, tb_file, design_description or "", trace, iteration,
                                           forcing=forcing)
        buggy_signals = trace["buggy_signals"]
        dependency_context = trace["dependency_context"]

        if is_first:
            user_prompt = build_initial_user_prompt(
                verilog_path.name, number_lines(verilog_source),
                static_report, initial_tb_report,
                design_description=design_description or "",
                compile_error=compile_error,
                waveform_context=waveform_text,
                suspicious_lines=localizer.get("prompt_block", ""),
            )
        else:
            user_prompt = build_refinement_prompt(
                iteration, number_lines(current_code),
                static_validation, tb_validation,
                compile_error=compile_error,
                waveform_context=waveform_text,
                suspicious_lines=localizer.get("prompt_block", ""),
                rollback_note=rollback_note, reverted_results=reverted_results,
            )

        # TODO: Remove for benchmarking
        if hasattr(client, "log_context"):
            client.log_context = {"file": verilog_path.name, "iteration": iteration,
                                  "prompt_kind": prompt_kind, "purpose": "fix"}
        _, iter_metrics = client.chat(user_prompt)
        iter_wall_time = time.perf_counter() - t_wall_start

        # Get system prompt token count
        system_prompt_tokens = len(
            client._tokenizer.encode(client._system_prompt)
        ) if hasattr(client, '_system_prompt') else 0

        analysis = parse_llm_json(client, f"iter-{iteration}")

        reply_code = analysis.get("fixed_code") or ""
        if reply_code:
            current_code = reply_code
            fix_kind = "full"
        else:
            logger.warning("Iteration %d: no fixed_code in the reply; code unchanged", iteration)
            fix_kind = "none"
        fixed_code = current_code

        bugs_found = analysis.get("bugs") or []
        compile_fixes = analysis.get("compile_fixes") or []

        # --- Print bugs for this iteration ---
        print(f"\n  --- Iteration {iteration} Bugs ---")
        if bugs_found:
            for bug in bugs_found:
                print(f"  [{bug.get('severity', '?').upper()}] {bug.get('bug_id', '?')} "
                      f"(line {bug.get('line', '?')}) [{bug.get('category', '?')}]")
                print(f"    Description:   {bug.get('description', '')}")
                print(f"    Intent:        {bug.get('intent_analysis', '')}")
                print(f"    Root cause:    {bug.get('root_cause', '')}")
                print(f"    Fix reasoning: {bug.get('fix_reasoning', '')}")
                buggy_snip = bug.get('buggy_code') or ''
                fixed_snip = bug.get('fixed_code') or ''
                # LLM sometimes returns multi-line snippets as a list of lines
                if isinstance(buggy_snip, list):
                    buggy_snip = "\n".join(map(str, buggy_snip))
                if isinstance(fixed_snip, list):
                    fixed_snip = "\n".join(map(str, fixed_snip))
                print(f"    Buggy Code:         {str(buggy_snip).strip()}")
                print(f"    Fixed Code:         {str(fixed_snip).strip()}")
                print()
        elif compile_fixes:
            for cf in compile_fixes:
                cf = cf if isinstance(cf, dict) else {"fix": cf}
                print(f"  [COMPILE] line {cf.get('line', '?')}: {cf.get('error', '')}")
                print(f"    Fix: {cf.get('fix', '')}")
        else:
            print("  No bugs reported.")

        # Validate the fix: static analysis + testbench
        static_validation = ""
        tb_validation = ""
        tb_result = {}
        static_clean = False
        tb_pass = False

        if fixed_code or current_code:
            tmp_path = iter_dir / f"{verilog_path.stem}_fixed_iter{iteration}.v"

            tmp_path.write_text(current_code, encoding="utf-8")

            temp_files.append(tmp_path)
            logger.info("Fixed code (iter %d) written to %s", iteration, tmp_path)

            logger.info("Validating fixed code (static analysis)...")
            static_validation = run_static_analysis(str(tmp_path))
            static_clean = static_analysis_is_clean(static_validation)
            logger.info("Static validation (iter %d):\n%s", iteration, static_validation)

            if has_testbench:
                logger.info("Validating fixed code (testbench)...")
                tb_result = run_testbench(str(tmp_path), tb_file, waveform_dir=wave_dir)
                tb_validation = format_tb_feedback(tb_result)
                tb_pass = tb_all_pass(tb_result)
                logger.info("Testbench validation (iter %d):\n%s", iteration, tb_validation)
        else:
            logger.warning("Iteration %d: no fixed_code in LLM response.", iteration)

        converged_this_iter = tb_pass if has_testbench else static_clean

        loop_record = {
            "iteration": iteration,
            "prompt_kind": prompt_kind,                # compile (COMPILE FIX) | functional (FULL ANALYSIS)
            "fix_kind": fix_kind,                      # full | none
            "bugs_reported": len(bugs_found),
            "compile_fixes_reported": len(compile_fixes),
            "buggy_signals": buggy_signals,
            "dependency_context": dependency_context,
            "static_clean": static_clean,
            "testbench_pass_rate": tb_result.get("pass_rate") if tb_result else None,
            "testbench_passed": tb_result.get("passed") if tb_result else None,
            "testbench_failed": tb_result.get("failed") if tb_result else None,
            "testbench_total": tb_result.get("total") if tb_result else None,
            "testbench_all_pass": tb_pass,
            "waveform_context": waveform_text,
            "waveform_info": waveform_info,
            "localizer": {k: v for k, v in localizer.items() if k != "prompt_block"},
            "converged": converged_this_iter,
            # --- new benchmark fields ---
            "system_prompt_tokens": system_prompt_tokens,
            "user_prompt_tokens": iter_metrics["input_tokens"] - system_prompt_tokens,
            "input_tokens": iter_metrics["input_tokens"],
            "cached_input_tokens": iter_metrics.get("cached_input_tokens"),
            "finish_reason": iter_metrics.get("finish_reason"),
            "trimmed_messages": iter_metrics.get("trimmed_messages", 0),
            "output_tokens": iter_metrics["output_tokens"],
            "total_tokens": iter_metrics["total_tokens"],
            "latency_s": iter_metrics["latency_s"],
            "ttft_s": iter_metrics.get("ttft_s"),
            "decode_s": iter_metrics.get("decode_s"),
            "decode_tokens_per_second": iter_metrics.get("decode_tokens_per_second"),
            "wall_time_s": round(iter_wall_time, 3),
            "tokens_per_second": iter_metrics["tokens_per_second"],
            "context_window_used": iter_metrics["input_tokens"],
            "context_window_pct": round(
                iter_metrics["input_tokens"] / client.context_window_size * 100, 1
            ) if hasattr(client, 'context_window_size') else None,
        }

        score = version_score(tb_result, has_testbench)
        rollback_note = reverted_results = ""
        if fixed_code and score >= best["score"]:
            best = {"iteration": iteration, "code": current_code, "path": tmp_path, "tb": tb_result,
                    "static": static_validation, "tb_report": tb_validation, "score": score}
        elif fixed_code:
            rollback_note = (
                f"Your fix in iteration {iteration} made the design worse "
                f"({_fmt_score(score, tb_result)}, vs {_fmt_score(best['score'], best['tb'])} for the best "
                f"version, from {'the original code' if best['iteration'] == 0 else 'iteration %d' % best['iteration']}), "
                f"so it was reverted. The code below is that best version; try a different fix.")
            logger.warning("Iteration %d: rolled back to %s", iteration,
                           "the original code" if best["iteration"] == 0 else f"iteration {best['iteration']}")
            reverted_results = tb_validation
            current_code = best["code"]
            tb_result, static_validation, tb_validation = best["tb"], best["static"], best["tb_report"]
        loop_record["rolled_back"] = bool(rollback_note)
        loop_record["best_iteration"] = best["iteration"]
        loop_history.append(loop_record)

        if converged_this_iter:
            logger.info(
                "Fixed code passes all checks at iteration %d. Done.", iteration,
            )
            break
        elif iteration < max_loops:
            logger.info(
                "Issues remain after iteration %d. Continuing to next loop...",
                iteration,
            )
        else:
            logger.warning(
                "Reached max loops (%d) with issues still remaining.", max_loops,
            )

    # --- Clean up intermediate temp files ---
    for tmp in temp_files:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    shutil.rmtree(iter_dir, ignore_errors=True)
    if wave_dir:
        shutil.rmtree(wave_dir, ignore_errors=True)

    if loop_history:
        fixed_code = best["code"]  

    # --- Save corrected code to output/ ---
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_fixed_path = None
    if fixed_code:
        output_fixed_path = resolved_output_dir / f"{verilog_path.stem}_fixed.v"
        output_fixed_path.write_text(fixed_code, encoding="utf-8")
        logger.info("Corrected code saved to %s", output_fixed_path)

    # --- Final testbench evaluation on the saved file ---
    final_tb = {}
    if has_testbench and output_fixed_path:
        final_tb = run_testbench(str(output_fixed_path), tb_file)
        logger.info("Final testbench: %s", format_tb_feedback(final_tb))

    # --- Build final report ---
    converged = loop_history[-1]["converged"] if loop_history else already_passes
    report = {
        "timestamp": datetime.now().isoformat(),
        "source_file": str(verilog_path),
        "model": resolved_model,
        "backend": backend,
        "max_loops": max_loops,
        "settings": {"waveform": waveform, "localize": localize, "forcing": forcing, "knowledge_base": use_kb},
        "iterations_used": len(loop_history),
        "best_iteration": best["iteration"],
        "rollbacks": sum(1 for r in loop_history if r.get("rolled_back")),
        "converged": converged,
        "early_stop": already_passes,  
        "evaluation": {
            "testbench_file": tb_file,
            "initial_pass_rate": initial_tb_result.get("pass_rate") if initial_tb_result else None,
            "final_pass_rate": final_tb.get("pass_rate") if final_tb else None,
            "final_passed": final_tb.get("passed") if final_tb else None,
            "final_failed": final_tb.get("failed") if final_tb else None,
            "final_total": final_tb.get("total") if final_tb else None,
            "all_pass": final_tb.get("all_pass", False) if final_tb else None,
        },
        "corrected_file": str(output_fixed_path) if output_fixed_path else None,
        "loop_history": loop_history,
        "final_analysis": analysis,
        "initial_static_analysis": static_report,
    }

    if output_file:
        out_path = Path(output_file)
    else:
        out_path = resolved_output_dir / f"report_{verilog_path.stem}_{timestamp}.json"

    save_report(report, out_path)

    bench_iters = [r for r in loop_history if "latency_s" in r]

    report["benchmark"] = {
        "total_wall_time_s": round(sum(r["wall_time_s"] for r in bench_iters), 3),
        "total_latency_s": round(sum(r["latency_s"] for r in bench_iters), 3),
        "avg_latency_per_iter_s": round(statistics.mean(r["latency_s"] for r in bench_iters), 3) if bench_iters else 0.0,
        "total_input_tokens": sum(r["input_tokens"] for r in bench_iters),
        "total_output_tokens": sum(r["output_tokens"] for r in bench_iters),
        "total_tokens": sum(r["total_tokens"] for r in bench_iters),
        "avg_tokens_per_second": round(statistics.mean(r["tokens_per_second"] for r in bench_iters), 1) if bench_iters else 0.0,
        "avg_system_prompt_tokens": round(statistics.mean(r["system_prompt_tokens"] for r in bench_iters)) if bench_iters else 0,
        "avg_user_prompt_tokens": round(statistics.mean(r["user_prompt_tokens"] for r in bench_iters)) if bench_iters else 0,
        "peak_context_window_pct": max(
            (r["context_window_pct"] for r in bench_iters if r["context_window_pct"]), default=None
        ),
    }

    # --- Print summary ---
    summary = analysis.get("summary", {}) if analysis else {}
    n_iters = report["iterations_used"]
    eval_info = report["evaluation"]

    print("\n" + "=" * 60)
    print("  VERILOG DEBUG AGENT — REPORT SUMMARY")
    print("=" * 60)
    print(f"  File:       {verilog_path.name}")
    print(f"  Model:      {resolved_model}")
    print(f"  Backend:    {backend}")
    print(f"  Iterations: {n_iters} / {max_loops}")
    print(f"  Converged:  {'yes' if converged else 'NO — issues may remain'}")
    print(f"  Bugs:       {summary.get('total_bugs', '?')}")
    print(f"  Confidence: {summary.get('confidence', '?')}")

    print(f"  Bugs:       {summary.get('total_bugs', '?')}")
    print(f"  Confidence: {summary.get('confidence', '?')}")

    remaining = summary.get("remaining_issues", "")
    if remaining and remaining.lower() != "none":
        print(f"  Remaining:  {remaining}")
    if eval_info.get("testbench_file"):
        init_rate = eval_info.get("initial_pass_rate")
        final_rate = eval_info.get("final_pass_rate")
        print(f"\n  --- Testbench Evaluation ---")
        print(f"  Testbench:        {Path(eval_info['testbench_file']).name}")
        if init_rate is not None:
            print(f"  Buggy pass rate:  {init_rate:.1%}")
        if final_rate is not None:
            print(f"  Fixed pass rate:  {final_rate:.1%}  "
                  f"({eval_info.get('final_passed')}/{eval_info.get('final_total')} tests)")
        print(f"  All tests pass:   {'YES' if eval_info.get('all_pass') else 'NO'}")
        if loop_history:
            print(f"\n  --- Per-Iteration Pass Rates ---")
            for rec in loop_history:
                pr = rec.get("testbench_pass_rate")
                pr_str = f"{pr:.0%}" if pr is not None else "N/A"
                p = rec.get("testbench_passed", "?")
                t = rec.get("testbench_total", "?")
                print(f"    Iter {rec['iteration']}: {pr_str} ({p}/{t} tests)")
    print(f"\n  Report:     {out_path}")
    if output_fixed_path:
        print(f"  Fixed code: {output_fixed_path}")

    bench = report.get("benchmark", {})
    print(f"\n  --- Benchmark ---")
    print(f"  Total wall time:     {bench.get('total_wall_time_s')}s")
    print(f"  Total latency:       {bench.get('total_latency_s')}s")
    print(f"  Avg latency/iter:    {bench.get('avg_latency_per_iter_s')}s")
    print(f"  Total input tokens:  {bench.get('total_input_tokens')}")
    print(f"  Total output tokens: {bench.get('total_output_tokens')}")
    print(f"  Avg tok/s:           {bench.get('avg_tokens_per_second')}")
    print(f"  Peak context used:   {bench.get('peak_context_window_pct')}%")

    row = {
        "timestamp": report["timestamp"],
        "model": report["model"],
        "file": Path(report["source_file"]).name,
        "converged": report["converged"],
        "iterations": report["iterations_used"],
        "final_pass_rate": report["evaluation"]["final_pass_rate"],
        "all_pass": report["evaluation"]["all_pass"],
        **report["benchmark"],
    }

    summary_path = Path("output/benchmark_summary.csv")
    summary_exists = summary_path.exists()

    with open(summary_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not summary_exists:
            writer.writeheader()
        writer.writerow(row)

    print("=" * 60)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Verilog Debug Agent — AI-powered iterative HDL bug finder and fixer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          # Remote model (testbench auto-discovered from samples/benchmarks/)
          python -m agent.debug_agent samples/buggy/counter_4bit.v
          python -m agent.debug_agent samples/buggy/alu_simple.v --model gpt-4o --backend openrouter

          # Local model (Qwen3-Coder, no API key needed)
          python -m agent.debug_agent samples/buggy/counter_4bit.v --model qwen-coder --backend hf

          # vLLM backend
          python -m agent.debug_agent samples/buggy/counter_4bit.v --model qwen-coder --backend vllm

          # Explicit testbench and multiple loops
          python -m agent.debug_agent samples/buggy/fsm_traffic.v \\
              --testbench samples/benchmarks/fsm_traffic_tb.v \\
              --max-loops 5
        """),
    )
    parser.add_argument(
        "verilog_file", nargs="?", help="Path to the buggy Verilog file",
    )
    parser.add_argument(
        "--model", "-m", default="qwen-coder-30b",
        help=(
            "LLM model to use. Remote (OpenRouter): "
            + ", ".join(k for k in ALL_MODELS if not k.startswith("local"))
            + ". Local: qwen-coder-30b, qwen-coder-next (or any HuggingFace model ID)"
        ),
    )
    parser.add_argument(
        "--backend", "-b", choices=BACKENDS, default=DEFAULT_BACKEND,
        help=(
            "Inference backend: openrouter (remote API), hf (local HuggingFace "
            f"transformers), vllm (local vLLM engine). Default: {DEFAULT_BACKEND}"
        ),
    )
    parser.add_argument(
        "--testbench", "-t",
        help="Path to testbench file (auto-discovered from samples/benchmarks/ if omitted)",
    )
    parser.add_argument("--output", "-o", help="Output report file path")
    parser.add_argument(
        "--output-dir", help="Directory for all output files (fixed code, reports, temp files)",
    )
    parser.add_argument(
        "--max-loops", "-L", type=int, default=DEFAULT_MAX_LOOPS,
        help=f"Max debugging iterations (default: {DEFAULT_MAX_LOOPS})",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--list-models", action="store_true",
        help="List all available model shortnames (remote + local)",
    )
    parser.add_argument(
        "--description", "-d",
        help="Path to a text file describing what the module is supposed to do",
    )
    parser.add_argument(
        "--waveform", action="store_true",
        help="Add a simulation waveform transition table to the prompts",
    )
    parser.add_argument(
        "--localize", action="store_true",
        help="After a failed iteration, localize the bug with the small predict-and-force model "
             "(config.yaml `localizer:`) and add the suspicious lines to the refinement prompt",
    )
    parser.add_argument(
        "--no-kb", action="store_true",
        help="Ablation: leave the knowledge base (skills/, bug patterns, templates) out of the system prompt",
    )
    parser.add_argument(
        "--no-forcing", action="store_true",
        help="Ablation (with --localize): the localizer predicts once and nothing is forced or simulated; "
             "suspects come from the unverified prediction",
    )
    parser.add_argument(
        "--save-prompts", action="store_true",
        help="Save every LLM request and reply to <output-dir>/<stem>_prompts.jsonl "
             "(vllm backend; for replaying the exact calls)",
    )

    args = parser.parse_args()

    if args.list_models:
        print("Available model shortnames:\n")
        print("  Remote (--backend openrouter — requires OPENROUTER_API_KEY):")
        for short, full in ALL_MODELS.items():
            if not full.startswith("local/"):
                print(f"    {short:20s} -> {full}")
        print("\n  Local (--backend hf [default] or --backend vllm):")
        for short, full in ALL_MODELS.items():
            if full.startswith("local/"):
                print(f"    {short:20s} -> {full.removeprefix('local/')}")
        print(
            "\n  You can also pass any OpenRouter model ID with --backend openrouter,"
            "\n  or any HuggingFace model ID with --backend hf / --backend vllm."
        )
        sys.exit(0)

    if not args.verilog_file:
        parser.error("verilog_file is required (unless using --list-models)")

    run_agent(
        verilog_file=args.verilog_file,
        model=args.model,
        backend=args.backend,
        testbench=args.testbench,
        output_file=args.output,
        output_dir=args.output_dir,
        max_loops=args.max_loops,
        verbose=args.verbose,
        design_description=args.description,
        waveform=args.waveform,
        localize=args.localize,
        save_prompts=args.save_prompts,
        use_kb=not args.no_kb,
        forcing=not args.no_forcing,
    )


if __name__ == "__main__":
    main()
