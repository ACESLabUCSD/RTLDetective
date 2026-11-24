# Common Verilog Bug Patterns

This catalog covers all common Verilog error types organized into three categories:
Syntax Errors (S1-S9), Function Errors (F1-F10), and Instantiation Errors (I1-I4).

---

# SYNTAX ERRORS

## S1. Premature Termination

### Pattern
Missing or redundant punctuation: semicolons, commas, parentheses, or end/endmodule.

### Example
```verilog
module A(input a, output b)   // BUG: missing semicolon
module A(input a  output b);  // BUG: missing comma between ports
```

### Detection
- Compiler error pointing to unexpected token
- The error is usually on the line AFTER the missing token

### Fix
Add the missing punctuation.

---

## S2. Undefined Variable

### Pattern
Using variables not declared, often due to typos in signal names.

### Example
```verilog
assign result = temp;   // CORRECT
assign resultl = temp;  // BUG: typo, resultl not declared
```

### Detection
- Compiler: undeclared identifier
- Diff signal names on LHS vs declarations for single-char typos

### Fix
Correct the typo or add a missing wire/reg declaration.

---

## S3. Operator Misuse

### Pattern
Using assignment `=` where a comparison operator is intended, or using the wrong comparison operator entirely.

### Comparison Operators
`==` (equal), `!=` (not equal), `>` (greater), `<` (less), `>=` (greater or equal), `<=` (less or equal)

### Example
```verilog
if (a = 2'b10)   // BUG: assignment in condition
if (a == 2'b10)  // CORRECT if checking equality
if (a >= 2'b10)  // CORRECT if checking threshold
```

### Detection
- Any `=` (not `==`, `<=`, `>=`) inside an if/while/case condition

### Fix
Replace `=` with the contextually correct comparison operator. Read the surrounding
algorithm to determine which one: equality checks use `==`/`!=`, range/threshold
checks use `>`/`<`/`>=`/`<=`. Use the design description as ground truth.

---

## S4. Redundant Variable Declaration

### Pattern
Declaring the same variable multiple times, e.g. in port list AND as internal signal.

### Example
```verilog
module A(input a, output b);
    reg a;      // BUG: a already declared as port
```

### Detection
- Compiler: duplicate declaration
- Search for duplicate names in port list + internal declarations

### Fix
Remove the redundant declaration.

---

## S5. Incorrect Encoding

### Pattern
Non-ANSI or invisible Unicode characters in the source file.

### Detection
- Compiler errors on seemingly valid code
- Hex-dump file to find non-ASCII bytes

### Fix
Re-type affected identifiers in plain ASCII editor.

---

## S6. Incorrect Data Type Assignment

### Pattern
Violating wire/reg rules: assign on reg, or procedural assignment on wire.

### Example
```verilog
reg a;
assign a = b;         // BUG: cannot continuous-assign to reg

wire c;
always @(*) c = d;    // BUG: cannot procedurally assign to wire
```

### Detection
- Compiler: illegal reference to net/variable
- Check every assign LHS is wire, every always LHS is reg

### Fix
Change the type (wire vs reg) to match the assignment context.

---

## S7. Port Mode Declaration Error

### Pattern
Missing port direction declarations for some ports.

### Example
```verilog
module A(a, b);
    input a;
    // BUG: b direction never declared
```

### Detection
- Check every port has input/output/inout
- Use ANSI-style ports to prevent this

### Fix
Add missing direction or convert to ANSI-style ports.

---

## S8. Data Index Out-of-Bounds Error

### Pattern
Bit-select or part-select exceeds declared range.

### Example
```verilog
reg [32:1] a;
assign b = a[16:1];   // CORRECT
assign b = a[16:0];   // BUG: bit 0 out of range (declared [32:1])
```

### Detection
- Compare every bit-select against declared range

### Fix
Adjust bit-select to match declared range.

---

## S9. Improper Use of Keywords

### Pattern
Using Verilog reserved keywords as identifiers.

### Example
```verilog
reg always;    // BUG: reserved keyword
reg my_reg;    // CORRECT
```

### Fix
Rename to a non-reserved name.

---

# FUNCTION ERRORS

## F1. Misuse of Assignments (Blocking vs Non-Blocking)

### Pattern
Blocking (=) in sequential blocks, or non-blocking (<=) in combinational blocks.

### Example
```verilog
// BUG: blocking in sequential
always @(posedge clk) begin a = b + c; end
// CORRECT:
always @(posedge clk) begin a <= b + c; end
```

### Fix
Sequential: use <=. Combinational: use =.

---

## F2. Flawed Sensitivity List

### Pattern
Missing signals, or wrong edge specification for reset.

### Example
```verilog
// BUG: posedge rst_n should be negedge rst_n
always @(posedge clk or posedge rst_n)
// CORRECT:
always @(posedge clk or negedge rst_n)
```

### Fix
Use always @(*) for combinational. Match edge to polarity for sequential.

---

## F3. Incomplete Case / If-Else (Latch Inference)

### Pattern
Missing default in case, or if without else in combinational blocks.

### Symptoms
- Latch inferred warning
- Signal holds stale value

### Fix
Add default branch, or pre-assign defaults at block top.

---

## F4. Concurrent Variable Use (Multiple Drivers)

### Pattern
Same variable assigned in multiple always blocks.

### Detection
- Signal on LHS in multiple always/assign blocks
- Simulation shows x values

### Fix
Consolidate into single always block with mux logic.

---

## F5. Insufficient Bit Width

### Pattern
Register or wire too narrow, causing truncation or overflow.

### Example
```verilog
wire [3:1] a;
assign a = 4\'b1000;   // BUG: truncated
```

### Fix
Widen the signal to accommodate full range.

---

## F6. Logical Errors in Expressions

### Pattern
Wrong operator, wrong operand, or operator precedence error.

### Example
```verilog
assign a = b + c;   // BUG if intent was bitwise AND
assign a = b & c;   // CORRECT

// Precedence error:
assign z = result == 8\'b0 & carry == 1\'b0;   // BUG: & binds before ==
assign z = (result == 8\'b0) && (carry == 1\'b0); // CORRECT
```

### Fix
Replace with correct operator. Add parentheses.

---

## F7. Mismatched Assignment Values

### Pattern
Wrong or missing radix in literals causing wrong bit patterns.

### Example
```verilog
if (a == 2\'b10)   // CORRECT: binary
if (a == 10)      // BUG: decimal 10 != binary 10
```

### Fix
Add explicit width and radix: 2\'b10 not bare 10.

---

## F8. Missing Reset or Incorrect Reset Polarity

### Pattern
Registers missing from reset, or polarity mismatch.

### Example
```verilog
// BUG: negedge but checking (rst_n) instead of (!rst_n)
always @(posedge clk or negedge rst_n) begin
    if (rst_n) count <= 0;   // WRONG polarity
end
```

### Fix
Add missing regs to reset. Fix polarity match.

---

## F9. Asynchronous Feedback Loops

### Pattern
Combinational signal feeds back to itself without register.

### Symptoms
- Simulation hangs/oscillates
- Synthesis: combinational loop

### Fix
Insert register to break loop.

---

## F10. Infinite Loop Constructs

### Pattern
forever/while/for without termination, or FSM self-loop without exit.

### Example
```verilog
// BUG: FSM state stuck
next_stage <= next_stage_temp;   // if always equals current, stuck

while (flag) begin
    // BUG: flag never cleared
end
```

### Fix
Add termination condition. For FSMs, add exit transitions.

---

# INSTANTIATION ERRORS

## I1. Incorrect Module Name

### Pattern
Typo in module name during instantiation.

### Example
```verilog
// Defined: module mod(...)
mdo u1 (.a(x), .b(y));   // BUG: mdo should be mod
```

### Detection
- Compiler: unknown module type

### Fix
Correct the module name spelling.

---

## I2. Incomplete Port Connection

### Pattern
Not all ports connected during instantiation.

### Example
```verilog
// Module has ports a, b, c
mod u1 (.a(x), .b(y));   // BUG: port c not connected
```

### Detection
- Compiler warning: unconnected port

### Fix
Connect missing port or explicitly mark unconnected with .c().

---

## I3. Port Name Mismatch

### Pattern
Named port does not match module definition.

### Example
```verilog
// Module ports: data_in, data_out
mod u1 (.din(d), .data_out(o));  // BUG: din not data_in
```

### Detection
- Compiler: port not found

### Fix
Use exact port names from module definition.

---

## I4. Port Order Mismatch (Positional)

### Pattern
Positional port order does not match module definition.

### Example
```verilog
// Module: mod(input a, input b, output c)
mod u1 (sig_b, sig_a, sig_c);  // BUG: a and b swapped
```

### Detection
- No compiler error, just wrong behavior

### Fix
Use named ports to avoid this class of error entirely.
