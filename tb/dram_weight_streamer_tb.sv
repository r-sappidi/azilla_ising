`timescale 1ns/1ps

import ising_pkg::*;

module dram_weight_streamer_tb;
    localparam int CLK_PERIOD_PS = 1000;
    localparam int RAMULATOR_TCK_PS = 250;
    localparam int RAMULATOR_TICKS_PER_CYCLE =
        CLK_PERIOD_PS / RAMULATOR_TCK_PS;
    localparam int MVM_COUNT = 2;
    localparam int STATE_INDEX_W = 3;
    localparam int GLOBAL_BLOCK_ID_W = 3;
    localparam int TOTAL_BLOCK_COUNT = 8;
    localparam int LANES = 4;
    localparam int ENGINE_ID_W = 1;
    localparam int TAG_W = ENGINE_ID_W + 1 + $clog2(SPIN_COUNT);

    logic clk = 1'b0;
    logic rst;
    always #0.5 clk = ~clk;

    logic [MVM_COUNT-1:0] sched_cmd_valid, sched_cmd_ready;
    logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0] sched_state_a, sched_state_b;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] sched_block_a, sched_block_b;
    logic [MVM_COUNT-1:0] node_cmd_valid, node_cmd_ready;
    logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0] node_state_a, node_state_b;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] node_block_a, node_block_b;
    logic [MVM_COUNT-1:0] node_weight_valid, node_weight_ready;
    logic [MVM_COUNT-1:0][DATA_W-1:0] node_weight_data;
    logic [LANES-1:0] mem_req_valid, mem_req_ready;
    logic [LANES-1:0][63:0] mem_req_addr;
    logic [LANES-1:0][TAG_W-1:0] mem_req_tag;
    logic [LANES-1:0] mem_rsp_valid, mem_rsp_ready;
    logic [LANES-1:0][DATA_W-1:0] mem_rsp_data;
    logic [LANES-1:0][TAG_W-1:0] mem_rsp_tag;
    logic [$clog2(2*MVM_COUNT*SPIN_COUNT+1)-1:0] outstanding;
    logic streamer_idle;
    int beat_count [0:MVM_COUNT-1];
    int completed_engines;

    import "DPI-C" function void az_dram_init(
        input string config_path,
        input string dataset_path,
        input int system_count,
        input int block_count
    );
    import "DPI-C" function void az_dram_tick(input int tick_count);
    import "DPI-C" function void az_dram_report();
    import "DPI-C" function void az_dram_finalize();

    dram_weight_streamer #(
        .MVM_COUNT(MVM_COUNT),
        .STATE_INDEX_W(STATE_INDEX_W),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .TOTAL_BLOCK_COUNT(TOTAL_BLOCK_COUNT),
        .MEM_REQ_LANES(LANES),
        .MEM_RSP_LANES(LANES)
    ) dut (
        .clk, .rst,
        .sched_cmd_valid_i(sched_cmd_valid),
        .sched_cmd_ready_o(sched_cmd_ready),
        .sched_state_a_index_i(sched_state_a),
        .sched_state_b_index_i(sched_state_b),
        .sched_block_a_id_i(sched_block_a),
        .sched_block_b_id_i(sched_block_b),
        .node_cmd_valid_o(node_cmd_valid),
        .node_cmd_ready_i(node_cmd_ready),
        .node_state_a_index_o(node_state_a),
        .node_state_b_index_o(node_state_b),
        .node_block_a_id_o(node_block_a),
        .node_block_b_id_o(node_block_b),
        .node_weight_valid_o(node_weight_valid),
        .node_weight_ready_i(node_weight_ready),
        .node_weight_data_o(node_weight_data),
        .mem_req_valid_o(mem_req_valid),
        .mem_req_ready_i(mem_req_ready),
        .mem_req_addr_o(mem_req_addr),
        .mem_req_tag_o(mem_req_tag),
        .mem_rsp_valid_i(mem_rsp_valid),
        .mem_rsp_ready_o(mem_rsp_ready),
        .mem_rsp_data_i(mem_rsp_data),
        .mem_rsp_tag_i(mem_rsp_tag),
        .outstanding_o(outstanding), .idle_o(streamer_idle)
    );

    ramulator_dpi_bridge #(
        .SYSTEM_ID(0), .REQ_LANES(LANES), .RSP_LANES(LANES),
        .TAG_W(TAG_W)
    ) memory (
        .clk, .rst,
        .req_valid_i(mem_req_valid), .req_ready_o(mem_req_ready),
        .req_addr_i(mem_req_addr), .req_tag_i(mem_req_tag),
        .rsp_valid_o(mem_rsp_valid), .rsp_ready_i(mem_rsp_ready),
        .rsp_data_o(mem_rsp_data), .rsp_tag_o(mem_rsp_tag)
    );

    function automatic byte signed expected_weight(
        input int row,
        input int column
    );
        int distance;
        distance = (row > column) ? row-column : column-row;
        return (distance == 1 || distance == 255) ? 1 : 0;
    endfunction

    always @(negedge clk)
        if (!rst) az_dram_tick(RAMULATOR_TICKS_PER_CYCLE);

    always @(posedge clk) begin
        for (int engine = 0; engine < MVM_COUNT; engine++) begin
            if (node_weight_valid[engine] && node_weight_ready[engine]) begin
                for (int column = 0; column < SPIN_COUNT; column++) begin
                    int row_global;
                    int column_global;
                    byte signed got;
                    row_global = int'(node_block_a[engine])*SPIN_COUNT + beat_count[engine];
                    column_global = int'(node_block_b[engine])*SPIN_COUNT + column;
                    got = node_weight_data[engine][column*WEIGHT_W +: WEIGHT_W];
                    assert (got == expected_weight(row_global, column_global))
                        else $fatal(1, "engine %0d beat %0d column %0d got %0d expected %0d",
                                    engine, beat_count[engine], column, got,
                                    expected_weight(row_global, column_global));
                end
                if (beat_count[engine] == SPIN_COUNT-1) begin
                    beat_count[engine] <= 0;
                    completed_engines <= completed_engines + 1;
                end
                else
                    beat_count[engine] <= beat_count[engine] + 1;
            end
        end
    end

    initial begin
        az_dram_init("tb/ramulator_128x32.yaml",
                     "tb/datasets/g256_smoke.txt", 1, TOTAL_BLOCK_COUNT);
        rst = 1'b1;
        sched_cmd_valid = '0;
        sched_state_a = '0;
        sched_state_b = '0;
        sched_block_a = '0;
        sched_block_b = '0;
        node_cmd_ready = '1;
        node_weight_ready = '1;
        beat_count = '{default: 0};
        completed_engines = 0;
        repeat (5) @(negedge clk);
        rst = 1'b0;

        @(negedge clk);
        sched_state_a[0] = 0; sched_state_b[0] = 1;
        sched_block_a[0] = 0; sched_block_b[0] = 1;
        sched_state_a[1] = 1; sched_state_b[1] = 2;
        sched_block_a[1] = 1; sched_block_b[1] = 2;
        sched_cmd_valid = '1;
        while ((sched_cmd_valid & sched_cmd_ready) != sched_cmd_valid) @(negedge clk);
        @(negedge clk);
        sched_cmd_valid = '0;

        wait (completed_engines == MVM_COUNT);
        repeat (4) @(posedge clk);
        assert (outstanding == 0) else $fatal(1, "outstanding reads remain");
        az_dram_report();
        az_dram_finalize();
        $display("dram_weight_streamer_tb PASS");
        $finish;
    end

    initial begin
        repeat (100000) @(posedge clk);
        $fatal(1, "dram_weight_streamer_tb timeout");
    end
endmodule
