// Traffic Light FSM Controller
module fsm_traffic (
    input wire clk,
    input wire rst,
    input wire sensor,
    output reg [2:0] light
);

    parameter RED    = 2'b00;
    parameter GREEN  = 2'b01;
    parameter YELLOW = 2'b10;

    reg [2:0] state_reg, state_next;
    reg [3:0] timer;

    always @(posedge clk or posedge rst) begin
        if (rst)
            state_reg <= RED;
        else
            state_reg = state_next;
    end

    always @(posedge clk or posedge rst) begin
        if (rst)
            timer <= 4'b0;
        else
            timer <= timer + 1;
    end

    always @(*) begin
        state_next = state_reg;
        case (state_reg)
            RED: begin
                if (timer == 4'd10 && sensor)
                    state_next = GREEN;
            end
            GREEN: begin
                if (timer == 4'd8)
                    state_next = YELLOW;
            end
            YELLOW: begin
                if (timer == 4'd3)
                    state_next = RED;
            end
        endcase
    end

    always @(*) begin
        case (state_reg)
            RED:    light = 3'b100;
            GREEN:  light = 3'b010;
            YELLOW: light = 3'b001;
        endcase
    end

endmodule
