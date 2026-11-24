# Verilog Debugging Agent Skill

## Metadata
- **Skill Name**: Verilog Debug Agent
- **Version**: 1.0
- **Description**: Systematic Verilog/SystemVerilog debugging skill. The agent first compiles the buggy code against its testbench, reads compile errors/warnings and simulation logs, then feeds all evidence to the LLM for reasoning-driven fix generation. Success is measured by testbench pass rate (100% = correct).
- **Trigger Criteria**:
  - User provides Verilog (.v) or SystemVerilog (.sv) source files
  - User reports synthesis warnings, simulation mismatches, or unexpected behavior
  - User requests code review of HDL modules

---

## Core Workflow: Compile → Read Logs → LLM → Fix → Recompile to Minimize LLM-Testbench Interaction

```
  buggy.v + testbench_tb.v + design_description.txt (if available)
         │
    ┌────▼─────────────┐
    │ DISCOVER CONTEXT │  Auto-find design_description.txt (search upward)
    └────┬─────────────┘
         │
    ┌────▼─────┐
    │ COMPILE  │  iverilog -Wall → capture errors + warnings
    └────┬─────┘
         │ compile ok?
    ┌────▼─────┐
    │ SIMULATE │  vvp → capture [PASS]/[FAIL] per test + sim output
    └────┬─────┘
         │
    ┌────▼──────────────────────────────────┐
    │ FEED TO LLM:                          │
    │   - Design specification (if found)   │
    │   - Source code                       │
    │   - Compile errors & warnings         │
    │   - Simulation pass/fail per test     │
    │   - Static analysis findings          │
    │   - Knowledge base context            │
    └────┬──────────────────────────────────┘
         │
    ┌────▼─────┐
    │ LLM FIX  │  Analyze evidence → produce fixed module
    └────┬─────┘
         │
    ┌────▼─────┐
    │ COMPILE  │  Compile fixed code against same testbench
    │ + SIM    │  Run simulation, capture results
    └────┬─────┘
         │
         ▼ all pass? → DONE
         │ else → feed compile/sim results back to LLM (next iteration)
```

---

## Memory Knowledge Base

The agent loads all documents from `memory/` into the LLM context before any
debugging begins. Each document serves a specific role in the debugging pipeline.

### memory/debugging_manuals/common_bug_patterns.md
**Role: Primary bug detection checklist**

A catalog of 23 bug categories organized into three groups:
- **Syntax Errors (S1-S9)**: Structural issues caught by the compiler — missing
  punctuation (S1), undeclared signals (S2), `=` vs `==` confusion (S3), duplicate
  declarations (S4), encoding issues (S5), wire/reg type violations (S6), missing
  port directions (S7), out-of-bounds bit-selects (S8), reserved keyword misuse (S9).
- **Function Errors (F1-F10)**: Behavioral bugs causing wrong simulation results —
  blocking/non-blocking misuse (F1), flawed sensitivity lists (F2), incomplete
  case/latch inference (F3), multiple drivers (F4), width truncation (F5), wrong
  operators/precedence (F6), missing radix in literals (F7), reset issues (F8),
  feedback loops (F9), infinite loops/FSM deadlocks (F10).
- **Instantiation Errors (I1-I4)**: Module wiring bugs — module name typo (I1),
  unconnected ports (I2), port name mismatch (I3), positional order mismatch (I4).

**How to use**: For every piece of buggy code, walk through ALL 23 categories and
confirm each is clean or flag it. The compile errors and warnings often map directly
to specific S/F/I codes (e.g., "undeclared identifier" → S2, "implicit definition
of wire" → S2/S6). Map each compile message to a category before reasoning further.

### memory/debugging_manuals/debugging_methodology.md
**Role: Overall debugging strategy**

Defines the phased approach:
1. **Phase 1 (LLM reasoning)**: Recover intent, decompose structure, run the full
   23-category scan, compare against templates. This is free and catches the majority
   of bugs.
2. **Phase 2 (testbench validation)**: Batch-apply all high-confidence fixes, run
   testbench, analyze remaining failures by reading expected vs actual values.
3. **Phase 3 (failure-driven localization)**: For remaining failures, trace backward
   from the failing output signal through its drivers to find the divergence point.
4. **Phase 4 (final validation)**: Rerun testbench; 100% pass = done.

Includes checklists for syntax (S1-S9), function (F1-F10), and instantiation (I1-I4)
that mirror common_bug_patterns.md but in checklist form for systematic scanning.

**How to use**: Follow this phased strategy. In particular, the backward-trace
technique (Phase 3) is critical when testbench failures remain after initial fixes —
start from the failing signal, find its drivers, and walk upstream until you find
where expected diverges from actual.

### memory/common_patterns/fix_patterns.json
**Role: Automated fix templates**

A JSON array of 16 structured fix patterns, each with:
- `trigger`: condition that activates the pattern (e.g., "blocking assignment in
  sequential always block")
- `description`: what the fix does
- `example_before` / `example_after`: concrete code transformation

Covers: blocking→non-blocking (F1), add default case (F3), fix sensitivity list (F2),
add async reset (F8), fix width mismatch (F5), remove multiple drivers (F4), add else
branch (F3), fix operator misuse (S3/F6), fix literal radix (F7), fix data type (S6),
fix module name typo (I1), fix missing port (I2), fix port name mismatch (I3),
positional→named ports (I4), fix precedence error (F6), fix infinite loop (F10).

**How to use**: After identifying a bug category, look up the matching fix pattern.
Apply the `example_after` transformation to the buggy code. When multiple bugs are
found, apply ALL matching patterns in one batch — do not fix one at a time.

### memory/hardware_specs/verilog_language_reference.md
**Role: Language rules and correct syntax**

Covers: module declarations, data types (wire/reg/integer/parameter), bit-width
literal format (`<width>'<radix><value>`), always block rules (combinational vs
sequential, blocking vs non-blocking), sensitivity list semantics, case statement
syntax (case/casez/casex), continuous assignments, operator table (arithmetic,
logical, bitwise, relational, shift, concatenation, conditional), synthesizable
patterns (mux, decoder, counter), generate statements, and module instantiation
(named vs positional ports, parameterized instantiation, common errors).

**How to use**: When unsure about correct Verilog syntax or semantics, consult this
document. It answers questions like "should this be `=` or `<=`?", "what does
`always @(*)` mean?", and "how do I correctly instantiate a parameterized module?".
Cross-reference compile warnings against the rules here to understand WHY something
is wrong.

### memory/hardware_specs/module_templates.md
**Role: Correct reference implementations to compare against**

Contains verified, bug-free template implementations for common design patterns:
- **Combinational logic module** (ALU-style): `always @(*)` with default assignment,
  complete case with default, blocking assignments
- **Sequential logic module** (flip-flop with async reset): `always @(posedge clk or
  posedge rst)`, non-blocking assignments, reset-first pattern
- **FSM template** (two-process style): separate state register (sequential) and
  next-state logic (combinational), default transitions, output logic with defaults
- **Shift register template**: parameterized width, async reset, MSB as serial_out,
  left-shift with serial_in at LSB
- **Parameterized counter template**: async reset, enable, overflow detection, proper
  width handling

**How to use**: After determining the design intent of the buggy module (e.g., "this
is a counter" or "this is an FSM"), compare its structure against the corresponding
template. Deviations from the template are likely bugs. For example: if the buggy
counter uses `always @(posedge clk)` without reset in the sensitivity list but the
template uses `always @(posedge clk or posedge rst)`, that's a reset bug (F8).

### memory/hardware_specs/synthesis_constraints.md
**Role: Synthesizability rules and hardware mapping**

Covers: which constructs are synthesizable vs simulation-only, clock domain crossing
rules, synchronous vs asynchronous reset trade-offs, latch avoidance rules (4 rules:
every if needs else, every case needs default, every signal assigned in all branches,
pre-assign defaults), timing considerations (critical path, pipelining), and a
resource mapping table (what hardware each Verilog construct becomes).

**How to use**: Consult when evaluating whether a fix will synthesize correctly. The
latch avoidance rules are particularly important — they explain WHY missing defaults
in case statements or incomplete if-else chains are bugs (they infer latches, which
cause simulation mismatches and synthesis warnings). The resource mapping table helps
understand what hardware the code actually creates.

---

## Instructions

### Step 0: Compile & Capture Logs (before any LLM call)

This happens automatically before the LLM sees anything.

1. **Compile** the buggy source together with the testbench:
   ```bash
   python tools/benchmark_runner.py <source.v> <testbench.v> --json
   ```
2. **Capture** from the compile stage:
   - Compile errors (syntax errors, undeclared signals, port mismatches)
   - Compile warnings (width mismatches, unused signals, implicit declarations)
3. **If compilation succeeds**, run simulation and capture:
   - Per-test `[PASS]` / `[FAIL]` with expected vs actual values
   - Timeout detection
   - Raw simulation output
4. **If compilation fails**, the compile errors alone are critical evidence — they tell you exactly what's structurally wrong before any behavioral analysis.

All of this output is fed to the LLM in the first prompt.

---

### Step 1: LLM Reasoning Pass (with compile + sim evidence)

The LLM receives:
- The full source code
- Compile errors and warnings
- Simulation test results (if compilation succeeded)
- Static analysis findings
- Knowledge base context

#### 1a. Read Compile Output First
- **Compile errors** = structural bugs: missing signals, port mismatches, syntax
- **Compile warnings** = likely bugs: width mismatches, unused signals, implicit nets
- Each compile message includes file name and line number — use these directly

#### 1b. Read Simulation Output
- Each `[FAIL]` line shows: test name, actual value, expected value
- Map failing tests to the signals/logic that drive them
- A test that expects `count=1` but gets `count=2` points at the counting logic

#### 1c. Recover Design Intent

**If a design description is provided** (``=== DESIGN SPECIFICATION ===`` section
in the prompt), treat it as the authoritative ground truth for what the module
should do.  Do NOT attempt to infer intent from the buggy code — the description
has the correct specification including module name, port definitions, and
expected behavior.  Compare the buggy code against this specification to identify
deviations.

The agent auto-discovers ``design_description.txt`` by searching upward from the
buggy file's directory (covers the MEIC testset layout:
``<module>/buggy/<file>.v`` → ``<module>/design_description.txt``).

**If no design description is available**, infer intent from:
1. Module name, port names, widths, parameters
2. Comments and documentation
3. Internal signal names (next_state, shift_reg, overflow)
4. Testbench structure (what's being tested tells you what the module should do)

#### 1d. Systematic Bug Scan (all 23 categories)

Scan against every category; confirm clean or flag with line number.

**Syntax checks (S1-S9):**
- S1: Missing semicolons, commas, parentheses, end/endmodule
- S2: Undeclared or misspelled signal names
- S3: Assignment `=` where comparison `==` intended
- S4: Variable declared twice (port + internal)
- S5: Non-ASCII characters
- S6: Assign to reg, or procedural assign to wire
- S7: Ports missing direction declarations
- S8: Bit-selects outside declared range
- S9: Reserved keywords used as identifiers

**Function checks (F1-F10):**
- F1: Blocking in sequential? Non-blocking in combinational?
- F2: Sensitivity lists complete? Reset edges match polarity?
- F3: All cases have default? All if-else cover all signals?
- F4: Signal driven by multiple always blocks?
- F5: Bit widths match? Truncation on arithmetic?
- F6: Wrong operators? Precedence errors?
- F7: Literals missing radix? Bare integers where binary intended?
- F8: All registers reset? Polarity match?
- F9: Combinational feedback loops?
- F10: Loops without termination? FSM deadlock states?

**Instantiation checks (I1-I4):**
- I1: Module names match defined names exactly
- I2: All ports connected
- I3: Named ports match definition
- I4: Positional order correct

#### 1e. Cross-Reference Against Templates
Compare code against `module_templates.md` reference designs. Flag deviations.

#### 1f. Generate Fix
Apply ALL identified fixes in a single batch. Return the COMPLETE corrected module.

---

### Step 2: Validate Fix (compile + simulate)

The agent automatically:
1. **Compiles** the proposed fix against the testbench
2. **If compile fails**: feeds compile errors back to LLM (next iteration)
3. **If compile succeeds**: runs simulation, captures test results
4. **If tests fail**: feeds failing test details back to LLM (next iteration)
5. **If all tests pass**: done, report success

---

### Step 3: Refinement (if needed)

When the LLM receives a refinement prompt, it gets:
- Its previous fixed code
- Fresh compile errors/warnings from compiling that code
- Fresh simulation results (if compilation succeeded)
- Static analysis findings

#### 3a. If Compilation Failed
- Read the compile error messages — they include file and line number
- Fix syntax/structural issues first
- Do NOT attempt functional fixes until compilation passes

#### 3b. If Tests Still Fail
- Read each `[FAIL]` line: test name, expected vs actual
- Trace backward from the failing output to its driver logic
- Compare against the original buggy code to see if the fix introduced a regression
- Apply targeted fix for the specific failing tests

#### 3c. Apply Fix
Return the COMPLETE corrected module with all fixes applied.

Target: 1-2 total iterations. Max: `--max-loops`.

---

### Step 4: Report

For each bug found:
```json
{
    "bug_id": "BUG-NNN",
    "line": "<line>",
    "category": "<S/F/I code>",
    "severity": "critical|major|minor|warning",
    "description": "<what>",
    "root_cause": "<why>",
    "suggested_fix": "<corrected code>"
}
```

Final report includes:
1. Bugs found/fixed by category
2. Per-iteration compile + testbench results:
   - Did it compile? Any warnings?
   - Pass rate and per-test results
3. Testbench pass rate: initial (buggy) → final (fixed)
4. Corrected `.v` file saved to `output/`
5. Iterations used

**Successful**: compilation clean + 100% testbench pass rate.

---

## Benchmark Dataset

`samples/benchmarks/` — testbenches with `[PASS]`/`[FAIL]` markers and `[SUMMARY]` line.

| Module | Testbench | Tests |
|--------|-----------|-------|
| counter_4bit.v | counter_4bit_tb.v | 10 |
| alu_simple.v | alu_simple_tb.v | 12 |
| fsm_traffic.v | fsm_traffic_tb.v | 8 |
| shift_register.v | shift_register_tb.v | 10 |

To add a new benchmark:
1. Place buggy source in `samples/buggy/<name>.v`
2. Create testbench at `samples/benchmarks/<name>_tb.v`
3. Use `[PASS]`/`[FAIL]` markers and a `[SUMMARY]` line
4. Agent auto-discovers testbench by stem matching

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| tools/benchmark_runner.py | Compile, simulate, and score against testbench |
| tools/static_analyzer.py | Lint-style static analysis |
| agent/debug_agent.py | Main agentic debug loop |
| agent/backends/openrouter_client.py | LLM via OpenRouter |
| agent/backends/local_model_client.py | Local HuggingFace LLM |
| agent/memory_loader.py | Loads knowledge base |
| scripts/run_all.py | Batch run all benchmarks |
| scripts/evaluate.py | Evaluate reports by pass rate |

---

## Supporting Documents

| Document | Path | Debugging Role |
|----------|------|----------------|
| Bug Patterns | `memory/debugging_manuals/common_bug_patterns.md` | 23-category checklist (S1-S9, F1-F10, I1-I4) with examples |
| Debug Methodology | `memory/debugging_manuals/debugging_methodology.md` | Phased strategy: reason → test → trace → validate |
| Fix Patterns | `memory/common_patterns/fix_patterns.json` | 16 structured before/after fix templates by category |
| Verilog Reference | `memory/hardware_specs/verilog_language_reference.md` | Language rules, operator table, correct syntax |
| Module Templates | `memory/hardware_specs/module_templates.md` | Bug-free reference designs (counter, FSM, ALU, shifter) |
| Synthesis Guide | `memory/hardware_specs/synthesis_constraints.md` | Latch avoidance rules, reset patterns, hardware mapping |
