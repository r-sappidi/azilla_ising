`timescale 1ns/1ps

import ising_pkg::*;

module h0_tile_tb;
    localparam int CORE_COUNT = 2;
    localparam int MVM_COUNT = 1;
    localparam int GLOBAL_BLOCK_ID_W = 8;

    logic clk = 0;
    logic rst = 1;
    logic init_start, init_done;
    logic [CORE_COUNT-1:0] core_weight_valid, core_weight_ready;
    logic [CORE_COUNT-1:0][DATA_W-1:0] core_weight_data;
    logic [CORE_COUNT-1:0][SPIN_COUNT-1:0] init_state;
    logic [CORE_COUNT-1:0][31:0] noise_seed;
    logic signed [COEFF_W-1:0] coeff_a, coeff_b, coeff_c, noise_amplitude;
    logic iter_start, iter_done, commit, done;
    logic [16:0] noise_decay;
    logic schedule_done;
    logic [MVM_COUNT-1:0] cmd_valid, cmd_ready;
    logic [MVM_COUNT-1:0][$clog2(CORE_COUNT)-1:0] state_a_index, state_b_index;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] block_a_id, block_b_id;
    logic [MVM_COUNT-1:0] weight_valid, weight_ready;
    logic [MVM_COUNT-1:0][DATA_W-1:0] weight_data;
    logic ext_partial_valid, ext_partial_ready, ext_partials_done;
    logic [GLOBAL_BLOCK_ID_W-1:0] ext_partial_block_id;
    logic signed [DATA_W-1:0] ext_partial_data;
    logic [CORE_COUNT-1:0][SPIN_COUNT-1:0] state_current, state_next;
    int errors;

    h0_tile #(
        .CORE_COUNT(CORE_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .BASE_BLOCK_ID(0)
    ) dut (.*,
        .core_weight_valid_i(core_weight_valid),
        .core_weight_ready_o(core_weight_ready),
        .core_weight_data_i(core_weight_data),
        .init_state_i(init_state),
        .noise_seed_i(noise_seed),
        .coeff_a_i(coeff_a), .coeff_b_i(coeff_b), .coeff_c_i(coeff_c),
        .noise_amplitude_i(noise_amplitude),
        .noise_decay_i(noise_decay),
        .schedule_done_i(schedule_done),
        .dma_cmd_valid_i(cmd_valid), .dma_cmd_ready_o(cmd_ready),
        .dma_state_a_index_i(state_a_index),
        .dma_state_b_index_i(state_b_index),
        .dma_block_a_id_i(block_a_id), .dma_block_b_id_i(block_b_id),
        .dma_weight_valid_i(weight_valid), .dma_weight_ready_o(weight_ready),
        .dma_weight_data_i(weight_data),
        .ext_partial_valid_i(ext_partial_valid),
        .ext_partial_ready_o(ext_partial_ready),
        .ext_partial_block_id_i(ext_partial_block_id),
        .ext_partial_data_i(ext_partial_data),
        .ext_partials_done_i(ext_partials_done),
        .state_current_o(state_current), .state_next_o(state_next)
    );

    always #5 clk = ~clk;

    initial begin
        init_start = 0;
        core_weight_valid = '0;
        core_weight_data = '0;
        init_state[0] = '1;
        init_state[1] = '0;
        noise_seed = '0;
        coeff_a = 0;
        coeff_b = 1;
        coeff_c = 0;
        noise_amplitude = 0;
        noise_decay = 0;
        iter_start = 0;
        commit = 0;
        done = 0;
        schedule_done = 0;
        cmd_valid = '0;
        state_a_index = '0;
        state_b_index = '0;
        block_a_id = '0;
        block_b_id = '0;
        weight_valid = '0;
        weight_data = '0;
        ext_partial_valid = 0;
        ext_partial_block_id = '0;
        ext_partial_data = '0;
        ext_partials_done = 0;
        errors = 0;

        repeat (4) @(negedge clk);
        rst = 0;
        init_start = 1;
        @(negedge clk);
        init_start = 0;

        // Load zero-valued diagonal J blocks into both cores.
        core_weight_valid = '1;
        for (int beat = 0; beat < SPIN_COUNT; beat++) begin
            while (core_weight_ready != '1) @(negedge clk);
            @(negedge clk);
        end
        core_weight_valid = '0;
        wait (init_done);
        $display("H0 tile test: initialization complete at %0t", $time);

        @(negedge clk);
        iter_start = 1;
        @(negedge clk);
        iter_start = 0;

        // Wait for the tile to copy frozen core states and start its node.
        state_a_index[0] = 0;
        state_b_index[0] = 1;
        block_a_id[0] = 0;
        block_b_id[0] = 1;
        cmd_valid[0] = 1;
        while (!cmd_ready[0]) @(negedge clk);
        $display("H0 tile test: command accepted at %0t", $time);
        @(negedge clk);
        cmd_valid[0] = 0;

        // All +1 weights: all-negative x_B gives -32 to A, while all-positive
        // x_A gives +32 to B.
        weight_data[0] = {DATA_W/WEIGHT_W{8'h01}};
        weight_valid[0] = 1;
        for (int beat = 0; beat < SPIN_COUNT; beat++) begin
            while (!weight_ready[0]) @(negedge clk);
            @(negedge clk);
        end
        weight_valid[0] = 0;
        $display("H0 tile test: weight block loaded at %0t", $time);

        schedule_done = 1;
        ext_partials_done = 1;
        @(negedge clk);
        schedule_done = 0;
        ext_partials_done = 0;

        wait (iter_done);
        $display("H0 tile test: iteration complete at %0t", $time);
        @(negedge clk);
        if (state_next[0] !== '0) begin
            $error("core zero next state was %h, expected all zero", state_next[0]);
            errors++;
        end
        if (state_next[1] !== '1) begin
            $error("core one next state was %h, expected all one", state_next[1]);
            errors++;
        end

        if (errors == 0)
            $display("PASS: H0 tile routed both cross-core partials correctly");
        else
            $fatal(1, "FAIL: H0 tile produced %0d errors", errors);
        $finish;
    end

    initial begin
        #100000;
        $fatal(1, "FAIL: H0 tile timeout");
    end
endmodule
