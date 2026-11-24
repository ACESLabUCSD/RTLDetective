# Verilog Language Reference — Key Constructs

## Module Declaration
```verilog
module name (port_list);
  // port declarations, internal signals, logic
endmodule
```

## Data Types
| Type       | Description                              |
|------------|------------------------------------------|
| `wire`     | Continuous assignment, combinational net  |
| `reg`      | Procedural assignment target (not always a register) |
| `integer`  | 32-bit signed variable                   |
| `parameter`| Named constant                           |
| `localparam`| Non-overridable parameter               |

## Bit Widths and Literals
- Format: `<width>'<radix><value>` — e.g., `8'hFF`, `4'b1010`, `3'd5`
- Radix codes: `b` (binary), `o` (octal), `d` (decimal), `h` (hex)
- Signed literals: `8'sd5` (signed decimal)

## Always Blocks
### Combinational Logic
```verilog
always @(*) begin
    // use blocking assignments (=)
end
```

### Sequential Logic (Flip-Flops)
```verilog
always @(posedge clk or posedge rst) begin
    // use non-blocking assignments (<=)
end
```

### Rules
- **Blocking (`=`)**: Executes sequentially. Use in combinational always blocks.
- **Non-blocking (`<=`)**: Schedules update at end of time step. Use in sequential always blocks.
- **Never mix** blocking and non-blocking for the same signal.

## Sensitivity Lists
- `always @(*)` — automatic, infers all RHS signals (combinational)
- `always @(posedge clk)` — edge-triggered (sequential)
- `always @(posedge clk or posedge rst)` — edge-triggered with async reset

## Case Statements
```verilog
case (selector)
    value1: statement1;
    value2: statement2;
    default: default_statement;  // ALWAYS include default
endcase
```
- `casez` — treats `z` as don't-care
- `casex` — treats `x` and `z` as don't-care

## Continuous Assignments
```verilog
assign wire_name = expression;
```
- Used outside always blocks for combinational logic.
- Cannot drive `reg` types.

## Operators
| Category    | Operators                            |
|-------------|--------------------------------------|
| Arithmetic  | `+`, `-`, `*`, `/`, `%`             |
| Logical     | `&&`, `\|\|`, `!`                   |
| Bitwise     | `&`, `\|`, `^`, `~`                 |
| Relational  | `==`, `!=`, `<`, `>`, `<=`, `>=`    |
| Shift       | `<<`, `>>`, `<<<`, `>>>`            |
| Concatenate | `{a, b}`, `{4{a}}` (replication)    |
| Conditional | `cond ? true_expr : false_expr`      |

## Common Synthesizable Patterns
### Multiplexer
```verilog
assign out = sel ? a : b;
```
### Decoder
```verilog
always @(*) begin
    out = 4'b0000;
    case (sel)
        2'b00: out[0] = 1'b1;
        2'b01: out[1] = 1'b1;
        2'b10: out[2] = 1'b1;
        2'b11: out[3] = 1'b1;
    endcase
end
```
### Counter
```verilog
always @(posedge clk or posedge rst) begin
    if (rst) count <= 0;
    else     count <= count + 1;
end
```

## Generate Statements
```verilog
genvar i;
generate
    for (i = 0; i < N; i = i + 1) begin : gen_block
        // instantiate modules or assign statements
    end
endgenerate
```

## Module Instantiation

### Named Port Connection (preferred)
```verilog
mod_name instance_name (
    .port_a  (signal_a),
    .port_b  (signal_b),
    .port_out(signal_out)
);
```

### Positional Port Connection (error-prone)
```verilog
mod_name instance_name (signal_a, signal_b, signal_out);
// Order MUST match module definition exactly
```

### Parameterized Instantiation
```verilog
mod_name #(.WIDTH(8), .DEPTH(16)) instance_name (
    .clk(clk), .data(data)
);
```

### Common Instantiation Errors
| Error | Example | Fix |
|-------|---------|-----|
| Module name typo | `mdo u1(...)` when module is `mod` | Spell module name exactly |
| Missing port | `mod u1(.a(x), .b(y))` but `c` exists | Connect all ports or use `.c()` |
| Port name typo | `.din(d)` when port is `data_in` | Use exact port names |
| Positional order wrong | Ports swapped in positional style | Use named ports instead |
| Width mismatch at port | 8-bit signal on 16-bit port | Match widths explicitly |
| Parameter not propagated | Default param wrong for this use | Override with `#(.PARAM(val))` |

## Testbench Constructs (Simulation Only)
```verilog
initial begin
    // runs once at time 0
    #10 signal = 1;
    #20 signal = 0;
end

// Clock generation
always #5 clk = ~clk;
```
