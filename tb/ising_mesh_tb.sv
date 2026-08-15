`timescale 1ns/1ps

import ising_pkg::*;

// Full-system testbench scaffold.
//
// This file only declares and instantiates the DUT, initializes every input,
// and generates clock/reset. Add schedule, weight, publication, checking, and
// performance-monitoring code in the marked sections below.
module ising_mesh_tb;
    localparam int CLK_PERIOD = 10;

    // Small configuration that still supports all four ownership levels:
    // core-local, H0, H1, and cross-H1.
    localparam int MESH_X_COUNT      = 2;
    localparam int MESH_Y_COUNT      = 1;
    localparam int H0_COUNT          = 2;
    localparam int CORES_PER_H0      = 2;
    localparam int H0_MVM_COUNT      = 1;
    localparam int H1_MVM_COUNT      = 1;
    localparam int CROSS_MVM_COUNT   = 1;
    localparam int GLOBAL_BLOCK_ID_W = 8;
    localparam int X_W               = 1;
    localparam int Y_W               = 1;
    localparam int SOURCE_ID_W       = 4;
    localparam int EPOCH_W           = 8;
    localparam int FIFO_DEPTH        = 4;
    localparam int NODE_COUNT        = MESH_X_COUNT * MESH_Y_COUNT;
    localparam int BLOCKS_PER_H1     = H0_COUNT * CORES_PER_H0;
    localparam int TOP_STATE_ENTRY_COUNT = NODE_COUNT * BLOCKS_PER_H1;
    localparam int H0_STATE_INDEX_W =
        (CORES_PER_H0 > 1) ? $clog2(CORES_PER_H0) : 1;
    localparam int H1_STATE_INDEX_W =
        (BLOCKS_PER_H1 > 1) ? $clog2(BLOCKS_PER_H1) : 1;
    localparam int TOP_STATE_INDEX_W =
        (TOP_STATE_ENTRY_COUNT > 1) ? $clog2(TOP_STATE_ENTRY_COUNT) : 1;

    logic clk;
    logic rst;

    logic [NODE_COUNT-1:0] init_start_i;
    logic [NODE_COUNT-1:0] init_done_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0]
                                                            core_weight_valid_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0]
                                                            core_weight_ready_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][DATA_W-1:0]
                                                            core_weight_data_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
                                                            init_state_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][31:0]
                                                            noise_seed_i;
    logic signed [NODE_COUNT-1:0][COEFF_W-1:0] coeff_a_i;
    logic signed [NODE_COUNT-1:0][COEFF_W-1:0] coeff_b_i;
    logic signed [NODE_COUNT-1:0][COEFF_W-1:0] coeff_c_i;
    logic signed [NODE_COUNT-1:0][COEFF_W-1:0] noise_amplitude_i;

    logic [NODE_COUNT-1:0] iter_start_i;
    logic [NODE_COUNT-1:0] iter_done_o;
    logic [NODE_COUNT-1:0] commit_i;
    logic [NODE_COUNT-1:0] done_i;
    logic [NODE_COUNT-1:0][16:0] noise_decay_i;
    logic [NODE_COUNT-1:0][EPOCH_W-1:0] epoch_i;

    logic [NODE_COUNT-1:0][H0_COUNT-1:0] h0_schedule_done_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                                                            h0_dma_cmd_valid_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                                                            h0_dma_cmd_ready_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
          [H0_STATE_INDEX_W-1:0] h0_dma_state_a_index_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
          [H0_STATE_INDEX_W-1:0] h0_dma_state_b_index_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
          [GLOBAL_BLOCK_ID_W-1:0] h0_dma_block_a_id_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
          [GLOBAL_BLOCK_ID_W-1:0] h0_dma_block_b_id_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                                                            h0_dma_weight_valid_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                                                            h0_dma_weight_ready_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0][DATA_W-1:0]
                                                            h0_dma_weight_data_i;

    logic [NODE_COUNT-1:0] h1_schedule_done_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_dma_cmd_valid_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_dma_cmd_ready_o;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][H1_STATE_INDEX_W-1:0]
                                                   h1_dma_state_a_index_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][H1_STATE_INDEX_W-1:0]
                                                   h1_dma_state_b_index_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                   h1_dma_block_a_id_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                   h1_dma_block_b_id_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_dma_weight_valid_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_dma_weight_ready_o;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][DATA_W-1:0]
                                                   h1_dma_weight_data_i;

    logic [NODE_COUNT-1:0] state_publish_valid_i;
    logic [NODE_COUNT-1:0] state_publish_ready_o;
    logic [NODE_COUNT-1:0][H1_STATE_INDEX_W-1:0]
                                                   state_publish_local_index_i;
    logic [NODE_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                   state_publish_block_id_i;
    logic [NODE_COUNT-1:0][X_W-1:0] state_publish_dest_x_i;
    logic [NODE_COUNT-1:0][Y_W-1:0] state_publish_dest_y_i;
    logic [NODE_COUNT-1:0] done_publish_valid_i;
    logic [NODE_COUNT-1:0] done_publish_ready_o;
    logic [NODE_COUNT-1:0][X_W-1:0] done_publish_dest_x_i;
    logic [NODE_COUNT-1:0][Y_W-1:0] done_publish_dest_y_i;

    logic [NODE_COUNT-1:0] cross_iter_start_i;
    logic [NODE_COUNT-1:0] cross_iter_done_o;
    logic [NODE_COUNT-1:0] cross_schedule_done_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_dma_cmd_valid_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_dma_cmd_ready_o;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][TOP_STATE_INDEX_W-1:0]
                                                   cross_dma_state_a_index_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][TOP_STATE_INDEX_W-1:0]
                                                   cross_dma_state_b_index_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                   cross_dma_block_a_id_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                   cross_dma_block_b_id_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_dma_weight_valid_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_dma_weight_ready_o;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][DATA_W-1:0]
                                                   cross_dma_weight_data_i;

    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
                                                   state_current_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
                                                   state_next_o;

    ising_mesh #(
        .MESH_X_COUNT(MESH_X_COUNT),
        .MESH_Y_COUNT(MESH_Y_COUNT),
        .H0_COUNT(H0_COUNT),
        .CORES_PER_H0(CORES_PER_H0),
        .H0_MVM_COUNT(H0_MVM_COUNT),
        .H1_MVM_COUNT(H1_MVM_COUNT),
        .CROSS_MVM_COUNT(CROSS_MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .X_W(X_W),
        .Y_W(Y_W),
        .SOURCE_ID_W(SOURCE_ID_W),
        .EPOCH_W(EPOCH_W),
        .FIFO_DEPTH(FIFO_DEPTH),
        .NODE_COUNT(NODE_COUNT),
        .BLOCKS_PER_H1(BLOCKS_PER_H1),
        .TOP_STATE_ENTRY_COUNT(TOP_STATE_ENTRY_COUNT)
    ) dut (.*);

    // 100 MHz for CLK_PERIOD=10 ns. Change CLK_PERIOD to model another RTL
    // clock; memory delivery timing remains controlled by testbench stimulus.
    initial clk = 1'b0;
    always #(CLK_PERIOD/2) clk = ~clk;

    initial begin
        rst = 1'b1;

        // Drive every DUT input to a known inactive value before releasing
        // reset. Individual stimulus tasks can override these signals later.
        init_start_i = '0;
        core_weight_valid_i = '0;
        core_weight_data_i = '0;
        init_state_i = '0;
        noise_seed_i = '0;
        coeff_a_i = '0;
        coeff_b_i = '0;
        coeff_c_i = '0;
        noise_amplitude_i = '0;
        iter_start_i = '0;
        commit_i = '0;
        done_i = '0;
        noise_decay_i = '0;
        epoch_i = '0;

        h0_schedule_done_i = '0;
        h0_dma_cmd_valid_i = '0;
        h0_dma_state_a_index_i = '0;
        h0_dma_state_b_index_i = '0;
        h0_dma_block_a_id_i = '0;
        h0_dma_block_b_id_i = '0;
        h0_dma_weight_valid_i = '0;
        h0_dma_weight_data_i = '0;

        h1_schedule_done_i = '0;
        h1_dma_cmd_valid_i = '0;
        h1_dma_state_a_index_i = '0;
        h1_dma_state_b_index_i = '0;
        h1_dma_block_a_id_i = '0;
        h1_dma_block_b_id_i = '0;
        h1_dma_weight_valid_i = '0;
        h1_dma_weight_data_i = '0;

        state_publish_valid_i = '0;
        state_publish_local_index_i = '0;
        state_publish_block_id_i = '0;
        state_publish_dest_x_i = '0;
        state_publish_dest_y_i = '0;
        done_publish_valid_i = '0;
        done_publish_dest_x_i = '0;
        done_publish_dest_y_i = '0;

        cross_iter_start_i = '0;
        cross_schedule_done_i = '0;
        cross_dma_cmd_valid_i = '0;
        cross_dma_state_a_index_i = '0;
        cross_dma_state_b_index_i = '0;
        cross_dma_block_a_id_i = '0;
        cross_dma_block_b_id_i = '0;
        cross_dma_weight_valid_i = '0;
        cross_dma_weight_data_i = '0;

        // Hold synchronous reset through several complete clock edges.
        repeat (5) @(negedge clk);
        rst = 1'b0;
        @(negedge clk);

        // -----------------------------------------------------------------
        // Add testbench sequence here:
        //   1. Set coefficients, initial states, and noise seeds.
        //   2. Pulse init_start_i and stream each core-local J block.
        //   3. Wait for init_done_o.
        //   4. Pulse iter_start_i and cross_iter_start_i.
        //   5. Publish required state blocks.
        //   6. Stream H0, H1, and cross-H1 commands/weights.
        //   7. Assert the three schedule_done groups.
        //   8. Complete the parent epoch after network traffic drains.
        //   9. Wait for iter_done_o and compare state_next_o.
        //  10. Pulse commit_i.
        // -----------------------------------------------------------------

        $display("ising_mesh_tb scaffold reset complete; add stimulus after this point");
        $finish;
    end

    // Add reusable ready/valid driver tasks here.
    // Add golden-model comparison code here.
    // Add NoC performance monitors or hierarchical probes here.
endmodule
