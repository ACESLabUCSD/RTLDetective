module tb ();

reg  clk,rst;

wire out1;
wire out2;
wire out3;

freq_div uut(.CLK_in(clk),.RST(rst),.CLK_50(out1),.CLK_10(out2),.CLK_1(out3));

initial begin
	clk = 0;
	forever #5 clk = ~clk;
end

integer error=0;
integer pass_count = 0;
integer fail_count = 0;

initial begin
	#10;
	rst = 1;
	#35;
	rst = 0;

	// t=45: out1=0, out2=0, out3=0
	if (out1 == 0 && out2 == 0 && out3 == 0) begin
		$display("[PASS] t=45: out1=%b out2=%b out3=%b", out1, out2, out3);
		pass_count = pass_count + 1;
	end else begin
		$display("[FAIL] t=45: expected out1=0 out2=0 out3=0  got out1=%b out2=%b out3=%b", out1, out2, out3);
		fail_count = fail_count + 1;
	end

	// t=55: out1=1, out2=0, out3=0
	#10;
	if (out1 == 1 && out2 == 0 && out3 == 0) begin
		$display("[PASS] t=55: out1=%b out2=%b out3=%b", out1, out2, out3);
		pass_count = pass_count + 1;
	end else begin
		$display("[FAIL] t=55: expected out1=1 out2=0 out3=0  got out1=%b out2=%b out3=%b", out1, out2, out3);
		fail_count = fail_count + 1;
	end

	// t=95: out1=1, out2=1, out3=0
	#40;
	if (out1 == 1 && out2 == 1 && out3 == 0) begin
		$display("[PASS] t=95: out1=%b out2=%b out3=%b", out1, out2, out3);
		pass_count = pass_count + 1;
	end else begin
		$display("[FAIL] t=95: expected out1=1 out2=1 out3=0  got out1=%b out2=%b out3=%b", out1, out2, out3);
		fail_count = fail_count + 1;
	end

	// t=225: out1=0, out2=1, out3=0
	#130;
	if (out1 == 0 && out2 == 1 && out3 == 0) begin
		$display("[PASS] t=225: out1=%b out2=%b out3=%b", out1, out2, out3);
		pass_count = pass_count + 1;
	end else begin
		$display("[FAIL] t=225: expected out1=0 out2=1 out3=0  got out1=%b out2=%b out3=%b", out1, out2, out3);
		fail_count = fail_count + 1;
	end

	// t=625: out1=0, out2=1, out3=1
	#400;
	if (out1 == 0 && out2 == 1 && out3 == 1) begin
		$display("[PASS] t=625: out1=%b out2=%b out3=%b", out1, out2, out3);
		pass_count = pass_count + 1;
	end else begin
		$display("[FAIL] t=625: expected out1=0 out2=1 out3=1  got out1=%b out2=%b out3=%b", out1, out2, out3);
		fail_count = fail_count + 1;
	end

	// t=1035: out1=1, out2=1, out3=1
	#410;
	if (out1 == 1 && out2 == 1 && out3 == 1) begin
		$display("[PASS] t=1035: out1=%b out2=%b out3=%b", out1, out2, out3);
		pass_count = pass_count + 1;
	end else begin
		$display("[FAIL] t=1035: expected out1=1 out2=1 out3=1  got out1=%b out2=%b out3=%b", out1, out2, out3);
		fail_count = fail_count + 1;
	end

	$display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
	         pass_count, fail_count, pass_count + fail_count);
	$finish;
end

initial begin
	#10000;
	$display("[FAIL] TIMEOUT");
	$display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
	         pass_count, fail_count + 1, pass_count + fail_count + 1);
	$finish;
end

endmodule