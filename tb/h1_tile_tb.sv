`timescale 1ns/1ps

import ising_pkg::*;

module h1_tile_tb;
    localparam int H0_COUNT = 2;
    localparam int CORES_PER_H0 = 2;
    localparam int H0_MVM_COUNT = 1;
    localparam int H1_MVM_COUNT = 1;
    localparam int GLOBAL_BLOCK_ID_W = 8;
    localparam int H1_STATE_INDEX_W = $clog2(H0_COUNT*CORES_PER_H0);

    logic clk = 0;
    logic rst = 1;
    logic init_start, init_done;
    logic [H0_COUNT-1:0][CORES_PER_H0-1:0] core_weight_valid, core_weight_ready;
    logic [H0_COUNT-1:0][CORES_PER_H0-1:0][DATA_W-1:0] core_weight_data;
    logic [H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0] init_state;
    logic [H0_COUNT-1:0][CORES_PER_H0-1:0][31:0] noise_seed;
    logic signed [COEFF_W-1:0] coeff_a, coeff_b, coeff_c, noise_amplitude;
    logic iter_start, iter_done, commit, done;
    logic [16:0] noise_decay;

    logic [H0_COUNT-1:0] h0_schedule_done;
    logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0] h0_cmd_valid, h0_cmd_ready;
    logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0][$clog2(CORES_PER_H0)-1:0]
        h0_state_a_index, h0_state_b_index;
    logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
        h0_block_a_id, h0_block_b_id;
    logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0] h0_weight_valid, h0_weight_ready;
    logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0][DATA_W-1:0] h0_weight_data;

    logic h1_schedule_done;
    logic [H1_MVM_COUNT-1:0] h1_cmd_valid, h1_cmd_ready;
    logic [H1_MVM_COUNT-1:0][H1_STATE_INDEX_W-1:0]
        h1_state_a_index, h1_state_b_index;
    logic [H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
        h1_block_a_id, h1_block_b_id;
    logic [H1_MVM_COUNT-1:0] h1_weight_valid, h1_weight_ready;
    logic [H1_MVM_COUNT-1:0][DATA_W-1:0] h1_weight_data;

    logic parent_partial_valid, parent_partial_ready, parent_partials_done;
    logic [GLOBAL_BLOCK_ID_W-1:0] parent_partial_block_id;
    logic signed [DATA_W-1:0] parent_partial_data;
    logic [H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
        state_current, state_next;
    int errors;

    h1_tile #(
        .H0_COUNT(H0_COUNT),
        .CORES_PER_H0(CORES_PER_H0),
        .H0_MVM_COUNT(H0_MVM_COUNT),
        .H1_MVM_COUNT(H1_MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .BASE_BLOCK_ID(0)
    ) dut (
        .clk, .rst, .init_start, .init_done,
        .core_weight_valid_i(core_weight_valid),
        .core_weight_ready_o(core_weight_ready),
        .core_weight_data_i(core_weight_data),
        .init_state_i(init_state), .noise_seed_i(noise_seed),
        .coeff_a_i(coeff_a), .coeff_b_i(coeff_b), .coeff_c_i(coeff_c),
        .noise_amplitude_i(noise_amplitude),
        .iter_start, .iter_done, .commit, .done, .noise_decay_i(noise_decay),
        .h0_schedule_done_i(h0_schedule_done),
        .h0_dma_cmd_valid_i(h0_cmd_valid), .h0_dma_cmd_ready_o(h0_cmd_ready),
        .h0_dma_state_a_index_i(h0_state_a_index),
        .h0_dma_state_b_index_i(h0_state_b_index),
        .h0_dma_block_a_id_i(h0_block_a_id),
        .h0_dma_block_b_id_i(h0_block_b_id),
        .h0_dma_weight_valid_i(h0_weight_valid),
        .h0_dma_weight_ready_o(h0_weight_ready),
        .h0_dma_weight_data_i(h0_weight_data),
        .h1_schedule_done_i(h1_schedule_done),
        .h1_dma_cmd_valid_i(h1_cmd_valid), .h1_dma_cmd_ready_o(h1_cmd_ready),
        .h1_dma_state_a_index_i(h1_state_a_index),
        .h1_dma_state_b_index_i(h1_state_b_index),
        .h1_dma_block_a_id_i(h1_block_a_id),
        .h1_dma_block_b_id_i(h1_block_b_id),
        .h1_dma_weight_valid_i(h1_weight_valid),
        .h1_dma_weight_ready_o(h1_weight_ready),
        .h1_dma_weight_data_i(h1_weight_data),
        .parent_partial_valid_i(parent_partial_valid),
        .parent_partial_ready_o(parent_partial_ready),
        .parent_partial_block_id_i(parent_partial_block_id),
        .parent_partial_data_i(parent_partial_data),
        .parent_partials_done_i(parent_partials_done),
        .state_current_o(state_current), .state_next_o(state_next)
    );

    always #5 clk = ~clk;

    initial begin
        init_start = 0;
        core_weight_valid = '0;
        core_weight_data = '0;
        init_state = '1;
        init_state[1][0] = '0;
        noise_seed = '0;
        coeff_a = 0;
        coeff_b = 1;
        coeff_c = 0;
        noise_amplitude = 0;
        noise_decay = 0;
        iter_start = 0;
        commit = 0;
        done = 0;
        h0_schedule_done = '0;
        h0_cmd_valid = '0;
        h0_state_a_index = '0;
        h0_state_b_index = '0;
        h0_block_a_id = '0;
        h0_block_b_id = '0;
        h0_weight_valid = '0;
        h0_weight_data = '0;
        h1_schedule_done = 0;
        h1_cmd_valid = '0;
        h1_state_a_index = '0;
        h1_state_b_index = '0;
        h1_block_a_id = '0;
        h1_block_b_id = '0;
        h1_weight_valid = '0;
        h1_weight_data = '0;
        parent_partial_valid = 0;
        parent_partial_block_id = '0;
        parent_partial_data = '0;
        parent_partials_done = 0;
        errors = 0;

        repeat (4) @(negedge clk);
        rst = 0;
        init_start = 1;
        @(negedge clk);
        init_start = 0;

        // The core-local diagonal blocks are zero in this test.
        core_weight_valid = '1;
        for (int beat = 0; beat < SPIN_COUNT; beat++) begin
            while (core_weight_ready != '1) @(negedge clk);
            @(negedge clk);
        end
        core_weight_valid = '0;
        wait (init_done);

        @(negedge clk);
        iter_start = 1;
        @(negedge clk);
        iter_start = 0;

        // Evaluate one cross-H0 block: global block 0 is all +1 and global
        // block 2 is all -1. An all-+1 J block must drive block 0 negative and
        // block 2 positive.
        h1_state_a_index[0] = 0;
        h1_state_b_index[0] = 2;
        h1_block_a_id[0] = 0;
        h1_block_b_id[0] = 2;
        h1_cmd_valid[0] = 1;
        while (!h1_cmd_ready[0]) @(negedge clk);
        @(negedge clk);
        h1_cmd_valid[0] = 0;

        h1_weight_data[0] = {DATA_W/WEIGHT_W{8'h01}};
        h1_weight_valid[0] = 1;
        for (int beat = 0; beat < SPIN_COUNT; beat++) begin
            while (!h1_weight_ready[0]) @(negedge clk);
            @(negedge clk);
        end
        h1_weight_valid[0] = 0;

        // Neither H0 has local cross-core work. Assert schedule completion only
        // after its internal node has received iter_start.
        h0_schedule_done = '1;
        h1_schedule_done = 1;
        parent_partials_done = 1;
        @(negedge clk);
        h0_schedule_done = '0;
        h1_schedule_done = 0;
        parent_partials_done = 0;

        wait (iter_done);
        @(negedge clk);

        if (state_next[0][0] !== '0) begin
            $error("H1 destination block 0 was %h, expected all zero", state_next[0][0]);
            errors++;
        end
        if (state_next[1][0] !== '1) begin
            $error("H1 destination block 2 was %h, expected all one", state_next[1][0]);
            errors++;
        end
        if (state_next[0][1] !== '1 || state_next[1][1] !== '1) begin
            $error("Unscheduled blocks changed unexpectedly");
            errors++;
        end

        if (errors == 0)
            $display("PASS: H1 tile routed symmetric cross-H0 partials correctly");
        else
            $fatal(1, "FAIL: H1 tile produced %0d errors", errors);
        $finish;
    end

    initial begin
        #200000;
        $fatal(1, "FAIL: H1 tile timeout");
    end
endmodule

