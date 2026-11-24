`timescale 1ns/1ps
module tb;

    reg  [2:0] A, B;
    reg        Cin;
    wire [2:0] S;
    wire       Cout;

    integer pass_count = 0;
    integer fail_count = 0;

    carry_lookahead_adder #(.N(3)) DUT (
        .A(A), .B(B), .Cin(Cin),
        .S(S), .Cout(Cout)
    );

    task load(input [2:0] a, b, input c);
        begin
            A = a; B = b; Cin = c;
        end
    endtask

    task check_result(input [2:0] exp_s, input exp_cout, input [127:0] label);
        begin
            #1;
            if (S === exp_s && Cout === exp_cout) begin
                $display("[PASS] %0s: A=%0d B=%0d Cin=%0b => Sum=%0d Cout=%0b (expected Sum=%0d Cout=%0b)",
                         label, A, B, Cin, S, Cout, exp_s, exp_cout);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: A=%0d B=%0d Cin=%0b => Sum=%0d Cout=%0b (expected Sum=%0d Cout=%0b)",
                         label, A, B, Cin, S, Cout, exp_s, exp_cout);
                fail_count = fail_count + 1;
            end
        end
    endtask

    // Compute expected {Cout, S} = A + B + Cin
    // Using 4-bit arithmetic to capture carry out
    function [3:0] expected_add;
        input [2:0] a, b;
        input       cin;
        begin
            expected_add = {1'b0, a} + {1'b0, b} + cin;
        end
    endfunction

    integer i, j, k;
    reg [3:0] exp;

    initial begin
        $dumpfile("carry_lookahead_adder.vcd");
        $dumpvars(0, tb);

        // ── Directed tests ──────────────────────────────────────────────

        // TC1: 0 + 0 + 0 = 0, no carry
        load(3'd0, 3'd0, 1'b0); #10;
        check_result(3'd0, 1'b0, "TC01_zero_add");

        // TC2: all ones + 0 + 0 = 7, no carry
        load(3'd7, 3'd0, 1'b0); #10;
        check_result(3'd7, 1'b0, "TC02_max_plus_zero");

        // TC3: max + max, no carry-in => 14 => Sum=6, Cout=1
        load(3'd7, 3'd7, 1'b0); #10;
        check_result(3'd6, 1'b1, "TC03_max_overflow");

        // TC4: max + max + Cin=1 => 15 => Sum=7, Cout=1
        load(3'd7, 3'd7, 1'b1); #10;
        check_result(3'd7, 1'b1, "TC04_max_overflow_cin");

        // TC5: carry-in propagation  3 + 4 + 1 = 8 => Sum=0, Cout=1
        load(3'd3, 3'd4, 1'b1); #10;
        check_result(3'd0, 1'b1, "TC05_cin_propagate");

        // TC6: carry-in no overflow  2 + 3 + 1 = 6
        load(3'd2, 3'd3, 1'b1); #10;
        check_result(3'd6, 1'b0, "TC06_cin_no_overflow");

        // TC7: 1 + 0 + 0 = 1
        load(3'd1, 3'd0, 1'b0); #10;
        check_result(3'd1, 1'b0, "TC07_one_plus_zero");

        // TC8: 0 + 1 + 1 = 2
        load(3'd0, 3'd1, 1'b1); #10;
        check_result(3'd2, 1'b0, "TC08_zero_plus_one_cin");

        // ── Exhaustive sweep (all 128 input combinations) ────────────────
        $display("-- Exhaustive sweep start --");
        for (k = 0; k < 2; k = k + 1) begin
            for (i = 0; i < 8; i = i + 1) begin
                for (j = 0; j < 8; j = j + 1) begin
                    load(i, j, k); #10;
                    exp = expected_add(i, j, k);
                    if (S === exp[2:0] && Cout === exp[3]) begin
                        pass_count = pass_count + 1;
                    end else begin
                        $display("[FAIL] Exhaustive: A=%0d B=%0d Cin=%0b => Sum=%0d Cout=%0b (expected Sum=%0d Cout=%0b)",
                                 A, B, Cin, S, Cout, exp[2:0], exp[3]);
                        fail_count = fail_count + 1;
                    end
                end
            end
        end
        $display("-- Exhaustive sweep end --");

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