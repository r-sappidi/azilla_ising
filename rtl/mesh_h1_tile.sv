import ising_pkg::*;

// One complete mesh location. This wrapper contains the entire arithmetic
// hierarchy below one H1, its H1/NoC protocol adapter, the local cross-H1
// compute endpoint, and the mesh router. Schedule and memory traffic remain
// external so a testbench can drive them directly.
module mesh_h1_tile #(
    parameter int H0_COUNT             = 16,
    parameter int CORES_PER_H0         = 32,
    parameter int H0_MVM_COUNT         = 16,
    parameter int H1_MVM_COUNT         = 16,
    parameter int CROSS_MVM_COUNT      = 16,
    parameter int TOP_STATE_ENTRY_COUNT = 32768,
    parameter int GLOBAL_BLOCK_ID_W    = 16,
    parameter int X_W                  = 3,
    parameter int Y_W                  = 3,
    parameter int SOURCE_ID_W          = 8,
    parameter int EPOCH_W              = 16,
    parameter int MESH_X_COUNT         = 8,
    parameter int FIFO_DEPTH           = 8,
    parameter int NODE_ID              = 0,
    parameter int NODE_X               = 0,
    parameter int NODE_Y               = 0,
    parameter int BASE_BLOCK_ID        = 0
) (
    input logic clk,
    input logic rst,

    input  logic init_start_i,
    output logic init_done_o,
    input  logic [H0_COUNT-1:0][CORES_PER_H0-1:0]                 core_weight_valid_i,
    output logic [H0_COUNT-1:0][CORES_PER_H0-1:0]                 core_weight_ready_o,
    input  logic [H0_COUNT-1:0][CORES_PER_H0-1:0][DATA_W-1:0]     core_weight_data_i,
    input  logic [H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0] init_state_i,
    input  logic [H0_COUNT-1:0][CORES_PER_H0-1:0][31:0]           noise_seed_i,
    input  logic signed [COEFF_W-1:0]                             coeff_a_i,
    input  logic signed [COEFF_W-1:0]                             coeff_b_i,
    input  logic signed [COEFF_W-1:0]                             coeff_c_i,
    input  logic signed [COEFF_W-1:0]                             noise_amplitude_i,

    input  logic iter_start_i,
    output logic iter_done_o,
    input  logic commit_i,
    input  logic done_i,
    input  logic [16:0] noise_decay_i,
    input  logic [EPOCH_W-1:0] epoch_i,

    // H0 schedule/weight stimulus.
    input  logic [H0_COUNT-1:0]                                  h0_schedule_done_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]                 h0_dma_cmd_valid_i,
    output logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]                 h0_dma_cmd_ready_o,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                 [((CORES_PER_H0 > 1) ? $clog2(CORES_PER_H0) : 1)-1:0]
                                                                   h0_dma_state_a_index_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                 [((CORES_PER_H0 > 1) ? $clog2(CORES_PER_H0) : 1)-1:0]
                                                                   h0_dma_state_b_index_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                                   h0_dma_block_a_id_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                                   h0_dma_block_b_id_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]                 h0_dma_weight_valid_i,
    output logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]                 h0_dma_weight_ready_o,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0][DATA_W-1:0]     h0_dma_weight_data_i,

    // H1 schedule/weight stimulus.
    input  logic h1_schedule_done_i,
    input  logic [H1_MVM_COUNT-1:0] h1_dma_cmd_valid_i,
    output logic [H1_MVM_COUNT-1:0] h1_dma_cmd_ready_o,
    input  logic [H1_MVM_COUNT-1:0]
                 [(((H0_COUNT*CORES_PER_H0) > 1) ?
                    $clog2(H0_COUNT*CORES_PER_H0) : 1)-1:0] h1_dma_state_a_index_i,
    input  logic [H1_MVM_COUNT-1:0]
                 [(((H0_COUNT*CORES_PER_H0) > 1) ?
                    $clog2(H0_COUNT*CORES_PER_H0) : 1)-1:0] h1_dma_state_b_index_i,
    input  logic [H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] h1_dma_block_a_id_i,
    input  logic [H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] h1_dma_block_b_id_i,
    input  logic [H1_MVM_COUNT-1:0] h1_dma_weight_valid_i,
    output logic [H1_MVM_COUNT-1:0] h1_dma_weight_ready_o,
    input  logic [H1_MVM_COUNT-1:0][DATA_W-1:0] h1_dma_weight_data_i,

    // Testbench-selected state publication. The local index selects directly
    // from this H1 tile's frozen distributed state vector.
    input  logic state_publish_valid_i,
    output logic state_publish_ready_o,
    input  logic [(((H0_COUNT*CORES_PER_H0) > 1) ?
                   $clog2(H0_COUNT*CORES_PER_H0) : 1)-1:0]
                                                state_publish_local_index_i,
    input  logic [GLOBAL_BLOCK_ID_W-1:0]        state_publish_block_id_i,
    input  logic [X_W-1:0]                      state_publish_dest_x_i,
    input  logic [Y_W-1:0]                      state_publish_dest_y_i,

    // Testbench/global-barrier completion injection.
    input  logic done_publish_valid_i,
    output logic done_publish_ready_o,
    input  logic [X_W-1:0] done_publish_dest_x_i,
    input  logic [Y_W-1:0] done_publish_dest_y_i,

    // Cross-H1 schedule/weight stimulus for this mesh location.
    input  logic cross_iter_start_i,
    output logic cross_iter_done_o,
    input  logic cross_schedule_done_i,
    input  logic [CROSS_MVM_COUNT-1:0] cross_dma_cmd_valid_i,
    output logic [CROSS_MVM_COUNT-1:0] cross_dma_cmd_ready_o,
    input  logic [CROSS_MVM_COUNT-1:0]
                 [((TOP_STATE_ENTRY_COUNT > 1) ? $clog2(TOP_STATE_ENTRY_COUNT) : 1)-1:0]
                                                cross_dma_state_a_index_i,
    input  logic [CROSS_MVM_COUNT-1:0]
                 [((TOP_STATE_ENTRY_COUNT > 1) ? $clog2(TOP_STATE_ENTRY_COUNT) : 1)-1:0]
                                                cross_dma_state_b_index_i,
    input  logic [CROSS_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] cross_dma_block_a_id_i,
    input  logic [CROSS_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] cross_dma_block_b_id_i,
    input  logic [CROSS_MVM_COUNT-1:0] cross_dma_weight_valid_i,
    output logic [CROSS_MVM_COUNT-1:0] cross_dma_weight_ready_o,
    input  logic [CROSS_MVM_COUNT-1:0][DATA_W-1:0] cross_dma_weight_data_i,

    // Cardinal mesh links: 0=north, 1=south, 2=east, 3=west.
    input  logic [3:0] link_in_valid_i,
    output logic [3:0] link_in_ready_o,
    input  logic [3:0][DATA_W-1:0] link_in_data_i,
    input  logic [3:0][1:0] link_in_type_i,
    input  logic [3:0][X_W-1:0] link_in_dest_x_i,
    input  logic [3:0][Y_W-1:0] link_in_dest_y_i,
    input  logic [3:0][SOURCE_ID_W-1:0] link_in_source_id_i,
    input  logic [3:0][EPOCH_W-1:0] link_in_epoch_i,
    input  logic [3:0][GLOBAL_BLOCK_ID_W-1:0] link_in_block_id_i,
    input  logic [3:0] link_in_last_i,
    output logic [3:0] link_out_valid_o,
    input  logic [3:0] link_out_ready_i,
    output logic [3:0][DATA_W-1:0] link_out_data_o,
    output logic [3:0][1:0] link_out_type_o,
    output logic [3:0][X_W-1:0] link_out_dest_x_o,
    output logic [3:0][Y_W-1:0] link_out_dest_y_o,
    output logic [3:0][SOURCE_ID_W-1:0] link_out_source_id_o,
    output logic [3:0][EPOCH_W-1:0] link_out_epoch_o,
    output logic [3:0][GLOBAL_BLOCK_ID_W-1:0] link_out_block_id_o,
    output logic [3:0] link_out_last_o,

    output logic [H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0] state_current_o,
    output logic [H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0] state_next_o
);
    localparam int BLOCKS_PER_H1 = H0_COUNT * CORES_PER_H0;
    localparam int H0_INDEX_W = (H0_COUNT > 1) ? $clog2(H0_COUNT) : 1;
    localparam int CORE_INDEX_W = (CORES_PER_H0 > 1) ? $clog2(CORES_PER_H0) : 1;

    logic [H0_INDEX_W-1:0] publish_h0_index;
    logic [CORE_INDEX_W-1:0] publish_core_index;
    logic [SPIN_COUNT-1:0] publish_state;

    logic h1_tx_valid, h1_tx_ready;
    logic [DATA_W-1:0] h1_tx_data;
    logic [1:0] h1_tx_type;
    logic [X_W-1:0] h1_tx_dest_x;
    logic [Y_W-1:0] h1_tx_dest_y;
    logic [SOURCE_ID_W-1:0] h1_tx_source_id;
    logic [EPOCH_W-1:0] h1_tx_epoch;
    logic [GLOBAL_BLOCK_ID_W-1:0] h1_tx_block_id;
    logic h1_tx_last;
    logic h1_rx_valid, h1_rx_ready;
    logic [DATA_W-1:0] h1_rx_data;
    logic [1:0] h1_rx_type;
    logic [SOURCE_ID_W-1:0] h1_rx_source_id;
    logic [EPOCH_W-1:0] h1_rx_epoch;
    logic [GLOBAL_BLOCK_ID_W-1:0] h1_rx_block_id;
    logic h1_rx_last;

    logic parent_partial_valid, parent_partial_ready;
    logic signed [DATA_W-1:0] parent_partial_data;
    logic [GLOBAL_BLOCK_ID_W-1:0] parent_partial_block_id;
    logic parent_partials_done;

    assign publish_h0_index = H0_INDEX_W'(int'(state_publish_local_index_i) /
                                           CORES_PER_H0);
    assign publish_core_index = CORE_INDEX_W'(int'(state_publish_local_index_i) %
                                               CORES_PER_H0);
    assign publish_state = state_current_o[publish_h0_index][publish_core_index];

    h1_tile #(
        .H0_COUNT(H0_COUNT), .CORES_PER_H0(CORES_PER_H0),
        .H0_MVM_COUNT(H0_MVM_COUNT), .H1_MVM_COUNT(H1_MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W), .BASE_BLOCK_ID(BASE_BLOCK_ID)
    ) h1 (
        .clk, .rst, .init_start(init_start_i), .init_done(init_done_o),
        .core_weight_valid_i, .core_weight_ready_o, .core_weight_data_i,
        .init_state_i, .noise_seed_i, .coeff_a_i, .coeff_b_i, .coeff_c_i,
        .noise_amplitude_i, .iter_start(iter_start_i), .iter_done(iter_done_o),
        .commit(commit_i), .done(done_i), .noise_decay_i,
        .h0_schedule_done_i, .h0_dma_cmd_valid_i, .h0_dma_cmd_ready_o,
        .h0_dma_state_a_index_i, .h0_dma_state_b_index_i,
        .h0_dma_block_a_id_i, .h0_dma_block_b_id_i,
        .h0_dma_weight_valid_i, .h0_dma_weight_ready_o, .h0_dma_weight_data_i,
        .h1_schedule_done_i, .h1_dma_cmd_valid_i, .h1_dma_cmd_ready_o,
        .h1_dma_state_a_index_i, .h1_dma_state_b_index_i,
        .h1_dma_block_a_id_i, .h1_dma_block_b_id_i,
        .h1_dma_weight_valid_i, .h1_dma_weight_ready_o, .h1_dma_weight_data_i,
        .parent_partial_valid_i(parent_partial_valid),
        .parent_partial_ready_o(parent_partial_ready),
        .parent_partial_block_id_i(parent_partial_block_id),
        .parent_partial_data_i(parent_partial_data),
        .parent_partials_done_i(parent_partials_done),
        .state_current_o, .state_next_o
    );

    h1_noc_adapter #(
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W), .X_W(X_W), .Y_W(Y_W),
        .SOURCE_ID_W(SOURCE_ID_W), .EPOCH_W(EPOCH_W), .SOURCE_ID(NODE_ID)
    ) noc_adapter (
        .clk, .rst, .current_epoch_i(epoch_i),
        .state_publish_valid_i, .state_publish_ready_o,
        .state_publish_data_i(publish_state), .state_publish_block_id_i,
        .state_publish_dest_x_i, .state_publish_dest_y_i,
        .done_publish_valid_i, .done_publish_ready_o,
        .done_publish_dest_x_i, .done_publish_dest_y_i,
        .noc_tx_valid_o(h1_tx_valid), .noc_tx_ready_i(h1_tx_ready),
        .noc_tx_data_o(h1_tx_data), .noc_tx_type_o(h1_tx_type),
        .noc_tx_dest_x_o(h1_tx_dest_x), .noc_tx_dest_y_o(h1_tx_dest_y),
        .noc_tx_source_id_o(h1_tx_source_id), .noc_tx_epoch_o(h1_tx_epoch),
        .noc_tx_block_id_o(h1_tx_block_id), .noc_tx_last_o(h1_tx_last),
        .noc_rx_valid_i(h1_rx_valid), .noc_rx_ready_o(h1_rx_ready),
        .noc_rx_data_i(h1_rx_data), .noc_rx_type_i(h1_rx_type),
        .noc_rx_source_id_i(h1_rx_source_id), .noc_rx_epoch_i(h1_rx_epoch),
        .noc_rx_block_id_i(h1_rx_block_id), .noc_rx_last_i(h1_rx_last),
        .parent_partial_valid_o(parent_partial_valid),
        .parent_partial_ready_i(parent_partial_ready),
        .parent_partial_data_o(parent_partial_data),
        .parent_partial_block_id_o(parent_partial_block_id),
        .parent_partials_done_o(parent_partials_done)
    );

    top_node #(
        .STATE_ENTRY_COUNT(TOP_STATE_ENTRY_COUNT), .MVM_COUNT(CROSS_MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W), .X_W(X_W), .Y_W(Y_W),
        .SOURCE_ID_W(SOURCE_ID_W), .EPOCH_W(EPOCH_W),
        .BLOCKS_PER_H1(BLOCKS_PER_H1), .MESH_X_COUNT(MESH_X_COUNT),
        .FIFO_DEPTH(FIFO_DEPTH), .NODE_ID(NODE_ID), .NODE_X(NODE_X),
        .NODE_Y(NODE_Y)
    ) top (
        .clk, .rst, .link_in_valid_i, .link_in_ready_o, .link_in_data_i,
        .link_in_type_i, .link_in_dest_x_i, .link_in_dest_y_i,
        .link_in_source_id_i, .link_in_epoch_i, .link_in_block_id_i,
        .link_in_last_i, .link_out_valid_o, .link_out_ready_i,
        .link_out_data_o, .link_out_type_o, .link_out_dest_x_o,
        .link_out_dest_y_o, .link_out_source_id_o, .link_out_epoch_o,
        .link_out_block_id_o, .link_out_last_o,
        .h1_tx_valid_i(h1_tx_valid), .h1_tx_ready_o(h1_tx_ready),
        .h1_tx_data_i(h1_tx_data), .h1_tx_type_i(h1_tx_type),
        .h1_tx_dest_x_i(h1_tx_dest_x), .h1_tx_dest_y_i(h1_tx_dest_y),
        .h1_tx_source_id_i(h1_tx_source_id), .h1_tx_epoch_i(h1_tx_epoch),
        .h1_tx_block_id_i(h1_tx_block_id), .h1_tx_last_i(h1_tx_last),
        .h1_rx_valid_o(h1_rx_valid), .h1_rx_ready_i(h1_rx_ready),
        .h1_rx_data_o(h1_rx_data), .h1_rx_type_o(h1_rx_type),
        .h1_rx_source_id_o(h1_rx_source_id), .h1_rx_epoch_o(h1_rx_epoch),
        .h1_rx_block_id_o(h1_rx_block_id), .h1_rx_last_o(h1_rx_last),
        .cross_iter_start_i, .cross_iter_done_o, .epoch_i,
        .cross_schedule_done_i, .cross_dma_cmd_valid_i,
        .cross_dma_cmd_ready_o, .cross_dma_state_a_index_i,
        .cross_dma_state_b_index_i, .cross_dma_block_a_id_i,
        .cross_dma_block_b_id_i, .cross_dma_weight_valid_i,
        .cross_dma_weight_ready_o, .cross_dma_weight_data_i
    );
endmodule
