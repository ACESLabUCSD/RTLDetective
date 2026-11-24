`timescale 1ns/1ps
module counter_4bit_tb;

    reg clk, rst, en;
    wire [3:0] count;
    integer pass_count = 0;
    integer fail_count = 0;

    counter_4bit uut (.clk(clk), .rst(rst), .en(en), .count(count));

    initial clk = 0;
    always #5 clk = ~clk;

    task check(input [3:0] expected, input [79:0] label);
        begin
            if (count === expected) begin
                $display("[PASS] %0s: count=%0d (expected %0d)", label, count, expected);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: count=%0d (expected %0d)", label, count, expected);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("counter_4bit.vcd");
        $dumpvars(0, counter_4bit_tb);

        // TC1: Reset
        rst = 1; en = 0;
        @(posedge clk); #1;
        check(4'd0, "TC1_reset");

        // TC2: Hold after reset deassert (en=0)
        rst = 0;
        @(posedge clk); #1;
        check(4'd0, "TC2_hold_no_en");

        // TC3: Count up when enabled
        en = 1;
        @(posedge clk); #1;
        check(4'd1, "TC3_count_1");

        // TC4: Continue counting
        @(posedge clk); #1;
        check(4'd2, "TC4_count_2");

        // TC5: Count several more
        repeat(3) @(posedge clk);
        #1;
        check(4'd5, "TC5_count_5");

        // TC6: Disable counting
        en = 0;
        @(posedge clk); #1;
        check(4'd5, "TC6_hold_dis");

        // TC7: Re-enable and count
        en = 1;
        @(posedge clk); #1;
        check(4'd6, "TC7_reenable");

        // TC8: Count to overflow (wrap from 15 to 0)
        en = 0; rst = 1;
        @(posedge clk); #1;
        rst = 0; en = 1;
        repeat(16) @(posedge clk);
        #1;
        check(4'd0, "TC8_wrap_0");

        // TC9: Verify wrap+1
        @(posedge clk); #1;
        check(4'd1, "TC9_wrap_1");

        // TC10: Mid-count reset
        repeat(5) @(posedge clk);
        rst = 1;
        @(posedge clk); #1;
        check(4'd0, "TC10_midrst");

        $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
                 pass_count, fail_count, pass_count + fail_count);
        $finish;
    end

    initial begin
        #5000;
        $display("[FAIL] TIMEOUT");
        $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
                 pass_count, fail_count + 1, pass_count + fail_count + 1);
        $finish;
    end
endmodule
