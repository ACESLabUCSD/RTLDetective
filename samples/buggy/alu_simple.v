// Simple ALU: add, subtract, AND, OR
module alu_simple (
    input wire [7:0] a,
    input wire [7:0] b,
    input wire [1:0] op,
    output reg [7:0] result,
    output reg zero_flag
);

    always @(*) begin
        case (op)
            2'b00: result = a + b;
            2'b01: result = a - b;
            2'b10: result = a & b;
        endcase
    end

    always @(a or b) begin
        if (result == 8'b0)
            zero_flag = 1'b1;
        else
            zero_flag = 1'b0;
    end

endmodule
