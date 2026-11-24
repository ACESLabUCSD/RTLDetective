`timescale 1ns/1ps
module shift_register_tb;

    reg clk, rst, load, shift_en, serial_in;
    reg [7:0] parallel_in;
    wire [7:0] data_out;
    wire serial_out;
    integer pass_count = 0;
    integer fail_count = 0;

    shift_register uut (
        .clk(clk), .rst(rst), .load(load), .shift_en(shift_en),
        .serial_in(serial_in), .parallel_in(parallel_in),
        .data_out(data_out), .serial_out(serial_out)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    task check_data(input [7:0] expected, input [79:0] label);
        begin
            if (data_out === expected) begin
                $display("[PASS] %0s: data_out=%b (expected %b)", label, data_out, expected);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: data_out=%b (expected %b)", label, data_out, expected);
                fail_count = fail_count + 1;
            end
        end
    endtask

    task check_serial(input expected, input [79:0] label);
        begin
            if (serial_out === expected) begin
                $display("[PASS] %0s: serial_out=%b (expected %b)", label, serial_out, expected);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: serial_out=%b (expected %b)", label, serial_out, expected);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("shift_register.vcd");
        $dumpvars(0, shift_register_tb);

        // TC1: Reset clears register
        rst = 1; load = 0; shift_en = 0; serial_in = 0; parallel_in = 8'h00;
        @(posedge clk); @(posedge clk); #1;
        check_data(8'h00, "TC1_reset");

        // TC2: Parallel load
        rst = 0; load = 1; parallel_in = 8'hA5;
        @(posedge clk); #1;
        check_data(8'hA5, "TC2_load");

        // TC3: serial_out is MSB after load (should be 1 for 0xA5 = 10100101)
        load = 0;
        #1;
        check_serial(1'b1, "TC3_sout_msb");

        // TC4: Shift left one position with serial_in=0
        shift_en = 1; serial_in = 0;
        @(posedge clk); #1;
        // 0xA5 = 10100101 shifted left -> 01001010 = 0x4A
        check_data(8'h4A, "TC4_shift1");

        // TC5: serial_out after shift (new MSB = 0)
        check_serial(1'b0, "TC5_sout_0");

        // TC6: Shift with serial_in=1
        serial_in = 1;
        @(posedge clk); #1;
        // 0x4A = 01001010 shifted left with 1 -> 10010101 = 0x95
        check_data(8'h95, "TC6_shift_1");

        // TC7: serial_out should be MSB (1)
        check_serial(1'b1, "TC7_sout_1");

        // TC8: Hold when shift_en=0
        shift_en = 0; serial_in = 0;
        @(posedge clk); #1;
        check_data(8'h95, "TC8_hold");

        // TC9: Reset from loaded state
        rst = 1;
        @(posedge clk); #1;
        check_data(8'h00, "TC9_reset2");

        // TC10: Shift 8 bits of known serial data
        rst = 0; shift_en = 1;
        serial_in = 1; @(posedge clk); // bit 0 (LSB enters)
        serial_in = 0; @(posedge clk);
        serial_in = 1; @(posedge clk);
        serial_in = 1; @(posedge clk);
        serial_in = 0; @(posedge clk);
        serial_in = 0; @(posedge clk);
        serial_in = 1; @(posedge clk);
        serial_in = 0; @(posedge clk);
        #1;
        // Entered MSB-first: 10110010 = 0xB2... but shift is left, serial_in enters at LSB
        // Sequence entered at LSB: 1,0,1,1,0,0,1,0 → register = 10110010 = 0xB2
        check_data(8'hB2, "TC10_serial");

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
