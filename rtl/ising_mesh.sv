import ising_pkg::*;

// Parameterized rectangular mesh of complete H1 locations. All scheduling and
// memory streams are exposed per node for direct testbench stimulation.
module ising_mesh #(
    parameter int MESH_X_COUNT          = 2,
    parameter int MESH_Y_COUNT          = 2,
    parameter int H0_COUNT              = 2,
    parameter int CORES_PER_H0          = 2,
    parameter int H0_MVM_COUNT          = 1,
    parameter int H1_MVM_COUNT          = 1,
    parameter int CROSS_MVM_COUNT       = 1,
    parameter int GLOBAL_BLOCK_ID_W     = 16,
    parameter int X_W                   = (MESH_X_COUNT > 1) ? $clog2(MESH_X_COUNT) : 1,
    parameter int Y_W                   = (MESH_Y_COUNT > 1) ? $clog2(MESH_Y_COUNT) : 1,
    parameter int SOURCE_ID_W           = 8,
    parameter int EPOCH_W               = 16,
    parameter int FIFO_DEPTH            = 8,
    parameter int NODE_COUNT            = MESH_X_COUNT * MESH_Y_COUNT,
    parameter int BLOCKS_PER_H1          = H0_COUNT * CORES_PER_H0,
    parameter int TOP_STATE_ENTRY_COUNT = NODE_COUNT * BLOCKS_PER_H1
) (
    input logic clk,
    input logic rst,

    input  logic [NODE_COUNT-1:0] init_start_i,
    output logic [NODE_COUNT-1:0] init_done_o,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0]
                                                        core_weight_valid_i,
    output logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0]
                                                        core_weight_ready_o,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][DATA_W-1:0]
                                                        core_weight_data_i,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
                                                        init_state_i,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][31:0]
                                                        noise_seed_i,
    input  logic signed [NODE_COUNT-1:0][COEFF_W-1:0] coeff_a_i,
    input  logic signed [NODE_COUNT-1:0][COEFF_W-1:0] coeff_b_i,
    input  logic signed [NODE_COUNT-1:0][COEFF_W-1:0] coeff_c_i,
    input  logic signed [NODE_COUNT-1:0][COEFF_W-1:0] noise_amplitude_i,
    input  logic [NODE_COUNT-1:0] iter_start_i,
    output logic [NODE_COUNT-1:0] iter_done_o,
    input  logic [NODE_COUNT-1:0] commit_i,
    input  logic [NODE_COUNT-1:0] done_i,
    input  logic [NODE_COUNT-1:0][16:0] noise_decay_i,
    input  logic [NODE_COUNT-1:0][EPOCH_W-1:0] epoch_i,

    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0] h0_schedule_done_i,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                                                        h0_dma_cmd_valid_i,
    output logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                                                        h0_dma_cmd_ready_o,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                 [((CORES_PER_H0 > 1) ? $clog2(CORES_PER_H0) : 1)-1:0]
                                                        h0_dma_state_a_index_i,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                 [((CORES_PER_H0 > 1) ? $clog2(CORES_PER_H0) : 1)-1:0]
                                                        h0_dma_state_b_index_i,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                 [GLOBAL_BLOCK_ID_W-1:0] h0_dma_block_a_id_i,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                 [GLOBAL_BLOCK_ID_W-1:0] h0_dma_block_b_id_i,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                                                        h0_dma_weight_valid_i,
    output logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                                                        h0_dma_weight_ready_o,
    input  logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0][DATA_W-1:0]
                                                        h0_dma_weight_data_i,

    input  logic [NODE_COUNT-1:0] h1_schedule_done_i,
    input  logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_dma_cmd_valid_i,
    output logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_dma_cmd_ready_o,
    input  logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0]
                 [((BLOCKS_PER_H1 > 1) ? $clog2(BLOCKS_PER_H1) : 1)-1:0]
                                                        h1_dma_state_a_index_i,
    input  logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0]
                 [((BLOCKS_PER_H1 > 1) ? $clog2(BLOCKS_PER_H1) : 1)-1:0]
                                                        h1_dma_state_b_index_i,
    input  logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                        h1_dma_block_a_id_i,
    input  logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                        h1_dma_block_b_id_i,
    input  logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_dma_weight_valid_i,
    output logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_dma_weight_ready_o,
    input  logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][DATA_W-1:0]
                                                        h1_dma_weight_data_i,

    input  logic [NODE_COUNT-1:0] state_publish_valid_i,
    output logic [NODE_COUNT-1:0] state_publish_ready_o,
    input  logic [NODE_COUNT-1:0]
                 [((BLOCKS_PER_H1 > 1) ? $clog2(BLOCKS_PER_H1) : 1)-1:0]
                                                        state_publish_local_index_i,
    input  logic [NODE_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] state_publish_block_id_i,
    input  logic [NODE_COUNT-1:0][X_W-1:0] state_publish_dest_x_i,
    input  logic [NODE_COUNT-1:0][Y_W-1:0] state_publish_dest_y_i,
    input  logic [NODE_COUNT-1:0] done_publish_valid_i,
    output logic [NODE_COUNT-1:0] done_publish_ready_o,
    input  logic [NODE_COUNT-1:0][X_W-1:0] done_publish_dest_x_i,
    input  logic [NODE_COUNT-1:0][Y_W-1:0] done_publish_dest_y_i,

    input  logic [NODE_COUNT-1:0] cross_iter_start_i,
    output logic [NODE_COUNT-1:0] cross_iter_done_o,
    input  logic [NODE_COUNT-1:0] cross_schedule_done_i,
    input  logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_dma_cmd_valid_i,
    output logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_dma_cmd_ready_o,
    input  logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0]
                 [((TOP_STATE_ENTRY_COUNT > 1) ? $clog2(TOP_STATE_ENTRY_COUNT) : 1)-1:0]
                                                        cross_dma_state_a_index_i,
    input  logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0]
                 [((TOP_STATE_ENTRY_COUNT > 1) ? $clog2(TOP_STATE_ENTRY_COUNT) : 1)-1:0]
                                                        cross_dma_state_b_index_i,
    input  logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                        cross_dma_block_a_id_i,
    input  logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                        cross_dma_block_b_id_i,
    input  logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_dma_weight_valid_i,
    output logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_dma_weight_ready_o,
    input  logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][DATA_W-1:0]
                                                        cross_dma_weight_data_i,

    output logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
                                                        state_current_o,
    output logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
                                                        state_next_o
);
    localparam int NORTH = 0;
    localparam int SOUTH = 1;
    localparam int EAST  = 2;
    localparam int WEST  = 3;

    logic [NODE_COUNT-1:0][3:0] link_in_valid, link_in_ready;
    logic [NODE_COUNT-1:0][3:0][DATA_W-1:0] link_in_data;
    logic [NODE_COUNT-1:0][3:0][1:0] link_in_type;
    logic [NODE_COUNT-1:0][3:0][X_W-1:0] link_in_dest_x;
    logic [NODE_COUNT-1:0][3:0][Y_W-1:0] link_in_dest_y;
    logic [NODE_COUNT-1:0][3:0][SOURCE_ID_W-1:0] link_in_source_id;
    logic [NODE_COUNT-1:0][3:0][EPOCH_W-1:0] link_in_epoch;
    logic [NODE_COUNT-1:0][3:0][GLOBAL_BLOCK_ID_W-1:0] link_in_block_id;
    logic [NODE_COUNT-1:0][3:0] link_in_last;
    logic [NODE_COUNT-1:0][3:0] link_out_valid, link_out_ready;
    logic [NODE_COUNT-1:0][3:0][DATA_W-1:0] link_out_data;
    logic [NODE_COUNT-1:0][3:0][1:0] link_out_type;
    logic [NODE_COUNT-1:0][3:0][X_W-1:0] link_out_dest_x;
    logic [NODE_COUNT-1:0][3:0][Y_W-1:0] link_out_dest_y;
    logic [NODE_COUNT-1:0][3:0][SOURCE_ID_W-1:0] link_out_source_id;
    logic [NODE_COUNT-1:0][3:0][EPOCH_W-1:0] link_out_epoch;
    logic [NODE_COUNT-1:0][3:0][GLOBAL_BLOCK_ID_W-1:0] link_out_block_id;
    logic [NODE_COUNT-1:0][3:0] link_out_last;

    for (genvar y = 0; y < MESH_Y_COUNT; y++) begin : gen_y
        for (genvar x = 0; x < MESH_X_COUNT; x++) begin : gen_x
            localparam int N = y*MESH_X_COUNT + x;

            // Each direction is driven exactly once. Boundary outputs are
            // always accepted; correct XY routing never selects them.
            if (y > 0) begin
                localparam int NB = (y-1)*MESH_X_COUNT + x;
                assign link_in_valid[N][NORTH] = link_out_valid[NB][SOUTH];
                assign link_in_data[N][NORTH] = link_out_data[NB][SOUTH];
                assign link_in_type[N][NORTH] = link_out_type[NB][SOUTH];
                assign link_in_dest_x[N][NORTH] = link_out_dest_x[NB][SOUTH];
                assign link_in_dest_y[N][NORTH] = link_out_dest_y[NB][SOUTH];
                assign link_in_source_id[N][NORTH] = link_out_source_id[NB][SOUTH];
                assign link_in_epoch[N][NORTH] = link_out_epoch[NB][SOUTH];
                assign link_in_block_id[N][NORTH] = link_out_block_id[NB][SOUTH];
                assign link_in_last[N][NORTH] = link_out_last[NB][SOUTH];
                assign link_out_ready[N][NORTH] = link_in_ready[NB][SOUTH];
            end else begin
                assign link_in_valid[N][NORTH] = 1'b0;
                assign link_in_data[N][NORTH] = '0; assign link_in_type[N][NORTH] = '0;
                assign link_in_dest_x[N][NORTH] = '0; assign link_in_dest_y[N][NORTH] = '0;
                assign link_in_source_id[N][NORTH] = '0; assign link_in_epoch[N][NORTH] = '0;
                assign link_in_block_id[N][NORTH] = '0; assign link_in_last[N][NORTH] = 1'b1;
                assign link_out_ready[N][NORTH] = 1'b1;
            end

            if (y+1 < MESH_Y_COUNT) begin
                localparam int NB = (y+1)*MESH_X_COUNT + x;
                assign link_in_valid[N][SOUTH] = link_out_valid[NB][NORTH];
                assign link_in_data[N][SOUTH] = link_out_data[NB][NORTH];
                assign link_in_type[N][SOUTH] = link_out_type[NB][NORTH];
                assign link_in_dest_x[N][SOUTH] = link_out_dest_x[NB][NORTH];
                assign link_in_dest_y[N][SOUTH] = link_out_dest_y[NB][NORTH];
                assign link_in_source_id[N][SOUTH] = link_out_source_id[NB][NORTH];
                assign link_in_epoch[N][SOUTH] = link_out_epoch[NB][NORTH];
                assign link_in_block_id[N][SOUTH] = link_out_block_id[NB][NORTH];
                assign link_in_last[N][SOUTH] = link_out_last[NB][NORTH];
                assign link_out_ready[N][SOUTH] = link_in_ready[NB][NORTH];
            end else begin
                assign link_in_valid[N][SOUTH] = 1'b0;
                assign link_in_data[N][SOUTH] = '0; assign link_in_type[N][SOUTH] = '0;
                assign link_in_dest_x[N][SOUTH] = '0; assign link_in_dest_y[N][SOUTH] = '0;
                assign link_in_source_id[N][SOUTH] = '0; assign link_in_epoch[N][SOUTH] = '0;
                assign link_in_block_id[N][SOUTH] = '0; assign link_in_last[N][SOUTH] = 1'b1;
                assign link_out_ready[N][SOUTH] = 1'b1;
            end

            if (x+1 < MESH_X_COUNT) begin
                localparam int NB = y*MESH_X_COUNT + x+1;
                assign link_in_valid[N][EAST] = link_out_valid[NB][WEST];
                assign link_in_data[N][EAST] = link_out_data[NB][WEST];
                assign link_in_type[N][EAST] = link_out_type[NB][WEST];
                assign link_in_dest_x[N][EAST] = link_out_dest_x[NB][WEST];
                assign link_in_dest_y[N][EAST] = link_out_dest_y[NB][WEST];
                assign link_in_source_id[N][EAST] = link_out_source_id[NB][WEST];
                assign link_in_epoch[N][EAST] = link_out_epoch[NB][WEST];
                assign link_in_block_id[N][EAST] = link_out_block_id[NB][WEST];
                assign link_in_last[N][EAST] = link_out_last[NB][WEST];
                assign link_out_ready[N][EAST] = link_in_ready[NB][WEST];
            end else begin
                assign link_in_valid[N][EAST] = 1'b0;
                assign link_in_data[N][EAST] = '0; assign link_in_type[N][EAST] = '0;
                assign link_in_dest_x[N][EAST] = '0; assign link_in_dest_y[N][EAST] = '0;
                assign link_in_source_id[N][EAST] = '0; assign link_in_epoch[N][EAST] = '0;
                assign link_in_block_id[N][EAST] = '0; assign link_in_last[N][EAST] = 1'b1;
                assign link_out_ready[N][EAST] = 1'b1;
            end

            if (x > 0) begin
                localparam int NB = y*MESH_X_COUNT + x-1;
                assign link_in_valid[N][WEST] = link_out_valid[NB][EAST];
                assign link_in_data[N][WEST] = link_out_data[NB][EAST];
                assign link_in_type[N][WEST] = link_out_type[NB][EAST];
                assign link_in_dest_x[N][WEST] = link_out_dest_x[NB][EAST];
                assign link_in_dest_y[N][WEST] = link_out_dest_y[NB][EAST];
                assign link_in_source_id[N][WEST] = link_out_source_id[NB][EAST];
                assign link_in_epoch[N][WEST] = link_out_epoch[NB][EAST];
                assign link_in_block_id[N][WEST] = link_out_block_id[NB][EAST];
                assign link_in_last[N][WEST] = link_out_last[NB][EAST];
                assign link_out_ready[N][WEST] = link_in_ready[NB][EAST];
            end else begin
                assign link_in_valid[N][WEST] = 1'b0;
                assign link_in_data[N][WEST] = '0; assign link_in_type[N][WEST] = '0;
                assign link_in_dest_x[N][WEST] = '0; assign link_in_dest_y[N][WEST] = '0;
                assign link_in_source_id[N][WEST] = '0; assign link_in_epoch[N][WEST] = '0;
                assign link_in_block_id[N][WEST] = '0; assign link_in_last[N][WEST] = 1'b1;
                assign link_out_ready[N][WEST] = 1'b1;
            end

            mesh_h1_tile #(
                .H0_COUNT(H0_COUNT), .CORES_PER_H0(CORES_PER_H0),
                .H0_MVM_COUNT(H0_MVM_COUNT), .H1_MVM_COUNT(H1_MVM_COUNT),
                .CROSS_MVM_COUNT(CROSS_MVM_COUNT),
                .TOP_STATE_ENTRY_COUNT(TOP_STATE_ENTRY_COUNT),
                .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W), .X_W(X_W), .Y_W(Y_W),
                .SOURCE_ID_W(SOURCE_ID_W), .EPOCH_W(EPOCH_W),
                .MESH_X_COUNT(MESH_X_COUNT), .FIFO_DEPTH(FIFO_DEPTH),
                .NODE_ID(N), .NODE_X(x), .NODE_Y(y),
                .BASE_BLOCK_ID(N*BLOCKS_PER_H1)
            ) tile (
                .clk, .rst, .init_start_i(init_start_i[N]), .init_done_o(init_done_o[N]),
                .core_weight_valid_i(core_weight_valid_i[N]),
                .core_weight_ready_o(core_weight_ready_o[N]),
                .core_weight_data_i(core_weight_data_i[N]), .init_state_i(init_state_i[N]),
                .noise_seed_i(noise_seed_i[N]), .coeff_a_i(coeff_a_i[N]),
                .coeff_b_i(coeff_b_i[N]), .coeff_c_i(coeff_c_i[N]),
                .noise_amplitude_i(noise_amplitude_i[N]), .iter_start_i(iter_start_i[N]),
                .iter_done_o(iter_done_o[N]), .commit_i(commit_i[N]), .done_i(done_i[N]),
                .noise_decay_i(noise_decay_i[N]), .epoch_i(epoch_i[N]),
                .h0_schedule_done_i(h0_schedule_done_i[N]),
                .h0_dma_cmd_valid_i(h0_dma_cmd_valid_i[N]),
                .h0_dma_cmd_ready_o(h0_dma_cmd_ready_o[N]),
                .h0_dma_state_a_index_i(h0_dma_state_a_index_i[N]),
                .h0_dma_state_b_index_i(h0_dma_state_b_index_i[N]),
                .h0_dma_block_a_id_i(h0_dma_block_a_id_i[N]),
                .h0_dma_block_b_id_i(h0_dma_block_b_id_i[N]),
                .h0_dma_weight_valid_i(h0_dma_weight_valid_i[N]),
                .h0_dma_weight_ready_o(h0_dma_weight_ready_o[N]),
                .h0_dma_weight_data_i(h0_dma_weight_data_i[N]),
                .h1_schedule_done_i(h1_schedule_done_i[N]),
                .h1_dma_cmd_valid_i(h1_dma_cmd_valid_i[N]),
                .h1_dma_cmd_ready_o(h1_dma_cmd_ready_o[N]),
                .h1_dma_state_a_index_i(h1_dma_state_a_index_i[N]),
                .h1_dma_state_b_index_i(h1_dma_state_b_index_i[N]),
                .h1_dma_block_a_id_i(h1_dma_block_a_id_i[N]),
                .h1_dma_block_b_id_i(h1_dma_block_b_id_i[N]),
                .h1_dma_weight_valid_i(h1_dma_weight_valid_i[N]),
                .h1_dma_weight_ready_o(h1_dma_weight_ready_o[N]),
                .h1_dma_weight_data_i(h1_dma_weight_data_i[N]),
                .state_publish_valid_i(state_publish_valid_i[N]),
                .state_publish_ready_o(state_publish_ready_o[N]),
                .state_publish_local_index_i(state_publish_local_index_i[N]),
                .state_publish_block_id_i(state_publish_block_id_i[N]),
                .state_publish_dest_x_i(state_publish_dest_x_i[N]),
                .state_publish_dest_y_i(state_publish_dest_y_i[N]),
                .done_publish_valid_i(done_publish_valid_i[N]),
                .done_publish_ready_o(done_publish_ready_o[N]),
                .done_publish_dest_x_i(done_publish_dest_x_i[N]),
                .done_publish_dest_y_i(done_publish_dest_y_i[N]),
                .cross_iter_start_i(cross_iter_start_i[N]),
                .cross_iter_done_o(cross_iter_done_o[N]),
                .cross_schedule_done_i(cross_schedule_done_i[N]),
                .cross_dma_cmd_valid_i(cross_dma_cmd_valid_i[N]),
                .cross_dma_cmd_ready_o(cross_dma_cmd_ready_o[N]),
                .cross_dma_state_a_index_i(cross_dma_state_a_index_i[N]),
                .cross_dma_state_b_index_i(cross_dma_state_b_index_i[N]),
                .cross_dma_block_a_id_i(cross_dma_block_a_id_i[N]),
                .cross_dma_block_b_id_i(cross_dma_block_b_id_i[N]),
                .cross_dma_weight_valid_i(cross_dma_weight_valid_i[N]),
                .cross_dma_weight_ready_o(cross_dma_weight_ready_o[N]),
                .cross_dma_weight_data_i(cross_dma_weight_data_i[N]),
                .link_in_valid_i(link_in_valid[N]), .link_in_ready_o(link_in_ready[N]),
                .link_in_data_i(link_in_data[N]), .link_in_type_i(link_in_type[N]),
                .link_in_dest_x_i(link_in_dest_x[N]), .link_in_dest_y_i(link_in_dest_y[N]),
                .link_in_source_id_i(link_in_source_id[N]), .link_in_epoch_i(link_in_epoch[N]),
                .link_in_block_id_i(link_in_block_id[N]), .link_in_last_i(link_in_last[N]),
                .link_out_valid_o(link_out_valid[N]), .link_out_ready_i(link_out_ready[N]),
                .link_out_data_o(link_out_data[N]), .link_out_type_o(link_out_type[N]),
                .link_out_dest_x_o(link_out_dest_x[N]), .link_out_dest_y_o(link_out_dest_y[N]),
                .link_out_source_id_o(link_out_source_id[N]), .link_out_epoch_o(link_out_epoch[N]),
                .link_out_block_id_o(link_out_block_id[N]), .link_out_last_o(link_out_last[N]),
                .state_current_o(state_current_o[N]), .state_next_o(state_next_o[N])
            );
        end
    end
endmodule
