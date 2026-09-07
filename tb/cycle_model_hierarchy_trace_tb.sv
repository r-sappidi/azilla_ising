`timescale 1ns/1ps

import ising_pkg::*;

module cycle_model_hierarchy_trace_tb;
    localparam int MVM_COUNT = 2;
    logic clk = 1'b0;
    logic rst = 1'b1;
    logic iter_start = 1'b0;
    logic iter_done;
    logic state_valid = 1'b0;
    logic state_ready;
    logic state_index = 1'b0;
    logic [SPIN_COUNT-1:0] state_data = '0;
    logic schedule_done = 1'b0;
    logic [MVM_COUNT-1:0] cmd_valid = '0;
    logic [MVM_COUNT-1:0] cmd_ready;
    logic [MVM_COUNT-1:0][0:0] state_a_index = '0;
    logic [MVM_COUNT-1:0][0:0] state_b_index = '0;
    logic [MVM_COUNT-1:0][15:0] block_a = '0;
    logic [MVM_COUNT-1:0][15:0] block_b = '0;
    logic [MVM_COUNT-1:0] weight_valid = '0;
    logic [MVM_COUNT-1:0] weight_ready;
    logic [MVM_COUNT-1:0][DATA_W-1:0] weight_data = '0;
    logic [MVM_COUNT-1:0] partial_valid;
    logic [MVM_COUNT-1:0] partial_ready = '1;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] partial_data;
    logic [MVM_COUNT-1:0][15:0] partial_block;
    logic [MVM_COUNT-1:0] partial_last;
    int trace_cycle = 0;
    int accepted_weights [0:MVM_COUNT-1] = '{default: 0};

    always #5 clk = ~clk;

    function automatic logic [DATA_W-1:0] identity_row(input int row);
        logic [DATA_W-1:0] word;
        word = '0;
        word[row*WEIGHT_W +: WEIGHT_W] = 8'd1;
        return word;
    endfunction

    hierarchy_node #(
        .STATE_ENTRY_COUNT(2), .MVM_COUNT(MVM_COUNT), .GLOBAL_BLOCK_ID_W(16)
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
        for (int engine = 0; engine < MVM_COUNT; engine++) begin
            if (weight_valid[engine] && weight_ready[engine])
                accepted_weights[engine]++;
        end
        for (int engine = 0; engine < MVM_COUNT; engine++)
            $display("HN %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %064h %0d %0d %0d %0d %0d %0d",
                     trace_cycle, engine, dut.node_state, iter_done, state_ready,
                     cmd_ready[engine], weight_ready[engine], partial_valid[engine],
                     partial_block[engine], partial_last[engine], partial_data[engine],
                     dut.engine_state[engine], dut.output_state[engine],
                     dut.engine_slot_valid[engine], dut.engine_result_slot_valid[engine],
                     dut.engine_result_slot_reserved[engine], dut.engine_partial_beat[engine]);
        trace_cycle++;
        if (trace_cycle == 512) begin
            $display("WATCHDOG accepted_weights=%0d,%0d", accepted_weights[0],
                     accepted_weights[1]);
            $finish;
        end
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

        cmd_valid = '1;
        state_a_index = '0;
        state_b_index = '1;
        block_a[0] = 16'd10; block_b[0] = 16'd20;
        block_a[1] = 16'd11; block_b[1] = 16'd21;
        step_and_trace();
        cmd_valid = '0;

        for (int engine = 0; engine < MVM_COUNT; engine++) begin
            for (int row = 0; row < SPIN_COUNT; row++) begin
                weight_valid = '0;
                while (!weight_ready[engine])
                    step_and_trace();
                weight_valid[engine] = 1'b1;
                weight_data[engine] = identity_row(row);
                step_and_trace();
            end
        end
        weight_valid = '0;
        schedule_done = 1'b1;
        step_and_trace();
        schedule_done = 1'b0;

        while (!iter_done)
            step_and_trace();
        $finish;
    end
endmodule
