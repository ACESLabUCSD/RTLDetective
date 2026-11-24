// 8-bit Shift Register with Parallel Load
module shift_register (
    input wire clk,
    input wire rst,
    input wire load,
    input wire shift_en,
    input wire serial_in,
    input wire [7:0] parallel_in,
    output wire [7:0] data_out,
    output wire serial_out
);

    reg [7:0] shift_reg;

    assign data_out = shift_reg;
    assign serial_out = shift_reg[0];

    always @(posedge clk) begin
        if (rst)
            shift_reg <= 8'b0;
        else if (load)
            shift_reg <= parallel_in;
        else if (shift_en)
            shift_reg <= {shift_reg[6:0], serial_in};
    end

    wire [3:0] debug_nibble;
    assign debug_nibble = shift_reg;

endmodule
