`timescale 1ns/1ns

module tb;
  
  reg clk;
  reg rst_n;
  reg pass_request;
  wire [7:0] clock;
  wire red;
  wire yellow;
  wire green;
  integer i;
  // Instantiate the module
  traffic_light uut (
    .clk(clk), 
    .rst_n(rst_n),
    .pass_request(pass_request), 
    .clock(clock), 
    .red(red),
    .yellow(yellow),
    .green(green)
  );
  
  // Clock generation
  always begin
    #5 clk = ~clk;
  end

  integer error = 0;
  integer clock_cnt;

  integer pass_count = 0;
  integer fail_count = 0;

  // Test sequence
  initial begin
    clk = 0;
    rst_n = 1;
    pass_request = 0;

    // Reset
    rst_n = 0;
    #10;

    // test 1: during reset
    if (clock==10 && red===0 && yellow===0 && green===0) begin
      $display("[PASS] t=10 reset: clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      pass_count = pass_count + 1;
    end else begin
      $display("[FAIL] t=10 reset: expected clock=10 red=0 yellow=0 green=0  got clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      fail_count = fail_count + 1;
    end

    rst_n = 1;
    #30;

    // test 2: red phase
    if (clock==10 && red==1 && yellow==0 && green==0) begin
      $display("[PASS] t=40 red: clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      pass_count = pass_count + 1;
    end else begin
      $display("[FAIL] t=40 red: expected clock=10 red=1 yellow=0 green=0  got clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      fail_count = fail_count + 1;
    end

    #100;

    // test 3: green phase
    if (clock==60 && red==0 && yellow==0 && green==1) begin
      $display("[PASS] t=140 green: clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      pass_count = pass_count + 1;
    end else begin
      $display("[FAIL] t=140 green: expected clock=60 red=0 yellow=0 green=1  got clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      fail_count = fail_count + 1;
    end

    #600;

    // test 4: yellow phase
    if (clock==5 && red==0 && yellow==1 && green==0) begin
      $display("[PASS] t=740 yellow: clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      pass_count = pass_count + 1;
    end else begin
      $display("[FAIL] t=740 yellow: expected clock=5 red=0 yellow=1 green=0  got clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      fail_count = fail_count + 1;
    end

    #150;
    clock_cnt = clock;

    // test 5: back to green
    if (clock==60 && red==0 && yellow==0 && green==1) begin
      $display("[PASS] t=890 green2: clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      pass_count = pass_count + 1;
    end else begin
      $display("[FAIL] t=890 green2: expected clock=60 red=0 yellow=0 green=1  got clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      fail_count = fail_count + 1;
    end

    #30;

    // test 6: pass_request baseline (clock counting down, no request yet)
    if (clock==57 && red==0 && yellow==0 && green==1) begin
      $display("[PASS] t=920 pre-req: clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      pass_count = pass_count + 1;
    end else begin
      $display("[FAIL] t=920 pre-req: expected clock=57 red=0 yellow=0 green=1  got clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      fail_count = fail_count + 1;
    end

    pass_request = 1;
    #10;

    // test 7: pass_request accelerates green countdown to 10
    if (clock==10 && red==0 && yellow==0 && green==1) begin
      $display("[PASS] t=930 pass_req: clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      pass_count = pass_count + 1;
    end else begin
      $display("[FAIL] t=930 pass_req: expected clock=10 red=0 yellow=0 green=1  got clock=%0d red=%b yellow=%b green=%b", clock, red, yellow, green);
      fail_count = fail_count + 1;
    end

    $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
             pass_count, fail_count, pass_count + fail_count);
    $finish;
  end

  initial begin
    #100000;
    $display("[FAIL] TIMEOUT");
    $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
             pass_count, fail_count + 1, pass_count + fail_count + 1);
    $finish;
  end

endmodule
