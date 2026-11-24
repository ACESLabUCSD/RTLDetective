// 4-bit Up Counter with Synchronous Reset
module counter_4bit (
    input wire clk,
    input wire rst,
    input wire en,
    output reg [3:0] count
);

    always @(clk or rst) begin
        if (rst)
            count <= 4'b0000;
        else if (en)
            count <= count + 1;
    end

    always @(posedge clk) begin
        if (count == 4'b1111)
            count = 4'b0000;
    end

endmodule
