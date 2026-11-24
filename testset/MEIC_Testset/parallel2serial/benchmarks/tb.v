`timescale 1ns/1ns

module tb;
  reg clk;
  reg rst_n;
  reg [3:0] d;
  wire valid_out;
  wire dout;

  // Instantiate the DUT (Design Under Test)
  parallel2serial uut (
    .clk(clk),
    .rst_n(rst_n),
    .d(d),
    .valid_out(valid_out),
    .dout(dout)
  );
  reg [3:0]num[0:39];
  // Generate clock
  always #5 clk = ~clk;
  integer error = 0;
  integer failcase = 0;
  integer i = 0;

  integer pass_count = 0;
  integer fail_count = 0;

  reg d3,d2,d1,d0;
  
  initial begin
    #1;
    for (i=0; i<40; i=i+1) begin
      // Initialize inputs
      clk = 0;
      rst_n = 0;
      d = 4'b0;
      #10;
      rst_n = 1;
      #10;
      d = num[i];

      while (valid_out == 0) begin
        @(posedge clk);
      end

      // bit 3 — valid_out should be 1
      d3 = dout;
      if (dout == d[3] && valid_out == 1) begin
        $display("[PASS] test %0d bit3: d=4'b%b dout=%b valid_out=%b", i, num[i], dout, valid_out);
        pass_count = pass_count + 1;
      end else begin
        $display("[FAIL] test %0d bit3: d=4'b%b expected dout=%b valid_out=1  got dout=%b valid_out=%b", i, num[i], d[3], dout, valid_out);
        fail_count = fail_count + 1;
      end
      #10;

      // bit 2 — valid_out should be 0
      d2 = dout;
      if (dout == d[2] && valid_out == 0) begin
        $display("[PASS] test %0d bit2: d=4'b%b dout=%b valid_out=%b", i, num[i], dout, valid_out);
        pass_count = pass_count + 1;
      end else begin
        $display("[FAIL] test %0d bit2: d=4'b%b expected dout=%b valid_out=0  got dout=%b valid_out=%b", i, num[i], d[2], dout, valid_out);
        fail_count = fail_count + 1;
      end
      #10;

      // bit 1 — valid_out should be 0
      d1 = dout;
      if (dout == d[1] && valid_out == 0) begin
        $display("[PASS] test %0d bit1: d=4'b%b dout=%b valid_out=%b", i, num[i], dout, valid_out);
        pass_count = pass_count + 1;
      end else begin
        $display("[FAIL] test %0d bit1: d=4'b%b expected dout=%b valid_out=0  got dout=%b valid_out=%b", i, num[i], d[1], dout, valid_out);
        fail_count = fail_count + 1;
      end
      #10;

      // bit 0 — valid_out should be 0
      d0 = dout;
      if (dout == d[0] && valid_out == 0) begin
        $display("[PASS] test %0d bit0: d=4'b%b dout=%b valid_out=%b", i, num[i], dout, valid_out);
        pass_count = pass_count + 1;
      end else begin
        $display("[FAIL] test %0d bit0: d=4'b%b expected dout=%b valid_out=0  got dout=%b valid_out=%b", i, num[i], d[0], dout, valid_out);
        fail_count = fail_count + 1;
      end
      #10;

      // final cycle — valid_out should be 1 again
      if (valid_out == 1) begin
        $display("[PASS] test %0d final: valid_out=%b", i, valid_out);
        pass_count = pass_count + 1;
      end else begin
        $display("[FAIL] test %0d final: expected valid_out=1  got valid_out=%b", i, valid_out);
        fail_count = fail_count + 1;
      end
      #10;
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

  initial begin
num[0] = 4'b1010;
num[1] = 4'b1111;
num[2] = 4'b1000;
num[3] = 4'b0100;
num[4] = 4'b0000;
num[5] = 4'b0100;
num[6] = 4'b0010;
num[7] = 4'b0101;
num[8] = 4'b1111;
num[9] = 4'b0011;
num[10] = 4'b1111;
num[11] = 4'b1111;
num[12] = 4'b1100;
num[13] = 4'b0100;
num[14] = 4'b1010;
num[15] = 4'b0101;
num[16] = 4'b1001;
num[17] = 4'b1100;
num[18] = 4'b1110;
num[19] = 4'b1011;
num[20] = 4'b1111;
num[21] = 4'b0101;
num[22] = 4'b1100;
num[23] = 4'b0000;
num[24] = 4'b0000;
num[25] = 4'b0010;
num[26] = 4'b0100;
num[27] = 4'b1110;
num[28] = 4'b1011;
num[29] = 4'b0111;
num[30] = 4'b0110;
num[31] = 4'b0010;
num[32] = 4'b1011;
num[33] = 4'b0110;
num[34] = 4'b1110;
num[35] = 4'b0111;
num[36] = 4'b1001;
num[37] = 4'b1001;
num[38] = 4'b0010;
num[39] = 4'b1010;
  
  end
endmodule

