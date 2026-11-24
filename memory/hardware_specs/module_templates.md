# Correct Verilog Module Templates

## Combinational Logic Module
```verilog
module comb_example (
    input  wire [7:0] a,
    input  wire [7:0] b,
    input  wire [1:0] sel,
    output reg  [7:0] result
);
    always @(*) begin
        result = 8'b0;  // default to prevent latch
        case (sel)
            2'b00:   result = a + b;
            2'b01:   result = a - b;
            2'b10:   result = a & b;
            2'b11:   result = a | b;
            default: result = 8'b0;
        endcase
    end
endmodule
```

## Sequential Logic Module (Async Reset)
```verilog
module seq_example (
    input  wire       clk,
    input  wire       rst,
    input  wire       en,
    input  wire [7:0] d,
    output reg  [7:0] q
);
    always @(posedge clk or posedge rst) begin
        if (rst)
            q <= 8'b0;
        else if (en)
            q <= d;
    end
endmodule
```

## FSM Template (Two-Process Style)
```verilog
module fsm_template (
    input  wire clk,
    input  wire rst,
    input  wire go,
    output reg  done
);
    localparam IDLE = 2'b00,
               RUN  = 2'b01,
               DONE = 2'b10;

    reg [1:0] state, next_state;

    // State register (sequential)
    always @(posedge clk or posedge rst) begin
        if (rst)
            state <= IDLE;
        else
            state <= next_state;
    end

    // Next-state logic (combinational)
    always @(*) begin
        next_state = state;  // default: hold
        case (state)
            IDLE: if (go)  next_state = RUN;
            RUN:           next_state = DONE;
            DONE:          next_state = IDLE;
            default:       next_state = IDLE;
        endcase
    end

    // Output logic (combinational)
    always @(*) begin
        done = 1'b0;  // default
        case (state)
            DONE:    done = 1'b1;
            default: done = 1'b0;
        endcase
    end
endmodule
```

## Shift Register Template
```verilog
module shift_reg_template #(
    parameter WIDTH = 8
)(
    input  wire             clk,
    input  wire             rst,
    input  wire             shift_en,
    input  wire             serial_in,
    output wire             serial_out,
    output wire [WIDTH-1:0] parallel_out
);
    reg [WIDTH-1:0] sr;

    assign serial_out   = sr[WIDTH-1];  // MSB out for left shift
    assign parallel_out = sr;

    always @(posedge clk or posedge rst) begin
        if (rst)
            sr <= {WIDTH{1'b0}};
        else if (shift_en)
            sr <= {sr[WIDTH-2:0], serial_in};  // left shift
    end
endmodule
```

## Parameterized Counter Template
```verilog
module counter_template #(
    parameter WIDTH = 8,
    parameter MAX   = (1 << WIDTH) - 1
)(
    input  wire             clk,
    input  wire             rst,
    input  wire             en,
    output reg  [WIDTH-1:0] count,
    output wire             overflow
);
    assign overflow = (count == MAX[WIDTH-1:0]) && en;

    always @(posedge clk or posedge rst) begin
        if (rst)
            count <= {WIDTH{1'b0}};
        else if (en)
            count <= count + 1;
    end
endmodule
```
