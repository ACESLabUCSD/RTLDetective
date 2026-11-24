//////////////////////////////////////////////////////////////////////
////                                                              ////
////  File name "tb_xge_mac.v"                                    ////
////                                                              ////
////  This file is part of the "10GE MAC" project                 ////
////  http://www.opencores.org/cores/xge_mac/                     ////
////                                                              ////
////  Author(s):                                                  ////
////      - A. Tanguay (antanguay@opencores.org)                  ////
////                                                              ////
//////////////////////////////////////////////////////////////////////
////                                                              ////
//// Copyright (C) 2008 AUTHORS. All rights reserved.             ////
////                                                              ////
//// This source file may be used and distributed without         ////
//// restriction provided that this copyright statement is not    ////
//// removed from the file and that any derivative work contains  ////
//// the original copyright notice and the associated disclaimer. ////
////                                                              ////
//// This source file is free software; you can redistribute it   ////
//// and/or modify it under the terms of the GNU Lesser General   ////
//// Public License as published by the Free Software Foundation; ////
//// either version 2.1 of the License, or (at your option) any   ////
//// later version.                                               ////
////                                                              ////
//// This source is distributed in the hope that it will be       ////
//// useful, but WITHOUT ANY WARRANTY; without even the implied   ////
//// warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR      ////
//// PURPOSE.  See the GNU Lesser General Public License for more ////
//// details.                                                     ////
////                                                              ////
//// You should have received a copy of the GNU Lesser General    ////
//// Public License along with this source; if not, download it   ////
//// from http://www.opencores.org/lgpl.shtml                     ////
////                                                              ////
//////////////////////////////////////////////////////////////////////

`include "timescale.v"
`include "defines.v"

//`define GXB
//`define XIL
//`define XIL_V10

module tb;

    // ── Registers ────────────────────────────────────────────────────────
    reg [7:0]  tx_buffer[0:10000];
    integer    tx_length;

    reg        clk_156m25;
    reg        clk_312m50;
    reg        clk_xgmii_rx;
    reg        clk_xgmii_tx;

    reg        reset_156m25_n;
    reg        reset_xgmii_rx_n;
    reg        reset_xgmii_tx_n;

    reg        pkt_rx_ren;

    reg [63:0] pkt_tx_data;
    reg        pkt_tx_val;
    reg        pkt_tx_sop;
    reg        pkt_tx_eop;
    reg [2:0]  pkt_tx_mod;

    reg        wb_clk_i;
    reg [31:0] wb_adr_i;
    reg        wb_cyc_i;
    reg [31:0] wb_dat_i;
    reg        wb_rst_i;
    reg        wb_stb_i;
    reg        wb_we_i;

    integer    tx_count;
    integer    rx_count;
    integer    pass_count;
    integer    fail_count;
    integer init_i;

    // ── Wires ─────────────────────────────────────────────────────────────
    wire        pkt_rx_avail;
    wire [63:0] pkt_rx_data;
    wire        pkt_rx_eop;
    wire        pkt_rx_err;
    wire [2:0]  pkt_rx_mod;
    wire        pkt_rx_sop;
    wire        pkt_rx_val;
    wire        pkt_tx_full;
    wire        wb_ack_o;
    wire [31:0] wb_dat_o;
    wire        wb_int_o;
    wire [7:0]  xgmii_txc;
    wire [63:0] xgmii_txd;

    wire [7:0]  xgmii_rxc;
    wire [63:0] xgmii_rxd;
    wire [3:0]  tx_dataout;

    wire        xaui_tx_l0_n, xaui_tx_l0_p;
    wire        xaui_tx_l1_n, xaui_tx_l1_p;
    wire        xaui_tx_l2_n, xaui_tx_l2_p;
    wire        xaui_tx_l3_n, xaui_tx_l3_p;

    // ── DUT ───────────────────────────────────────────────────────────────
    xge_mac dut (
        .pkt_rx_avail       (pkt_rx_avail),
        .pkt_rx_data        (pkt_rx_data[63:0]),
        .pkt_rx_eop         (pkt_rx_eop),
        .pkt_rx_err         (pkt_rx_err),
        .pkt_rx_mod         (pkt_rx_mod[2:0]),
        .pkt_rx_sop         (pkt_rx_sop),
        .pkt_rx_val         (pkt_rx_val),
        .pkt_tx_full        (pkt_tx_full),
        .wb_ack_o           (wb_ack_o),
        .wb_dat_o           (wb_dat_o[31:0]),
        .wb_int_o           (wb_int_o),
        .xgmii_txc          (xgmii_txc[7:0]),
        .xgmii_txd          (xgmii_txd[63:0]),
        .clk_156m25         (clk_156m25),
        .clk_xgmii_rx       (clk_xgmii_rx),
        .clk_xgmii_tx       (clk_xgmii_tx),
        .pkt_rx_ren         (pkt_rx_ren),
        .pkt_tx_data        (pkt_tx_data[63:0]),
        .pkt_tx_eop         (pkt_tx_eop),
        .pkt_tx_mod         (pkt_tx_mod[2:0]),
        .pkt_tx_sop         (pkt_tx_sop),
        .pkt_tx_val         (pkt_tx_val),
        .reset_156m25_n     (reset_156m25_n),
        .reset_xgmii_rx_n   (reset_xgmii_rx_n),
        .reset_xgmii_tx_n   (reset_xgmii_tx_n),
        .wb_adr_i           (wb_adr_i[7:0]),
        .wb_clk_i           (wb_clk_i),
        .wb_cyc_i           (wb_cyc_i),
        .wb_dat_i           (wb_dat_i[31:0]),
        .wb_rst_i           (wb_rst_i),
        .wb_stb_i           (wb_stb_i),
        .wb_we_i            (wb_we_i),
        .xgmii_rxc          (xgmii_rxc[7:0]),
        .xgmii_rxd          (xgmii_rxd[63:0])
    );

    // ── Optional Transceiver Instances (unchanged) ────────────────────────
`ifdef GXB
    gxb gxb(
        .rx_ctrldetect  ({xgmii_rxc[7],xgmii_rxc[5],xgmii_rxc[3],xgmii_rxc[1],
                          xgmii_rxc[6],xgmii_rxc[4],xgmii_rxc[2],xgmii_rxc[0]}),
        .rx_dataout     ({xgmii_rxd[63:56],xgmii_rxd[47:40],xgmii_rxd[31:24],xgmii_rxd[15:8],
                          xgmii_rxd[55:48],xgmii_rxd[39:32],xgmii_rxd[23:16],xgmii_rxd[7:0]}),
        .tx_dataout     (tx_dataout[3:0]),
        .pll_inclk      (clk_156m25),
        .rx_analogreset (~reset_156m25_n),
        .rx_cruclk      ({clk_156m25,clk_156m25,clk_156m25,clk_156m25}),
        .rx_datain      (tx_dataout[3:0]),
        .rx_digitalreset(~reset_156m25_n),
        .tx_ctrlenable  ({xgmii_txc[7],xgmii_txc[5],xgmii_txc[3],xgmii_txc[1],
                          xgmii_txc[6],xgmii_txc[4],xgmii_txc[2],xgmii_txc[0]}),
        .tx_datain      ({xgmii_txd[63:56],xgmii_txd[47:40],xgmii_txd[31:24],xgmii_txd[15:8],
                          xgmii_txd[55:48],xgmii_txd[39:32],xgmii_txd[23:16],xgmii_txd[7:0]}),
        .tx_digitalreset(~reset_156m25_n));
`endif

`ifdef XIL
    xaui_block xaui(
        .txoutclk               (), .xgmii_rxd (xgmii_rxd[63:0]), .xgmii_rxc (xgmii_rxc[7:0]),
        .xaui_tx_l0_p           (xaui_tx_l0_p), .xaui_tx_l0_n (xaui_tx_l0_n),
        .xaui_tx_l1_p           (xaui_tx_l1_p), .xaui_tx_l1_n (xaui_tx_l1_n),
        .xaui_tx_l2_p           (xaui_tx_l2_p), .xaui_tx_l2_n (xaui_tx_l2_n),
        .xaui_tx_l3_p           (xaui_tx_l3_p), .xaui_tx_l3_n (xaui_tx_l3_n),
        .txlock (), .align_status (), .sync_status (), .mgt_tx_ready (),
        .drp_o (), .drp_rdy (), .status_vector (),
        .dclk                   (clk_156m25), .clk156 (clk_156m25),
        .clk312                 (clk_312m50), .refclk (clk_156m25),
        .reset                  (~reset_156m25_n), .reset156 (~reset_156m25_n),
        .xgmii_txd              (xgmii_txd[63:0]), .xgmii_txc (xgmii_txc[7:0]),
        .xaui_rx_l0_p           (xaui_tx_l0_p), .xaui_rx_l0_n (xaui_tx_l0_n),
        .xaui_rx_l1_p           (xaui_tx_l1_p), .xaui_rx_l1_n (xaui_tx_l1_n),
        .xaui_rx_l2_p           (xaui_tx_l2_p), .xaui_rx_l2_n (xaui_tx_l2_n),
        .xaui_rx_l3_p           (xaui_tx_l3_p), .xaui_rx_l3_n (xaui_tx_l3_n),
        .signal_detect          (4'b1111), .drp_addr (7'b0), .drp_en (2'b0),
        .drp_i                  (16'b0), .drp_we (2'b0), .configuration_vector (7'b0));
    glbl glbl();
`endif

`ifdef XIL_V10
    xaui_v10_2_block xaui(
        .txoutclk               (), .xgmii_rxd (xgmii_rxd[63:0]), .xgmii_rxc (xgmii_rxc[7:0]),
        .xaui_tx_l0_p           (xaui_tx_l0_p), .xaui_tx_l0_n (xaui_tx_l0_n),
        .xaui_tx_l1_p           (xaui_tx_l1_p), .xaui_tx_l1_n (xaui_tx_l1_n),
        .xaui_tx_l2_p           (xaui_tx_l2_p), .xaui_tx_l2_n (xaui_tx_l2_n),
        .xaui_tx_l3_p           (xaui_tx_l3_p), .xaui_tx_l3_n (xaui_tx_l3_n),
        .txlock (), .align_status (), .sync_status (), .mgt_tx_ready (),
        .drp_o (), .drp_rdy (), .status_vector (),
        .dclk                   (clk_156m25), .clk156 (clk_156m25),
        .refclk                 (clk_156m25), .reset (~reset_156m25_n),
        .reset156               (~reset_156m25_n),
        .xgmii_txd              (xgmii_txd[63:0]), .xgmii_txc (xgmii_txc[7:0]),
        .xaui_rx_l0_p           (xaui_tx_l0_p), .xaui_rx_l0_n (xaui_tx_l0_n),
        .xaui_rx_l1_p           (xaui_tx_l1_p), .xaui_rx_l1_n (xaui_tx_l1_n),
        .xaui_rx_l2_p           (xaui_tx_l2_p), .xaui_rx_l2_n (xaui_tx_l2_n),
        .xaui_rx_l3_p           (xaui_tx_l3_p), .xaui_rx_l3_n (xaui_tx_l3_n),
        .signal_detect          (4'b1111), .drp_addr (9'b0), .drp_en (4'b0),
        .drp_i                  (16'b0), .drp_we (4'b0), .configuration_vector (7'b0));
    glbl glbl();
`endif

    // ── XGMII Loopback ────────────────────────────────────────────────────
`ifndef GXB
  `ifndef XIL
    `ifndef XIL_V10
      assign xgmii_rxc = xgmii_txc;
      assign xgmii_rxd = xgmii_txd;
    `endif
  `endif
`endif

    // ── Timing helpers ────────────────────────────────────────────────────
    task WaitNS;
        input [31:0] delay;
        begin #(1000 * delay); end
    endtask

    task WaitPS;
        input [31:0] delay;
        begin #(delay); end
    endtask

    // ── Clock generation ──────────────────────────────────────────────────
    initial begin
        clk_156m25   = 1'b0; clk_xgmii_rx = 1'b0; clk_xgmii_tx = 1'b0;
        forever begin
            WaitPS(3200);
            clk_156m25   = ~clk_156m25;
            clk_xgmii_rx = ~clk_xgmii_rx;
            clk_xgmii_tx = ~clk_xgmii_tx;
        end
    end

    initial begin
        wb_clk_i = 1'b0;
        forever begin WaitPS(20000); wb_clk_i = ~wb_clk_i; end
    end

    initial begin
        clk_312m50 = 1'b0;
        forever begin WaitPS(1600); clk_312m50 = ~clk_312m50; end
    end

    // ── Reset generation ──────────────────────────────────────────────────
    initial begin
        reset_156m25_n   = 1'b0;
        reset_xgmii_rx_n = 1'b0;
        reset_xgmii_tx_n = 1'b0;
        WaitNS(20);
        reset_156m25_n   = 1'b1;
        reset_xgmii_rx_n = 1'b1;
        reset_xgmii_tx_n = 1'b1;
    end

    // ── Signal initialisation ─────────────────────────────────────────────
    initial begin
        for (init_i = 0; init_i <= 1000; init_i = init_i + 1)
            tx_buffer[init_i] = 8'h00;

        pass_count = 0;
        fail_count = 0;
        tx_count   = 0;
        rx_count   = 0;
        pkt_rx_ren = 1'b0;

        pkt_tx_data = 64'b0;
        pkt_tx_val  = 1'b0;
        pkt_tx_sop  = 1'b0;
        pkt_tx_eop  = 1'b0;
        pkt_tx_mod  = 3'b0;
    end

    // ── Wishbone initialisation ───────────────────────────────────────────
    initial begin
        wb_adr_i <= 8'b0;
        wb_cyc_i <= 1'b0;
        wb_dat_i <= 32'b0;
        wb_rst_i <= 1'b1;
        wb_stb_i <= 1'b0;
        wb_we_i  <= 1'b0;
        @(posedge wb_clk_i);
        wb_rst_i <= 1'b0;
    end

    // ── Check helpers ─────────────────────────────────────────────────────

    // Verify that a received packet count matches what was sent
    task check_pkt_count(input [127:0] label);
        begin
            #1;
            if (rx_count === tx_count) begin
                $display("[PASS] %0s: rx_count=%0d == tx_count=%0d", label, rx_count, tx_count);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: rx_count=%0d != tx_count=%0d", label, rx_count, tx_count);
                fail_count = fail_count + 1;
            end
        end
    endtask

    // Verify that no rx_err was asserted during a packet
    task check_no_rx_err(input [127:0] label);
        begin
            #1;
            if (pkt_rx_err === 1'b0) begin
                $display("[PASS] %0s: pkt_rx_err=0 (no error)", label);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: pkt_rx_err=1 (unexpected error)", label);
                fail_count = fail_count + 1;
            end
        end
    endtask

    // Verify a 32-bit register read result
    task check_reg(input [31:0] actual, input [31:0] expected, input [127:0] label);
        begin
            if (actual === expected) begin
                $display("[PASS] %0s: reg=0x%08X (expected 0x%08X)", label, actual, expected);
                pass_count = pass_count + 1;
            end else begin
                $display("[FAIL] %0s: reg=0x%08X (expected 0x%08X)", label, actual, expected);
                fail_count = fail_count + 1;
            end
        end
    endtask

    // ── Wishbone register read ────────────────────────────────────────────
    task CpuRead;
        input  [31:0] addr;
        output [31:0] data;
        begin
            @(posedge wb_clk_i);
            wb_adr_i <= addr;
            wb_cyc_i <= 1'b1;
            wb_stb_i <= 1'b1;
            @(posedge wb_clk_i);
            wb_stb_i <= 1'b0;
            @(posedge wb_clk_i);
            wb_cyc_i <= 1'b0;
            data     <= wb_dat_o;
            @(posedge wb_clk_i);
        end
    endtask

    // ── Packet transmit ───────────────────────────────────────────────────
    task TxPacket;
        integer i;
        begin
            $display("[TX] Sending packet, length=%0d", tx_length);

            @(posedge clk_156m25);
            WaitNS(1);
            pkt_tx_val = 1'b1;

            for (i = 0; i < tx_length; i = i + 8) begin
                pkt_tx_sop = (i == 0) ? 1'b1 : 1'b0;
                pkt_tx_eop = 1'b0;
                pkt_tx_mod = 3'b0;

                if (i + 8 >= tx_length) begin
                    pkt_tx_eop = 1'b1;
                    pkt_tx_mod = tx_length % 8;
                end

                pkt_tx_data[`LANE7] = tx_buffer[i];
                pkt_tx_data[`LANE6] = tx_buffer[i+1];
                pkt_tx_data[`LANE5] = tx_buffer[i+2];
                pkt_tx_data[`LANE4] = tx_buffer[i+3];
                pkt_tx_data[`LANE3] = tx_buffer[i+4];
                pkt_tx_data[`LANE2] = tx_buffer[i+5];
                pkt_tx_data[`LANE1] = tx_buffer[i+6];
                pkt_tx_data[`LANE0] = tx_buffer[i+7];

                @(posedge clk_156m25);
                WaitNS(1);
            end

            pkt_tx_val = 1'b0;
            pkt_tx_eop = 1'b0;
            pkt_tx_mod = 3'b0;
            tx_count   = tx_count + 1;
        end
    endtask

    // ── Packet receive ────────────────────────────────────────────────────
    task RxPacket;
        reg done;
        begin
            done = 0;
            pkt_rx_ren <= 1'b1;
            @(posedge clk_156m25);

            while (!done) begin
                if (pkt_rx_val) begin
                    if (pkt_rx_sop)
                        $display("[RX] ---- Packet start ----");

                    $display("[RX]   data=%016X", pkt_rx_data);

                    if (pkt_rx_err)
                        $display("[RX]   WARNING: pkt_rx_err asserted");

                    if (pkt_rx_eop) begin
                        $display("[RX] ---- Packet end (mod=%0d) ----", pkt_rx_mod);
                        done       = 1;
                        pkt_rx_ren <= 1'b0;
                    end
                end
                @(posedge clk_156m25);
            end

            rx_count = rx_count + 1;
            check_no_rx_err("RxPacket_no_err");
        end
    endtask

    // ── Command-file packet loader ────────────────────────────────────────
    task CmdTxPacket;
        input [31:0] file;
        integer count, data, i;
        begin
            count = $fscanf(file, "%2d", tx_length);
            if (count == 1) begin
                for (i = 0; i < tx_length; i = i + 1) begin
                    count = $fscanf(file, "%2x", data);
                    if (count) tx_buffer[i] = data;
                end
                TxPacket();
            end
        end
    endtask

    // ── Main command-file processor ───────────────────────────────────────
    reg [31:0] stat_tx_pkts;
    reg [31:0] stat_rx_pkts;
    reg [31:0] stat_tx_oct;
    reg [31:0] stat_rx_oct;

    task ProcessCmdFile;
        integer      file_cmd, count;
        reg [8*8-1:0] str;
        begin
            $dumpfile("xge_mac_tb.vcd");
            $dumpvars(0, tb);

            file_cmd = $fopen("./testset/ACES/xge_mac/testbench/packets_tx.txt", "r");
            if (!file_cmd) begin
                $display("[FAIL] Could not open packets_tx.txt");
                fail_count = fail_count + 1;
                $stop;
            end

            while (!$feof(file_cmd)) begin
                count = $fscanf(file_cmd, "%s", str);
                if (count == 1) begin
                    $display("[CMD] %0s", str);
                    case (str)
                        "SEND_PKT": CmdTxPacket(file_cmd);
                    endcase
                end
            end

            $fclose(file_cmd);

            // Allow time for all packets to be received
            WaitNS(50000);

            // ── Read and check statistics registers ──────────────────────
            CpuRead(`CPUREG_STATSTXPKTS,   stat_tx_pkts);
            CpuRead(`CPUREG_STATSRXPKTS,   stat_rx_pkts);
            CpuRead(`CPUREG_STATSTXOCTETS, stat_tx_oct);
            CpuRead(`CPUREG_STATSRXOCTETS, stat_rx_oct);

            check_reg(stat_tx_pkts, tx_count, "STATSTXPKTS_match");
            check_reg(stat_rx_pkts, rx_count, "STATSRXPKTS_match");
            check_pkt_count("TX_RX_count_match");

            $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
                     pass_count, fail_count, pass_count + fail_count);
            $finish;
        end
    endtask

    // ── Top-level stimulus ────────────────────────────────────────────────
    initial begin
        WaitNS(5000);
`ifdef XIL
        WaitNS(200000);
`endif
        ProcessCmdFile();
    end

    // ── Receive monitor ───────────────────────────────────────────────────
    initial begin
        forever begin
            if (pkt_rx_avail) begin
                RxPacket();
                if (rx_count == tx_count)
                    $display("[INFO] All %0d packets received.", rx_count);
            end
            @(posedge clk_156m25);
        end
    end

    // ── Timeout watchdog ──────────────────────────────────────────────────
    initial begin
`ifdef XIL
        #500000000;
`else
        #100000000;
`endif
        $display("[FAIL] TIMEOUT — simulation exceeded wall-clock budget");
        $display("[SUMMARY] %0d passed, %0d failed out of %0d tests",
                 pass_count, fail_count + 1, pass_count + fail_count + 1);
        $finish;
    end

endmodule