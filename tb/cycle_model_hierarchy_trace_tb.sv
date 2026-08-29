`timescale 1ns/1ps

import ising_pkg::*;

module cycle_model_hierarchy_trace_tb;
    logic clk = 1'b0;
    logic rst = 1'b1;
    logic iter_start = 1'b0;
    logic iter_done;
    logic state_valid = 1'b0;
    logic state_ready;
    logic state_index = 1'b0;
    logic [SPIN_COUNT-1:0] state_data = '0;
    logic schedule_done = 1'b0;
    logic [0:0] cmd_valid = '0;
    logic [0:0] cmd_ready;
    logic [0:0][0:0] state_a_index = '0;
    logic [0:0][0:0] state_b_index = '0;
    logic [0:0][15:0] block_a = '0;
    logic [0:0][15:0] block_b = '0;
    logic [0:0] weight_valid = '0;
    logic [0:0] weight_ready;
    logic [0:0][DATA_W-1:0] weight_data = '0;
    logic [0:0] partial_valid;
    logic [0:0] partial_ready = '1;
    logic signed [0:0][DATA_W-1:0] partial_data;
    logic [0:0][15:0] partial_block;
    logic [0:0] partial_last;
    int trace_cycle = 0;

    always #5 clk = ~clk;

    function automatic logic [DATA_W-1:0] identity_row(input int row);
        logic [DATA_W-1:0] word;
        word = '0;
        word[row*WEIGHT_W +: WEIGHT_W] = 8'd1;
        return word;
    endfunction

    hierarchy_node #(
        .STATE_ENTRY_COUNT(2), .MVM_COUNT(1), .GLOBAL_BLOCK_ID_W(16)
    ) dut (
        .clk, .rst, .iter_start, .iter_done,
        .state_valid_i(state_valid), .state_ready_o(state_ready),
        .state_index_i(state_index), .state_data_i(state_data),
        .schedule_done_i(schedule_done),
        .dma_cmd_valid_i(cmd_valid), .dma_cmd_ready_o(cmd_ready),
        .dma_state_a_index_i(state_a_index),
        .dma_state_b_index_i(state_b_index),
        .dma_block_a_i(block_a), .dma_block_b_i(block_b),
        .dma_weight_valid_i(weight_valid), .dma_weight_ready_o(weight_ready),
        .dma_weight_data_i(weight_data),
        .partial_valid_o(partial_valid), .partial_ready_i(partial_ready),
        .partial_data_o(partial_data), .partial_block_id_o(partial_block),
        .partial_last_o(partial_last)
    );

    task automatic step_and_trace;
        @(posedge clk);
        @(negedge clk);
        $display("HN %0d %0d %0d %0d %0d %0d %0d %0d %0d %064h %0d %0d %0d %0d %0d %0d",
                 trace_cycle, dut.node_state, iter_done, state_ready,
                 cmd_ready[0], weight_ready[0], partial_valid[0],
                 partial_block[0], partial_last[0], partial_data[0],
                 dut.engine_state[0], dut.output_state[0],
                 dut.engine_slot_valid[0], dut.engine_result_slot_valid[0],
                 dut.engine_result_slot_reserved[0], dut.engine_partial_beat[0]);
        trace_cycle++;
    endtask

    initial begin
        step_and_trace();
        step_and_trace();
        rst = 1'b0;
        step_and_trace();

        state_valid = 1'b1;
        state_index = 1'b0;
        state_data = 32'hffffffff;
        step_and_trace();
        state_index = 1'b1;
        state_data = 32'h00000000;
        step_and_trace();
        state_valid = 1'b0;

        iter_start = 1'b1;
        step_and_trace();
        iter_start = 1'b0;

        cmd_valid[0] = 1'b1;
        state_a_index[0] = 0;
        state_b_index[0] = 1;
        block_a[0] = 16'd10;
        block_b[0] = 16'd20;
        step_and_trace();
        cmd_valid[0] = 1'b0;

        for (int row = 0; row < SPIN_COUNT; row++) begin
            weight_valid[0] = 1'b1;
            weight_data[0] = identity_row(row);
            step_and_trace();
        end
        weight_valid[0] = 1'b0;
        schedule_done = 1'b1;
        step_and_trace();
        schedule_done = 1'b0;

        while (!iter_done)
            step_and_trace();
        $finish;
    end
endmodule
