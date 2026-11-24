# Memory Knowledge Base Index

Use this index to decide which documents to retrieve for a given debugging problem.
Request documents by their exact key (shown in each entry header).

---

## common_patterns/

### `common_patterns/fix_patterns.json`
**Contains:** Structured before/after fix examples for 16 bug patterns across categories: assignment, case_statement, driver, expression, instantiation, latch, literal, loop, operator, reset, sensitivity, type, width.
**Patterns:** 16 fix patterns
**Categories:** assignment, case_statement, driver, expression, instantiation, latch, literal, loop, operator, reset, sensitivity, type, width
**Trigger phrases:**
  - blocking assignment in sequential always block
  - case statement without default branch
  - manual sensitivity list in combinational logic
  - sequential block missing async reset in sensitivity
  - assignment between signals of different widths
  - signal driven by multiple always blocks
  - if without else in combinational always block
  - assignment operator = used in conditional where comparison == intended
  - bare integer used where sized binary/hex literal intended
  - continuous assign to reg or procedural assign to wire
  - instantiated module name does not match any defined module
  - module instantiation missing one or more port connections
  - named port in instantiation does not match module definition
  - positional port connection with potential order mismatch
  - bitwise operator where logical operator intended, or missing parentheses
  - forever/while/for without termination or FSM deadlock state
**Retrieve when:** A specific bug category or pattern has been identified by the LLM or static analysis. Match against trigger phrases or category names. Covers: blocking_to_nonblocking, add_default_case, fix_sensitivity_list, add_async_reset, fix_width_mismatch, remove_multiple_drivers....

## debugging_manuals/

### `debugging_manuals/common_bug_patterns.md`
**Sections:** Common Verilog Bug Patterns, SYNTAX ERRORS, S1. Premature Termination, S2. Undefined Variable, S3. Operator Misuse, S4. Redundant Variable Declaration, S5. Incorrect Encoding, S6. Incorrect Data Type Assignment, S7. Port Mode Declaration Error, S8. Data Index Out-of-Bounds Error, S9. Improper Use of Keywords, FUNCTION ERRORS, F1. Misuse of Assignments (Blocking vs Non-Blocking), F2. Flawed Sensitivity List, F3. Incomplete Case / If-Else (Latch Inference), F4. Concurrent Variable Use (Multiple Drivers), F5. Insufficient Bit Width, F6. Logical Errors in Expressions, F7. Mismatched Assignment Values, F8. Missing Reset or Incorrect Reset Polarity, F9. Asynchronous Feedback Loops, F10. Infinite Loop Constructs, INSTANTIATION ERRORS, I1. Incorrect Module Name, I2. Incomplete Port Connection, I3. Port Name Mismatch, I4. Port Order Mismatch (Positional)
**Keywords:** F1, F10, F2, F3, F4, F5, F6, F7, F8, F9, I1, I2, I3, I4, S1, S2, S3, S4, S5, S6
**Retrieve when:** Compiler errors, simulation failures, latch warnings, blocking/non-blocking confusion, reset polarity bugs, port mismatches.

### `debugging_manuals/debugging_methodology.md`
**Sections:** Verilog Debugging Methodology, Philosophy: Reason First, Simulate Second, Phase 1: LLM-Only Reasoning (zero cost), Phase 2: Testbench Validation (first iteration), Phase 3: Failure-Driven Localization (reasoning, not simulation), Phase 4: Final Validation, Key Metrics
**Entries:**
  - **1.1 Intent Recovery** — - Infer expected behavior from module name, port names, comments, signal names - Determine design type: combinational, sequential, FSM, datapath
  - **1.2 Structural Analysis** — - Decompose into: ports, signals, always blocks, assigns, instantiations - For each always block: is it combinational or sequential? - For each signal...
  - **1.3 Systematic 23-Category Scan** — Check ALL categories from common_bug_patterns.md (S1-S9, F1-F10, I1-I4): **Syntax Checklist (S1-S9):** - [ ] No missing punctuation (semicolons, comma...
  - **1.4 Template Comparison** — Compare code structure against correct templates in module_templates.md.
  - **2.1 Run Original** — Run testbench on buggy code. Record all PASS/FAIL results.
  - **2.2 Batch Fix** — Apply ALL high-confidence fixes from Phase 1 at once. Run testbench.
  - **2.3 Failure Analysis** — For remaining failures: read expected vs actual, identify failing signal.
  - **3.1 Backward Trace** — From the failing output signal, trace backward through its drivers to locate where the value diverges from the expected behavior. Step 1 - Identify th...
  - **3.2 Hypothesis** — Map the divergence to a specific bug category.
  - **3.3 Targeted Fix** — Fix only the identified bug.
**Keywords:** F1, F10, I1, I4, S1, S9, actual, against, always, analysis, apply, backward, batch, behavior, blocks, buggy, bugs, categories, category, check
**Retrieve when:** Always include on iteration 1. Include on later iterations when the same bug persists across multiple rounds.

## hardware_specs/

### `hardware_specs/module_templates.md`
**Sections:** Correct Verilog Module Templates, Combinational Logic Module, Sequential Logic Module (Async Reset), FSM Template (Two-Process Style), Shift Register Template, Parameterized Counter Template
**Keywords:** async, combinational, correct, input, logic, module, parameter, process, register, reset, sequential, shift, style, template, templates, verilog, width, wire
**Retrieve when:** Module structure looks wrong, instantiation errors (I1-I4), FSM or counter behavior incorrect, need correct template to compare against.

### `hardware_specs/synthesis_constraints.md`
**Sections:** Synthesis Constraints and Best Practices, Synthesizable vs Non-Synthesizable Constructs, Clock Domain Crossing, Reset Design, Latch Avoidance Rules, Timing Considerations, Resource Mapping
**Entries:**
  - **Synthesizable** — - `always @(posedge clk)`, `always @(*)` - `assign` continuous assignments - `if/else`, `case/casez/casex`
  - **Non-Synthesizable (Simulation Only)** — - `initial` blocks - `#delay` time delays - `$display`, `$monitor`, `$finish`, etc.
  - **Synchronous Reset** — always @(posedge clk) begin if (rst) q <= 0; else     q <= d;
  - **Asynchronous Reset** — always @(posedge clk or posedge rst) begin if (rst) q <= 0; else     q <= d;
**Keywords:** always, assign, assignments, asynchronous, avoidance, begin, best, between, blocks, clock, combinational, considerations, constraints, constructs, continuous, critical, crossing, delay, delays, design
**Retrieve when:** Synthesis warnings, timing failures, latch inferred, combinational loop detected, clock constraint issues.

### `hardware_specs/verilog_language_reference.md`
**Sections:** Verilog Language Reference — Key Constructs, Module Declaration, Data Types, Bit Widths and Literals, Always Blocks, Sensitivity Lists, Case Statements, Continuous Assignments, Operators, Common Synthesizable Patterns, Generate Statements, Module Instantiation, Testbench Constructs (Simulation Only)
**Entries:**
  - **Combinational Logic** — always @(*) begin // use blocking assignments (=) end
  - **Sequential Logic (Flip-Flops)** — always @(posedge clk or posedge rst) begin // use non-blocking assignments (<=) end
  - **Rules** — - **Blocking (`=`)**: Executes sequentially. Use in combinational always blocks. - **Non-blocking (`<=`)**: Schedules update at end of time step. Use ...
  - **Multiplexer** — assign out = sel ? a : b;
  - **Decoder** — always @(*) begin out = 4'b0000; case (sel)
  - **Counter** — always @(posedge clk or posedge rst) begin if (rst) count <= 0; else     count <= count + 1;
  - **Named Port Connection (preferred)** — mod_name instance_name ( .port_a  (signal_a), .port_b  (signal_b),
  - **Positional Port Connection (error-prone)** — mod_name instance_name (signal_a, signal_b, signal_out); // Order MUST match module definition exactly
  - **Parameterized Instantiation** — mod_name #(.WIDTH(8), .DEPTH(16)) instance_name ( .clk(clk), .data(data) );
  - **Common Instantiation Errors** — | Error | Example | Fix | |-------|---------|-----| | Module name typo | `mdo u1(...)` when module is `mod` | Spell module name exactly |
**Keywords:** always, arithmetic, assign, assignment, assignments, automatic, begin, blocking, blocks, case, category, codes, combinational, common, connection, constructs, continuous, count, counter, data
**Retrieve when:** Type errors, wire/reg assignment issues, sensitivity list warnings, operator or precedence issues, bit-width mismatches.