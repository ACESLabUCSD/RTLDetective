`timescale 1ns/1ps
module fsm_traffic_tb;

    reg clk, rst, sensor;
    wire [2:0] light;
    integer pass_count = 0;
    integer fail_count = 0;

    fsm_traffic uut (.clk(clk), .rst(rst), .sensor(sensor), .light(light));

    initial clk = 0;
    always #5 clk = ~clk;

    task check_light(input [2:0] expected, input [79:0] label);
        begin
            if (light === expected) begin
                $display("[PASS] %0s: light=%b (expected %b)", label, light, expected);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: light=%b (expected %b)", label, light, expected);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("fsm_traffic.vcd");
        $dumpvars(0, fsm_traffic_tb);

        // TC1: Reset goes to RED (light=100)
        rst = 1; sensor = 0;
        @(posedge clk); @(posedge clk); #1;
        check_light(3'b100, "TC1_reset_red");

        // TC2: Stay RED without sensor
        rst = 0;
        repeat(5) @(posedge clk);
        #1;
        check_light(3'b100, "TC2_stay_red");

        // TC3: Light output has no X bits
        if (^light === 1'bx) begin
            $display("[FAIL] TC3_no_x: light has X values: %b", light);
            fail_count = fail_count + 1;
        end else begin
            $display("[PASS] TC3_no_x: light has no X values");
            pass_count = pass_count + 1;
        end

        // TC4: Sensor eventually causes transition to GREEN (light=001)
        sensor = 1;
        begin : find_green
            integer i;
            reg found;
            found = 0;
            for (i = 0; i < 30; i = i + 1) begin
                @(posedge clk); #1;
                if (light == 3'b001) begin
                    found = 1;
                    disable find_green;
                end
            end
        end
        if (light == 3'b001) begin
            $display("[PASS] TC4_to_green: light=001 (GREEN)");
            pass_count = pass_count + 1;
        end else begin
            $display("[FAIL] TC4_to_green: light=%b (expected 001)", light);
            fail_count = fail_count + 1;
        end

        // TC5: GREEN transitions to YELLOW (light=010)
        begin : find_yellow
            integer i;
            reg found;
            found = 0;
            for (i = 0; i < 20; i = i + 1) begin
                @(posedge clk); #1;
                if (light == 3'b010) begin
                    found = 1;
                    disable find_yellow;
                end
            end
        end
        if (light == 3'b010) begin
            $display("[PASS] TC5_to_yellow: light=010 (YELLOW)");
            pass_count = pass_count + 1;
        end else begin
            $display("[FAIL] TC5_to_yellow: light=%b (expected 010)", light);
            fail_count = fail_count + 1;
        end

        // TC6: YELLOW transitions back to RED (light=100)
        begin : find_red
            integer i;
            reg found;
            found = 0;
            for (i = 0; i < 20; i = i + 1) begin
                @(posedge clk); #1;
                if (light == 3'b100) begin
                    found = 1;
                    disable find_red;
                end
            end
        end
        if (light == 3'b100) begin
            $display("[PASS] TC6_back_red: light=100 (RED)");
            pass_count = pass_count + 1;
        end else begin
            $display("[FAIL] TC6_back_red: light=%b (expected 100)", light);
            fail_count = fail_count + 1;
        end

        // TC7: Outputs are one-hot (only one of R/Y/G active at any time)
        begin : onehot_check
            integer i;
            reg onehot_ok;
            onehot_ok = 1;
            for (i = 0; i < 40; i = i + 1) begin
                @(posedge clk); #1;
                if (light != 3'b100 && light != 3'b010 && light != 3'b001) begin
                    onehot_ok = 0;
                end
            end
            if (onehot_ok) begin
                $display("[PASS] TC7_onehot: all outputs are valid one-hot");
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] TC7_onehot: invalid light encoding detected");
                fail_count = fail_count + 1;
            end
        end

        // TC8: Reset from any state returns to RED
        repeat(8) @(posedge clk);
        rst = 1;
        @(posedge clk); @(posedge clk); #1;
        check_light(3'b100, "TC8_rst_any");

        $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
                 pass_count, fail_count, pass_count + fail_count);
        $finish;
    end

    initial begin
        #20000;
        $display("[FAIL] TIMEOUT");
        $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
                 pass_count, fail_count + 1, pass_count + fail_count + 1);
        $finish;
    end
endmodule
