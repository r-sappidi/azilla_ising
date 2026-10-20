import ising_pkg::*;

// H1 tile for CIR with H0-anchored symmetric ownership.  Replaces
// rtl/h1_tile.sv in the anchored build (same module name, superset of ports).
//
// Child H0 tiles own every block that is not inside one H0; the H1 hierarchy
// node is kept (state snapshot and start sequence unchanged) but carries no
// jobs in this mode.  The child crossbar switches H1_MVM_COUNT engine
// sources followed by H0_COUNT uplink sibling streams, with the parent
// (mesh) port first (PARENT_FIRST).  Mesh-bound uplink flits are exposed per
// H0 for the mesh-level uplink injection arbiter.  Per-H0 state fill ports
// and completion flags are passed through.
module h1_tile #(
    parameter int H0_COUNT          = 16,
    parameter int CORES_PER_H0      = 32,
    parameter int H0_MVM_COUNT      = 16,
    parameter int H1_MVM_COUNT      = 16,
    parameter int GLOBAL_BLOCK_ID_W = 16,
    parameter int BASE_BLOCK_ID     = 0,
    parameter int H0_STATE_ENTRY_COUNT = 32768,
    parameter int NODE_ID           = 0,
    parameter int MESH_X_COUNT      = 8,
    parameter int X_W               = 3,
    parameter int Y_W               = 3,
    parameter int SOURCE_ID_W       = 8,
    parameter int EPOCH_W           = 16,
    parameter int UPLINK_PACKETS    = 4,
    parameter bit ACCUMULATE        = 1'b1
) (
    input logic clk,
    input logic rst,

    input  logic init_start,
    output logic init_done,
    input  logic [H0_COUNT-1:0][CORES_PER_H0-1:0]                 core_weight_valid_i,
    output logic [H0_COUNT-1:0][CORES_PER_H0-1:0]                 core_weight_ready_o,
    input  logic [H0_COUNT-1:0][CORES_PER_H0-1:0][DATA_W-1:0]     core_weight_data_i,
    input  logic [H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0] init_state_i,
    input  logic [H0_COUNT-1:0][CORES_PER_H0-1:0][31:0]           noise_seed_i,
    input  logic signed [COEFF_W-1:0]                             coeff_a_i,
    input  logic signed [COEFF_W-1:0]                             coeff_b_i,
    input  logic signed [COEFF_W-1:0]                             coeff_c_i,
    input  logic signed [COEFF_W-1:0]                             noise_amplitude_i,

    input  logic iter_start,
    output logic iter_done,
    input  logic commit,
    input  logic done,
    input  logic [16:0] noise_decay_i,
    input  logic [EPOCH_W-1:0] epoch_i,

    // Per-H0 global state fill.
    input  logic [H0_COUNT-1:0]                                   h0_state_fill_valid_i,
    output logic [H0_COUNT-1:0]                                   h0_state_fill_ready_o,
    input  logic [H0_COUNT-1:0]
                 [((H0_STATE_ENTRY_COUNT > 1) ? $clog2(H0_STATE_ENTRY_COUNT) : 1)-1:0]
                                                                   h0_state_fill_index_i,
    input  logic [H0_COUNT-1:0][SPIN_COUNT-1:0]                   h0_state_fill_data_i,
    input  logic [H0_COUNT-1:0]                                   h0_state_fill_done_i,

    // Schedules and weight streams for each child H0 hierarchy node.
    input  logic [H0_COUNT-1:0]                                  h0_schedule_done_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]                 h0_dma_cmd_valid_i,
    output logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]                 h0_dma_cmd_ready_o,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                 [((H0_STATE_ENTRY_COUNT > 1) ? $clog2(H0_STATE_ENTRY_COUNT) : 1)-1:0]
                                                                   h0_dma_state_a_index_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]
                 [((H0_STATE_ENTRY_COUNT > 1) ? $clog2(H0_STATE_ENTRY_COUNT) : 1)-1:0]
                                                                   h0_dma_state_b_index_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                                   h0_dma_block_a_id_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                                   h0_dma_block_b_id_i,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]                 h0_dma_weight_valid_i,
    output logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0]                 h0_dma_weight_ready_o,
    input  logic [H0_COUNT-1:0][H0_MVM_COUNT-1:0][DATA_W-1:0]     h0_dma_weight_data_i,

    // Schedule and weight streams for the H1 hierarchy node (idle here).
    input  logic                                                    h1_schedule_done_i,
    input  logic [H1_MVM_COUNT-1:0]                                 h1_dma_cmd_valid_i,
    output logic [H1_MVM_COUNT-1:0]                                 h1_dma_cmd_ready_o,
    input  logic [H1_MVM_COUNT-1:0]
                 [(((H0_COUNT*CORES_PER_H0) > 1) ?
                    $clog2(H0_COUNT*CORES_PER_H0) : 1)-1:0]         h1_dma_state_a_index_i,
    input  logic [H1_MVM_COUNT-1:0]
                 [(((H0_COUNT*CORES_PER_H0) > 1) ?
                    $clog2(H0_COUNT*CORES_PER_H0) : 1)-1:0]         h1_dma_state_b_index_i,
    input  logic [H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]         h1_dma_block_a_id_i,
    input  logic [H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]         h1_dma_block_b_id_i,
    input  logic [H1_MVM_COUNT-1:0]                                h1_dma_weight_valid_i,
    output logic [H1_MVM_COUNT-1:0]                                h1_dma_weight_ready_o,
    input  logic [H1_MVM_COUNT-1:0][DATA_W-1:0]                    h1_dma_weight_data_i,

    // Partial stream from the mesh (parent).
    input  logic                                         parent_partial_valid_i,
    output logic                                         parent_partial_ready_o,
    input  logic [GLOBAL_BLOCK_ID_W-1:0]                 parent_partial_block_id_i,
    input  logic signed [DATA_W-1:0]                     parent_partial_data_i,
    input  logic                                         parent_partials_done_i,

    // Per-H0 mesh-bound uplink flits.
    output logic [H0_COUNT-1:0]                          h0_mesh_tx_valid_o,
    input  logic [H0_COUNT-1:0]                          h0_mesh_tx_ready_i,
    output logic [H0_COUNT-1:0][DATA_W-1:0]              h0_mesh_tx_data_o,
    output logic [H0_COUNT-1:0][1:0]                     h0_mesh_tx_type_o,
    output logic [H0_COUNT-1:0][X_W-1:0]                 h0_mesh_tx_dest_x_o,
    output logic [H0_COUNT-1:0][Y_W-1:0]                 h0_mesh_tx_dest_y_o,
    output logic [H0_COUNT-1:0][SOURCE_ID_W-1:0]         h0_mesh_tx_source_id_o,
    output logic [H0_COUNT-1:0][EPOCH_W-1:0]             h0_mesh_tx_epoch_o,
    output logic [H0_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]   h0_mesh_tx_block_id_o,
    output logic [H0_COUNT-1:0]                          h0_mesh_tx_last_o,

    output logic [H0_COUNT-1:0]                          h0_node_done_o,
    output logic [H0_COUNT-1:0]                          h0_uplink_flushed_o,

    output logic [H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
                                                         state_current_o,
    output logic [H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
                                                         state_next_o
);
    localparam int H1_STATE_COUNT = H0_COUNT * CORES_PER_H0;
    localparam int H1_STATE_INDEX_W =
        (H1_STATE_COUNT > 1) ? $clog2(H1_STATE_COUNT) : 1;
    localparam int H0_ID_W = (H0_COUNT > 1) ? $clog2(H0_COUNT) : 1;
    localparam int CORE_ID_W =
        (CORES_PER_H0 > 1) ? $clog2(CORES_PER_H0) : 1;
    localparam int SOURCE_COUNT = H1_MVM_COUNT + H0_COUNT;

    typedef enum logic [2:0] {
        TILE_IDLE,
        TILE_LOAD_STATES,
        TILE_START_NODE,
        TILE_RUN,
        TILE_DONE
    } tile_state_t;

    tile_state_t tile_state, tile_state_n;
    logic [H1_STATE_INDEX_W-1:0] state_load_index;
    logic [H0_ID_W-1:0] state_load_h0;
    logic [CORE_ID_W-1:0] state_load_core;
    logic h1_node_iter_start;
    logic h1_node_iter_done;
    logic h1_node_state_valid;
    logic h1_node_state_ready;
    logic parent_done_pending;
    logic h1_partials_done;

    logic [H0_COUNT-1:0] h0_init_done;
    logic [H0_COUNT-1:0] h0_iter_done;
    logic [H0_COUNT-1:0] h0_ext_partial_valid;
    logic [H0_COUNT-1:0] h0_ext_partial_ready;
    logic signed [H0_COUNT-1:0][DATA_W-1:0] h0_ext_partial_data;
    logic [H0_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] h0_ext_partial_block_id;

    logic [H1_MVM_COUNT-1:0] node_partial_valid;
    logic [H1_MVM_COUNT-1:0] node_partial_ready;
    logic signed [H1_MVM_COUNT-1:0][DATA_W-1:0] node_partial_data;
    logic [H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] node_partial_block_id;
    logic [H1_MVM_COUNT-1:0] node_partial_last;

    // Crossbar sources: H1 engines, then one sibling stream per H0 uplink.
    logic [SOURCE_COUNT-1:0] source_partial_valid;
    logic [SOURCE_COUNT-1:0] source_partial_ready;
    logic signed [SOURCE_COUNT-1:0][DATA_W-1:0] source_partial_data;
    logic [SOURCE_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] source_partial_block_id;
    logic [SOURCE_COUNT-1:0] source_partial_last;

    assign init_done = &h0_init_done;
    assign iter_done = &h0_iter_done;
    assign h1_node_iter_start = tile_state == TILE_START_NODE;
    assign h1_node_state_valid = tile_state == TILE_LOAD_STATES;
    assign state_load_h0 = H0_ID_W'(int'(state_load_index) / CORES_PER_H0);
    assign state_load_core = CORE_ID_W'(int'(state_load_index) % CORES_PER_H0);
    assign h1_partials_done = h1_node_iter_done && parent_done_pending;

    always_comb begin
        for (int engine = 0; engine < H1_MVM_COUNT; engine++) begin
            source_partial_valid[engine] = node_partial_valid[engine];
            source_partial_data[engine] = node_partial_data[engine];
            source_partial_block_id[engine] = node_partial_block_id[engine];
            source_partial_last[engine] = node_partial_last[engine];
            node_partial_ready[engine] = source_partial_ready[engine];
        end
    end

    // ------------------------------------------------------------------
    // Child H0 tiles
    // ------------------------------------------------------------------
    generate
        for (genvar h0_index = 0; h0_index < H0_COUNT; h0_index++) begin : gen_h0_tiles
            h0_tile #(
                .CORE_COUNT(CORES_PER_H0),
                .MVM_COUNT(H0_MVM_COUNT),
                .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
                .BASE_BLOCK_ID(BASE_BLOCK_ID + h0_index*CORES_PER_H0),
                .STATE_ENTRY_COUNT(H0_STATE_ENTRY_COUNT),
                .BLOCKS_PER_H1(H1_STATE_COUNT),
                .NODE_ID(NODE_ID),
                .MESH_X_COUNT(MESH_X_COUNT),
                .X_W(X_W), .Y_W(Y_W),
                .SOURCE_ID_W(SOURCE_ID_W), .EPOCH_W(EPOCH_W),
                .UPLINK_PACKETS(UPLINK_PACKETS),
                .ACCUMULATE(ACCUMULATE)
            ) child_h0 (
                .clk,
                .rst,
                .init_start,
                .init_done(h0_init_done[h0_index]),
                .core_weight_valid_i(core_weight_valid_i[h0_index]),
                .core_weight_ready_o(core_weight_ready_o[h0_index]),
                .core_weight_data_i(core_weight_data_i[h0_index]),
                .init_state_i(init_state_i[h0_index]),
                .noise_seed_i(noise_seed_i[h0_index]),
                .coeff_a_i,
                .coeff_b_i,
                .coeff_c_i,
                .noise_amplitude_i,
                .iter_start,
                .iter_done(h0_iter_done[h0_index]),
                .commit,
                .done,
                .noise_decay_i,
                .epoch_i,
                .state_fill_valid_i(h0_state_fill_valid_i[h0_index]),
                .state_fill_ready_o(h0_state_fill_ready_o[h0_index]),
                .state_fill_index_i(h0_state_fill_index_i[h0_index]),
                .state_fill_data_i(h0_state_fill_data_i[h0_index]),
                .state_fill_done_i(h0_state_fill_done_i[h0_index]),
                .schedule_done_i(h0_schedule_done_i[h0_index]),
                .dma_cmd_valid_i(h0_dma_cmd_valid_i[h0_index]),
                .dma_cmd_ready_o(h0_dma_cmd_ready_o[h0_index]),
                .dma_state_a_index_i(h0_dma_state_a_index_i[h0_index]),
                .dma_state_b_index_i(h0_dma_state_b_index_i[h0_index]),
                .dma_block_a_id_i(h0_dma_block_a_id_i[h0_index]),
                .dma_block_b_id_i(h0_dma_block_b_id_i[h0_index]),
                .dma_weight_valid_i(h0_dma_weight_valid_i[h0_index]),
                .dma_weight_ready_o(h0_dma_weight_ready_o[h0_index]),
                .dma_weight_data_i(h0_dma_weight_data_i[h0_index]),
                .ext_partial_valid_i(h0_ext_partial_valid[h0_index]),
                .ext_partial_ready_o(h0_ext_partial_ready[h0_index]),
                .ext_partial_block_id_i(h0_ext_partial_block_id[h0_index]),
                .ext_partial_data_i(h0_ext_partial_data[h0_index]),
                .ext_partials_done_i(h1_partials_done),
                .sib_partial_valid_o(source_partial_valid[H1_MVM_COUNT + h0_index]),
                .sib_partial_ready_i(source_partial_ready[H1_MVM_COUNT + h0_index]),
                .sib_partial_data_o(source_partial_data[H1_MVM_COUNT + h0_index]),
                .sib_partial_block_id_o(source_partial_block_id[H1_MVM_COUNT + h0_index]),
                .sib_partial_last_o(source_partial_last[H1_MVM_COUNT + h0_index]),
                .mesh_tx_valid_o(h0_mesh_tx_valid_o[h0_index]),
                .mesh_tx_ready_i(h0_mesh_tx_ready_i[h0_index]),
                .mesh_tx_data_o(h0_mesh_tx_data_o[h0_index]),
                .mesh_tx_type_o(h0_mesh_tx_type_o[h0_index]),
                .mesh_tx_dest_x_o(h0_mesh_tx_dest_x_o[h0_index]),
                .mesh_tx_dest_y_o(h0_mesh_tx_dest_y_o[h0_index]),
                .mesh_tx_source_id_o(h0_mesh_tx_source_id_o[h0_index]),
                .mesh_tx_epoch_o(h0_mesh_tx_epoch_o[h0_index]),
                .mesh_tx_block_id_o(h0_mesh_tx_block_id_o[h0_index]),
                .mesh_tx_last_o(h0_mesh_tx_last_o[h0_index]),
                .node_done_o(h0_node_done_o[h0_index]),
                .uplink_flushed_o(h0_uplink_flushed_o[h0_index]),
                .state_current_o(state_current_o[h0_index]),
                .state_next_o(state_next_o[h0_index])
            );
        end
    endgenerate

    // H1 hierarchy node: present, snapshot sequence unchanged, no jobs.
    hierarchy_node #(
        .STATE_ENTRY_COUNT(H1_STATE_COUNT),
        .MVM_COUNT(H1_MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W)
    ) h1_compute (
        .clk,
        .rst,
        .iter_start(h1_node_iter_start),
        .iter_done(h1_node_iter_done),
        .state_valid_i(h1_node_state_valid),
        .state_ready_o(h1_node_state_ready),
        .state_index_i(state_load_index),
        .state_data_i(state_current_o[state_load_h0][state_load_core]),
        .schedule_done_i(h1_schedule_done_i),
        .dma_cmd_valid_i(h1_dma_cmd_valid_i),
        .dma_cmd_ready_o(h1_dma_cmd_ready_o),
        .dma_state_a_index_i(h1_dma_state_a_index_i),
        .dma_state_b_index_i(h1_dma_state_b_index_i),
        .dma_block_a_i(h1_dma_block_a_id_i),
        .dma_block_b_i(h1_dma_block_b_id_i),
        .dma_weight_valid_i(h1_dma_weight_valid_i),
        .dma_weight_ready_o(h1_dma_weight_ready_o),
        .dma_weight_data_i(h1_dma_weight_data_i),
        .partial_valid_o(node_partial_valid),
        .partial_ready_i(node_partial_ready),
        .partial_data_o(node_partial_data),
        .partial_block_id_o(node_partial_block_id),
        .partial_last_o(node_partial_last)
    );

    // Child crossbar: mesh (parent) first, then H1 engines, then uplinks.
    h1_child_adapter #(
        .CHILD_COUNT(H0_COUNT),
        .BLOCKS_PER_CHILD(CORES_PER_H0),
        .MVM_COUNT(SOURCE_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .BASE_BLOCK_ID(BASE_BLOCK_ID),
        .PARENT_FIRST(1'b1)
    ) child_adapter (
        .clk,
        .rst,
        .node_partial_valid_i(source_partial_valid),
        .node_partial_ready_o(source_partial_ready),
        .node_partial_data_i(source_partial_data),
        .node_partial_block_id_i(source_partial_block_id),
        .node_partial_last_i(source_partial_last),
        .parent_partial_valid_i,
        .parent_partial_ready_o,
        .parent_partial_data_i,
        .parent_partial_block_id_i,
        .child_partial_valid_o(h0_ext_partial_valid),
        .child_partial_ready_i(h0_ext_partial_ready),
        .child_partial_data_o(h0_ext_partial_data),
        .child_partial_block_id_o(h0_ext_partial_block_id)
    );

    // ------------------------------------------------------------------
    // H1 state snapshot and iteration lifecycle (unchanged)
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            tile_state <= TILE_IDLE;
            state_load_index <= '0;
            parent_done_pending <= 1'b0;
        end
        else begin
            tile_state <= tile_state_n;

            if (iter_start) begin
                state_load_index <= '0;
                parent_done_pending <= 1'b0;
            end
            else begin
                if (tile_state == TILE_LOAD_STATES &&
                    h1_node_state_valid && h1_node_state_ready &&
                    state_load_index != H1_STATE_INDEX_W'(H1_STATE_COUNT-1))
                    state_load_index <= state_load_index + 1'b1;

                if (parent_partials_done_i)
                    parent_done_pending <= 1'b1;
            end
        end
    end

    always_comb begin
        tile_state_n = tile_state;

        unique case (tile_state)
            TILE_IDLE: begin
                if (iter_start)
                    tile_state_n = TILE_LOAD_STATES;
            end
            TILE_LOAD_STATES: begin
                if (h1_node_state_valid && h1_node_state_ready &&
                    state_load_index == H1_STATE_INDEX_W'(H1_STATE_COUNT-1))
                    tile_state_n = TILE_START_NODE;
            end
            TILE_START_NODE: tile_state_n = TILE_RUN;
            TILE_RUN: begin
                if (iter_done)
                    tile_state_n = commit ? TILE_IDLE : TILE_DONE;
            end
            TILE_DONE: begin
                if (commit)
                    tile_state_n = TILE_IDLE;
            end
            default: tile_state_n = TILE_IDLE;
        endcase
    end
endmodule
