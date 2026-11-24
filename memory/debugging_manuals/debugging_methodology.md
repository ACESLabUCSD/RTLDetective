# Verilog Debugging Methodology

## Philosophy: Reason First, Simulate Second

Maximize bugs found through LLM reasoning (free) before running testbenches (costly).
Target: 1-2 testbench iterations total.

## Preservation Rule
Do not modify expressions inside loop bodies unless they directly correspond
to a known bug pattern with a compiler or simulation error pointing to that line.
Unfamiliar arithmetic in loop bodies is likely intentional algorithm logic.

## Phase 1: LLM-Only Reasoning (zero cost)

### 1.1 Intent Recovery
- Infer expected behavior from module name, port names, comments, signal names
- Determine design type: combinational, sequential, FSM, datapath

### 1.2 Structural Analysis
- Decompose into: ports, signals, always blocks, assigns, instantiations
- For each always block: is it combinational or sequential?
- For each signal: who drives it? Is it driven exactly once?

### 1.3 Systematic 23-Category Scan
Check ALL categories from common_bug_patterns.md (S1-S9, F1-F10, I1-I4):

**Syntax Checklist (S1-S9):**
- [ ] No missing punctuation (semicolons, commas, parentheses)
- [ ] All signal names spelled correctly and declared
- [ ] No = where == intended in conditions
- [ ] No duplicate declarations
- [ ] No non-ASCII characters
- [ ] Wire/reg types match assignment context
- [ ] All ports have direction declared
- [ ] All bit-selects within declared range
- [ ] No reserved keywords as identifiers
- [ ] Module definitions have proper endmodule termination
- [ ] All begin blocks have matching end

**Function Checklist (F1-F10):**
- [ ] Blocking (=) only in combinational, non-blocking (<=) only in sequential
- [ ] Sensitivity lists complete, reset edges match polarity
- [ ] All case have default, all if-else cover all signals
- [ ] No signal driven by multiple always blocks
- [ ] Bit widths sufficient for all operations
- [ ] Operators correct, precedence explicit with parentheses
- [ ] All literals have explicit width and radix
- [ ] All registers reset with correct polarity
- [ ] No combinational feedback loops
- [ ] No infinite loops or FSM deadlocks
- [ ] Wrong FSM states or conditions
- [ ] Wrong FSM assignments

**Instantiation Checklist (I1-I4):**
- [ ] Module names spelled correctly
- [ ] All ports connected
- [ ] Port names match module definition
- [ ] Port order correct (or using named connections)

### 1.4 Template Comparison
Compare code structure against correct templates in module_templates.md.

## Phase 2: Testbench Validation (first iteration)

### 2.1 Run Original
Run testbench on buggy code. Record all PASS/FAIL results.

### 2.2 Batch Fix
Apply ALL high-confidence fixes from Phase 1 at once. Run testbench.

### 2.3 Failure Analysis
For remaining failures: read expected vs actual, identify failing signal.

## Phase 3: Failure-Driven Localization (reasoning, not simulation)

### 3.1 Backward Trace
From the failing output signal, trace backward through its drivers to locate where the value diverges from the expected behavior.
  Step 1 - Identify the first failing output signal from the testbench results. 
  Step 2 - Find the signal(s) that drive this output in the RTL.
  Step 3 - Add $display or $monitor statements in the testbench to print the values of the failing signal and its driver signals.
  Example: $display("t=%0t y=%0d alu_out=%0d a=%0d b=%0d", $time, y, alu_out, a, b);
  Step 4 - Run the simulation again and observe the printed values.

  Step 5 - Determine whether the driver signal already contains the incorrect value.

      If the driver is correct → the bug is between the driver and the output.

      If the driver is incorrect → trace one step further back to the signals driving that driver.

  Step 6 - Repeat this process recursively from Step 3 till the first signal that becomes incorrect indicates the location of the bug.: Print the driver. Then the driver of the driver. Continue until the first incorrect signal is found. 

### 3.2 Hypothesis
Map the divergence to a specific bug category.

### 3.3 Targeted Fix
Fix only the identified bug.

## Phase 4: Final Validation

Run testbench. If 100% pass: done. If not: repeat Phase 3.

## Key Metrics
- Bugs found by reasoning (Phase 1) vs by testbench (Phase 2+)
- Number of LLM-testbench iterations
- Final testbench pass rate
