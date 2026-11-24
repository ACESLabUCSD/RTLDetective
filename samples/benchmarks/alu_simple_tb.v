`timescale 1ns/1ps
module alu_simple_tb;

    reg  [7:0] a, b;
    reg  [1:0] op;
    wire [7:0] result;
    wire       zero_flag;
    integer pass_count = 0;
    integer fail_count = 0;

    alu_simple uut (.a(a), .b(b), .op(op), .result(result), .zero_flag(zero_flag));

    task check_result(input [7:0] expected, input [79:0] label);
        begin
            #1;
            if (result === expected) begin
                $display("[PASS] %0s: result=%0d (expected %0d)", label, result, expected);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: result=%0d (expected %0d)", label, result, expected);
                fail_count = fail_count + 1;
            end
        end
    endtask

    task check_zero(input expected_zf, input [79:0] label);
        begin
            #1;
            if (zero_flag === expected_zf) begin
                $display("[PASS] %0s: zero_flag=%0b (expected %0b)", label, zero_flag, expected_zf);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: zero_flag=%0b (expected %0b)", label, zero_flag, expected_zf);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("alu_simple.vcd");
        $dumpvars(0, alu_simple_tb);

        // TC1: Addition
        a = 8'd10; b = 8'd20; op = 2'b00; #10;
        check_result(8'd30, "TC1_add");

        // TC2: Subtraction
        a = 8'd50; b = 8'd30; op = 2'b01; #10;
        check_result(8'd20, "TC2_sub");

        // TC3: Bitwise AND
        a = 8'hF0; b = 8'h3C; op = 2'b10; #10;
        check_result(8'h30, "TC3_and");

        // TC4: Bitwise OR (op=2'b11)
        a = 8'hF0; b = 8'h0F; op = 2'b11; #10;
        check_result(8'hFF, "TC4_or");

        // TC5: Addition resulting in zero
        a = 8'd0; b = 8'd0; op = 2'b00; #10;
        check_result(8'd0, "TC5_add_zero");

        // TC6: Zero flag should be 1 when result is 0
        check_zero(1'b1, "TC6_zflag_1");

        // TC7: Zero flag should be 0 when result is not 0
        a = 8'd1; b = 8'd2; op = 2'b00; #10;
        check_zero(1'b0, "TC7_zflag_0");

        // TC8: Subtraction to zero
        a = 8'd42; b = 8'd42; op = 2'b01; #10;
        check_result(8'd0, "TC8_sub_zero");
        check_zero(1'b1, "TC9_subz_fl");

        // TC10: AND producing zero
        a = 8'hF0; b = 8'h0F; op = 2'b10; #10;
        check_result(8'd0, "TC10_and_z");

        // TC11: Zero flag after AND zero
        check_zero(1'b1, "TC11_andz_fl");

        // TC12: OR never zero when inputs non-zero
        a = 8'd1; b = 8'd0; op = 2'b11; #10;
        check_zero(1'b0, "TC12_or_nz");

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
