`timescale 1ns / 1ps
`include "../rtl/inc.v"

module test_point_add;

    // Inputs
    reg clk;
    reg reset;
    reg [`WIDTH:0] x1, y1, x2, y2;
    reg zero1, zero2;

    // Outputs
    wire done;
    wire zero3;
    wire [`WIDTH:0] x3, y3;

    // Constants
    reg [`WIDTH:0] p1x, p1y, np1y, p2x, p2y, np2y;

    integer pass_count = 0;
    integer fail_count = 0;

    // Instantiate UUT
    point_add uut (
        .clk(clk), .reset(reset),
        .x1(x1), .y1(y1), .zero1(zero1),
        .x2(x2), .y2(y2), .zero2(zero2),
        .done(done),
        .x3(x3), .y3(y3), .zero3(zero3)
    );

    always #5 clk = ~clk;

    task go;
        begin
            @(negedge clk); reset = 1;
            @(negedge clk); reset = 0;
            @(posedge done);
        end
    endtask

    task check_inf(input [127:0] label);
        begin
            #5;
            if (zero3 === 1) begin
                $display("[PASS] %0s: result is infinity point (zero3=1)", label);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: expected infinity point (zero3=1), got zero3=%0b x3=%h y3=%h",
                         label, zero3, x3, y3);
                fail_count = fail_count + 1;
            end
        end
    endtask

    task check_point(
        input [`WIDTH:0] exp_x, exp_y,
        input [127:0] label
    );
        begin
            #5;
            if (zero3 === 0 && x3 === exp_x && y3 === exp_y) begin
                $display("[PASS] %0s: x3=%h y3=%h", label, x3, y3);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s:", label);
                $display("       got    zero3=%0b x3=%h y3=%h", zero3, x3, y3);
                $display("       expect zero3=0  x3=%h y3=%h", exp_x, exp_y);
                fail_count = fail_count + 1;
            end
        end
    endtask

    task check_passthrough(
        input [`WIDTH:0] exp_x, exp_y,
        input [127:0] label
    );
        begin
            #5;
            if (zero3 === 0 && x3 === exp_x && y3 === exp_y) begin
                $display("[PASS] %0s: x3=%h y3=%h (passthrough correct)", label, x3, y3);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s:", label);
                $display("       got    zero3=%0b x3=%h y3=%h", zero3, x3, y3);
                $display("       expect zero3=0  x3=%h y3=%h", exp_x, exp_y);
                fail_count = fail_count + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("test_point_add.vcd");
        $dumpvars(0, test_point_add);

        // Initialise
        clk = 0; reset = 0;
        x1 = 0; y1 = 0; zero1 = 0;
        x2 = 0; y2 = 0; zero2 = 0;
        #100;

        // Load constants
        p1x  = 194'h154594219a60a610649861a602548666509898492a8049;
        p1y  = 194'h9a5a89a26aa5a1189680a6a64080a519a5054a11a9208094;
        np1y = 194'h65a54651955a52246940595980405a265a0a852256104068;
        p2x  = 194'h109489806019280a602169554246868a518a6102854294968;
        p2y  = 194'h94995581208995898a04995a50901a6a60421902a21a966a;
        np2y = 194'h6866aa4210466a46450866a5a06025959081260151256995;

        // ── TC01: Both points are infinity => result is infinity ──────────
        x1 = p1x; y1 = p1y; zero1 = 1;
        x2 = p2x; y2 = p2y; zero2 = 1;
        go;
        check_inf("TC01_both_inf");

        // ── TC02: P1 finite, P2 infinity => result = P1 ──────────────────
        x1 = p1x; y1 = p1y; zero1 = 0;
        x2 = p2x; y2 = p2y; zero2 = 1;
        go;
        check_passthrough(p1x, p1y, "TC02_p2_inf_returns_p1");

        // ── TC03: P1 infinity, P2 finite => result = P2 ──────────────────
        x1 = p1x; y1 = p1y; zero1 = 1;
        x2 = p2x; y2 = p2y; zero2 = 0;
        go;
        check_passthrough(p2x, p2y, "TC03_p1_inf_returns_p2");

        // ── TC04: P1 == -P2 (negation of p1y) => result is infinity ──────
        x1 = p1x; y1 = p1y;  zero1 = 0;
        x2 = p1x; y2 = np1y; zero2 = 0;
        go;
        check_inf("TC04_p1_neg_p2_inf");

        // ── TC05: P1 == -P2 (negation of p2y) => result is infinity ──────
        x1 = p2x; y1 = p2y;  zero1 = 0;
        x2 = p2x; y2 = np2y; zero2 = 0;
        go;
        check_inf("TC05_p2_neg_p1_inf");

        // ── TC06: P1 == P2 (point doubling, pair 1) ───────────────────────
        x1 = p1x; y1 = p1y; zero1 = 0;
        x2 = p1x; y2 = p1y; zero2 = 0;
        go;
        check_point(
            194'h51a80aa6548495816a6015424a209489998160946485920a,
            194'h18828584561659888a26269240125594996068145915145,
            "TC06_double_p1"
        );

        // ── TC07: P1 == P2 (point doubling, pair 2) ───────────────────────
        x1 = p2x; y1 = p2y; zero1 = 0;
        x2 = p2x; y2 = p2y; zero2 = 0;
        go;
        check_point(
            194'h2805051564005642629524a84a6159a605024615a90a62042,
            194'h2502568a5aa504152460984aa699616901895100a595862a8,
            "TC07_double_p2"
        );

        // ── TC08: P1 == P2 (point doubling, pair 3) ───────────────────────
        x1 = 194'h126569286a9860859046680265109015266416aa984082610;
        y1 = 194'h2a41880890628944a6844a269258216041061196854181160;
        zero1 = 0;
        x2 = x1; y2 = y1; zero2 = 0;
        go;
        check_point(
            194'h68060682a5016661a990165691662666126691485920a940,
            194'h1a428568aa082410a482244a642905a015582a945860a8898,
            "TC08_double_p3"
        );

        // ── TC09: P1 != ±P2, general addition (pair 1) ───────────────────
        x1 = p1x; y1 = p1y; zero1 = 0;
        x2 = p2x; y2 = p2y; zero2 = 0;
        go;
        check_point(
            194'ha629964882665246a929a19808a94825948aa499250110a,
            194'h920546a8540695a10010a95485a848684a51a864656a82,
            "TC09_add_p1_p2"
        );

        // ── TC10: P1 != ±P2, general addition (pair 2) ───────────────────
        x1 = 194'h2405a1946a466242911520254a852988898292a4069969259;
        y1 = 194'ha4568a88466646a4a86925162822a6621aa88aa85916089a;
        zero1 = 0;
        x2 = 194'h10545a2861a44529a24458448958295561a012412846a9259;
        y2 = 194'h2066580628a590a248451a6994956a142a42a5a010840229a;
        zero2 = 0;
        go;
        check_point(
            194'h5820222101a8668a69a4492258246242545104498588a4a8,
            194'h9892492664444861200a582a998010901105a90556429005,
            "TC10_add_arb_points"
        );

        $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
                 pass_count, fail_count, pass_count + fail_count);
        $finish;
    end

    initial begin
        #500000;
        $display("[FAIL] TIMEOUT");
        $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
                 pass_count, fail_count + 1, pass_count + fail_count + 1);
        $finish;
    end

endmodule