# Synthesis Constraints and Best Practices

## Synthesizable vs Non-Synthesizable Constructs

### Synthesizable
- `always @(posedge clk)`, `always @(*)`
- `assign` continuous assignments
- `if/else`, `case/casez/casex`
- Arithmetic: `+`, `-`, `*` (careful with multiply — uses DSP)
- Logical and bitwise operators
- `generate` blocks
- Module instantiation

### Non-Synthesizable (Simulation Only)
- `initial` blocks
- `#delay` time delays
- `$display`, `$monitor`, `$finish`, etc.
- `fork/join`
- `real` data type
- File I/O: `$fopen`, `$fread`, etc.

## Clock Domain Crossing
- Signals crossing clock domains must be synchronized (double-flop minimum)
- Use FIFOs or handshake protocols for multi-bit transfers
- Gray code for counters crossing domains

## Reset Design
### Synchronous Reset
```verilog
always @(posedge clk) begin
    if (rst) q <= 0;
    else     q <= d;
end
```
- Pro: Cleaner timing, no reset recovery issues
- Con: Requires active clock to reset

### Asynchronous Reset
```verilog
always @(posedge clk or posedge rst) begin
    if (rst) q <= 0;
    else     q <= d;
end
```
- Pro: Works without clock
- Con: Reset release must be synchronized to avoid metastability

## Latch Avoidance Rules
1. Every `if` must have an `else` in combinational blocks
2. Every `case` must have a `default`
3. Every signal assigned in a combinational block must be assigned in ALL branches
4. Pre-assign default values at the top of combinational always blocks

## Timing Considerations
- Critical path: longest combinational delay between two flip-flops
- Pipeline stages break long combinational paths
- Register outputs before driving off-chip
- Avoid asynchronous feedback loops

## Resource Mapping
| Verilog Construct | Hardware Resource       |
|-------------------|------------------------|
| `reg` in `always @(posedge)` | Flip-flop |
| `reg` in `always @(*)` without full assignment | Latch (BAD) |
| `wire` with `assign` | Combinational logic |
| `case` | Multiplexer or LUT |
| `+`, `-` | Adder/Subtractor |
| `*` | DSP block or LUT array |
| Large `case` | ROM / LUT-based |
