import ising_pkg::*;

// H0 tile for CIR with H0-anchored symmetric ownership.  Replaces
// rtl/h0_tile.sv in the anchored build (same module name, superset of ports).
//
// Differences from the base tile:
//   - The hierarchy node's state table is indexed by global block ID
//     (STATE_ENTRY_COUNT = total blocks) and is filled through an external
//     state write port, one 32-bit block per cycle, instead of the tile's own
//     core-state copy loop.  The controller (testbench) writes every distinct
//     block the tile's schedule references, local and remote, then pulses
//     state_fill_done_i; the node starts on the following cycle.  A tile with
//     nothing to fill may receive state_fill_done_i together with iter_start.
//   - Engine partials addressed to blocks outside this tile are consumed by
//     an h0_uplink instead of the local adapter.  The uplink's sibling stream
//     goes to the H1 child crossbar, its mesh stream to the H1's uplink
//     injection arbiter.
//   - node_done_o / uplink_flushed_o expose the completion condition of the
//     anchored quiet window (every H0 node done and every uplink flushed).
module h0_tile #(
    parameter int CORE_COUNT        = 32,
    parameter int MVM_COUNT         = 16,
    parameter int GLOBAL_BLOCK_ID_W = 16,
    parameter int BASE_BLOCK_ID     = 0,
    parameter int STATE_ENTRY_COUNT = 32768,
    parameter int BLOCKS_PER_H1     = 512,
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

    input  logic                                      init_start,
    output logic                                      init_done,
    input  logic [CORE_COUNT-1:0]                     core_weight_valid_i,
    output logic [CORE_COUNT-1:0]                     core_weight_ready_o,
    input  logic [CORE_COUNT-1:0][DATA_W-1:0]         core_weight_data_i,
    input  logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]     init_state_i,
    input  logic [CORE_COUNT-1:0][31:0]               noise_seed_i,
    input  logic signed [COEFF_W-1:0]                 coeff_a_i,
    input  logic signed [COEFF_W-1:0]                 coeff_b_i,
    input  logic signed [COEFF_W-1:0]                 coeff_c_i,
    input  logic signed [COEFF_W-1:0]                 noise_amplitude_i,

    input  logic                                      iter_start,
    output logic                                      iter_done,
    input  logic                                      commit,
    input  logic                                      done,
    input  logic [16:0]                               noise_decay_i,
    input  logic [EPOCH_W-1:0]                        epoch_i,

    // External state fill (global block index) for the hierarchy node.
    input  logic                                      state_fill_valid_i,
    output logic                                      state_fill_ready_o,
    input  logic [((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                      state_fill_index_i,
    input  logic [SPIN_COUNT-1:0]                     state_fill_data_i,
    input  logic                                      state_fill_done_i,

    input  logic                                      schedule_done_i,
    input  logic [MVM_COUNT-1:0]                     dma_cmd_valid_i,
    output logic [MVM_COUNT-1:0]                     dma_cmd_ready_o,
    input  logic [MVM_COUNT-1:0][((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                       dma_state_a_index_i,
    input  logic [MVM_COUNT-1:0][((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                       dma_state_b_index_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                       dma_block_a_id_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                       dma_block_b_id_i,
    input  logic [MVM_COUNT-1:0]                     dma_weight_valid_i,
    output logic [MVM_COUNT-1:0]                     dma_weight_ready_o,
    input  logic [MVM_COUNT-1:0][DATA_W-1:0]         dma_weight_data_i,

    input  logic                                      ext_partial_valid_i,
    output logic                                      ext_partial_ready_o,
    input  logic [GLOBAL_BLOCK_ID_W-1:0]             ext_partial_block_id_i,
    input  logic signed [DATA_W-1:0]                 ext_partial_data_i,
    input  logic                                      ext_partials_done_i,

    // Uplink: sibling-bound partials (H1 child crossbar source).
    output logic                                      sib_partial_valid_o,
    input  logic                                      sib_partial_ready_i,
    output logic signed [DATA_W-1:0]                 sib_partial_data_o,
    output logic [GLOBAL_BLOCK_ID_W-1:0]             sib_partial_block_id_o,
    output logic                                      sib_partial_last_o,

    // Uplink: mesh-bound partial flits (uplink injection arbiter source).
    output logic                                      mesh_tx_valid_o,
    input  logic                                      mesh_tx_ready_i,
    output logic [DATA_W-1:0]                        mesh_tx_data_o,
    output logic [1:0]                               mesh_tx_type_o,
    output logic [X_W-1:0]                           mesh_tx_dest_x_o,
    output logic [Y_W-1:0]                           mesh_tx_dest_y_o,
    output logic [SOURCE_ID_W-1:0]                   mesh_tx_source_id_o,
    output logic [EPOCH_W-1:0]                       mesh_tx_epoch_o,
    output logic [GLOBAL_BLOCK_ID_W-1:0]             mesh_tx_block_id_o,
    output logic                                      mesh_tx_last_o,

    output logic                                      node_done_o,
    output logic                                      uplink_flushed_o,

    output logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]    state_current_o,
    output logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]    state_next_o
);
    localparam int STATE_INDEX_W =
        (STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1;

    typedef enum logic [2:0] {
        TILE_IDLE,
        TILE_FILL_STATES,
        TILE_START_NODE,
        TILE_RUN,
        TILE_DONE
    } tile_state_t;

    tile_state_t tile_state, tile_state_n;
    logic node_iter_start;
    logic node_iter_done;
    logic node_state_valid;
    logic node_state_ready;
    logic core_iter_start;
    logic ext_partials_done_pending;
    logic partials_done_to_cores;

    logic [CORE_COUNT-1:0] core_init_done;
    logic [CORE_COUNT-1:0] core_iter_done;
    logic [CORE_COUNT-1:0] core_h0_partial_valid;
    logic [CORE_COUNT-1:0] core_h0_partial_ready;
    logic signed [CORE_COUNT-1:0][DATA_W-1:0] core_h0_partial_data;
    logic [CORE_COUNT-1:0] core_ext_partial_valid;
    logic [CORE_COUNT-1:0] core_ext_partial_ready;
    logic signed [CORE_COUNT-1:0][DATA_W-1:0] core_ext_partial_data;

    logic [MVM_COUNT-1:0] node_partial_valid;
    logic [MVM_COUNT-1:0] node_partial_ready;
    logic [MVM_COUNT-1:0] adapter_partial_ready;
    logic [MVM_COUNT-1:0] uplink_partial_ready;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] node_partial_data;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] node_partial_block_id;
    logic [MVM_COUNT-1:0] node_partial_last;

    assign init_done = &core_init_done;
    assign iter_done = &core_iter_done;
    assign core_iter_start = iter_start && tile_state == TILE_IDLE;
    assign node_iter_start = tile_state == TILE_START_NODE;
    assign node_state_valid = tile_state == TILE_FILL_STATES && state_fill_valid_i;
    assign state_fill_ready_o = tile_state == TILE_FILL_STATES && node_state_ready;
    assign partials_done_to_cores = node_iter_done && ext_partials_done_pending;
    assign node_done_o = node_iter_done;
    // Local and remote destinations are disjoint, so the two ready sources
    // never contend for the same engine.
    assign node_partial_ready = adapter_partial_ready | uplink_partial_ready;

    // ------------------------------------------------------------------
    // Resident spin endpoints
    // ------------------------------------------------------------------
    generate
        for (genvar core_index = 0; core_index < CORE_COUNT; core_index++) begin : gen_spin_cores
            spin_core core (
                .clk,
                .rst,
                .init_start,
                .init_done(core_init_done[core_index]),
                .weight_init_valid(core_weight_valid_i[core_index]),
                .weight_init_ready(core_weight_ready_o[core_index]),
                .weight_init_data(core_weight_data_i[core_index]),
                .noise_seed(noise_seed_i[core_index]),
                .coeff_a(coeff_a_i),
                .coeff_b(coeff_b_i),
                .coeff_c(coeff_c_i),
                .noise_amplitude(noise_amplitude_i),
                .init_state(init_state_i[core_index]),
                .iter_start(core_iter_start),
                .partials_done(partials_done_to_cores),
                .commit,
                .noise_decay(noise_decay_i),
                .iter_done(core_iter_done[core_index]),
                .done,
                .h0_partial_valid(core_h0_partial_valid[core_index]),
                .h0_partial_ready(core_h0_partial_ready[core_index]),
                .h0_partial_data(core_h0_partial_data[core_index]),
                .ext_partial_valid(core_ext_partial_valid[core_index]),
                .ext_partial_ready(core_ext_partial_ready[core_index]),
                .ext_partial_data(core_ext_partial_data[core_index]),
                .state_next(state_next_o[core_index]),
                .state_current(state_current_o[core_index])
            );
        end
    endgenerate

    // Symmetric engines for every block anchored at this H0 (global state
    // table, unchanged hierarchy_node).
    hierarchy_node #(
        .STATE_ENTRY_COUNT(STATE_ENTRY_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W)
    ) hierarchy_compute (
        .clk,
        .rst,
        .iter_start(node_iter_start),
        .iter_done(node_iter_done),
        .state_valid_i(node_state_valid),
        .state_ready_o(node_state_ready),
        .state_index_i(state_fill_index_i),
        .state_data_i(state_fill_data_i),
        .schedule_done_i,
        .dma_cmd_valid_i,
        .dma_cmd_ready_o,
        .dma_state_a_index_i,
        .dma_state_b_index_i,
        .dma_block_a_i(dma_block_a_id_i),
        .dma_block_b_i(dma_block_b_id_i),
        .dma_weight_valid_i,
        .dma_weight_ready_o,
        .dma_weight_data_i,
        .partial_valid_o(node_partial_valid),
        .partial_ready_i(node_partial_ready),
        .partial_data_o(node_partial_data),
        .partial_block_id_o(node_partial_block_id),
        .partial_last_o(node_partial_last)
    );

    // Local destinations: unchanged H0 adapter (ignores foreign block IDs).
    h0_adapter #(
        .CORE_COUNT(CORE_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .BASE_BLOCK_ID(BASE_BLOCK_ID)
    ) local_adapter (
        .clk,
        .rst,
        .partial_valid_i(node_partial_valid),
        .partial_ready_o(adapter_partial_ready),
        .partial_data_i(node_partial_data),
        .partial_block_id_i(node_partial_block_id),
        .partial_last_i(node_partial_last),
        .core_partial_valid_o(core_h0_partial_valid),
        .core_partial_ready_i(core_h0_partial_ready),
        .core_partial_data_o(core_h0_partial_data)
    );

    // Remote destinations: the uplink.
    h0_uplink #(
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .TOTAL_BLOCK_COUNT(STATE_ENTRY_COUNT),
        .BLOCKS_PER_H0(CORE_COUNT),
        .BASE_BLOCK_ID(BASE_BLOCK_ID),
        .BLOCKS_PER_H1(BLOCKS_PER_H1),
        .NODE_ID(NODE_ID),
        .MESH_X_COUNT(MESH_X_COUNT),
        .X_W(X_W),
        .Y_W(Y_W),
        .SOURCE_ID_W(SOURCE_ID_W),
        .EPOCH_W(EPOCH_W),
        .FIFO_PACKETS(UPLINK_PACKETS),
        .ACCUMULATE(ACCUMULATE)
    ) uplink (
        .clk,
        .rst,
        .iter_start_i(core_iter_start),
        .node_done_i(node_iter_done),
        .epoch_i,
        .partial_valid_i(node_partial_valid),
        .partial_ready_o(uplink_partial_ready),
        .partial_data_i(node_partial_data),
        .partial_block_id_i(node_partial_block_id),
        .partial_last_i(node_partial_last),
        .sib_valid_o(sib_partial_valid_o),
        .sib_ready_i(sib_partial_ready_i),
        .sib_data_o(sib_partial_data_o),
        .sib_block_id_o(sib_partial_block_id_o),
        .sib_last_o(sib_partial_last_o),
        .mesh_valid_o(mesh_tx_valid_o),
        .mesh_ready_i(mesh_tx_ready_i),
        .mesh_data_o(mesh_tx_data_o),
        .mesh_type_o(mesh_tx_type_o),
        .mesh_dest_x_o(mesh_tx_dest_x_o),
        .mesh_dest_y_o(mesh_tx_dest_y_o),
        .mesh_source_id_o(mesh_tx_source_id_o),
        .mesh_epoch_o(mesh_tx_epoch_o),
        .mesh_block_id_o(mesh_tx_block_id_o),
        .mesh_last_o(mesh_tx_last_o),
        .flushed_o(uplink_flushed_o)
    );

    // External partials (H1 crossbar: H1 engines, sibling uplinks, mesh)
    // are demultiplexed to the addressed core.
    always_comb begin
        core_ext_partial_valid = '0;
        core_ext_partial_data = '0;
        ext_partial_ready_o = 1'b0;

        for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
            if (ext_partial_block_id_i ==
                GLOBAL_BLOCK_ID_W'(BASE_BLOCK_ID + core_index)) begin
                core_ext_partial_valid[core_index] = ext_partial_valid_i;
                core_ext_partial_data[core_index] = ext_partial_data_i;
                ext_partial_ready_o = core_ext_partial_ready[core_index];
            end
        end
    end

    // ------------------------------------------------------------------
    // Iteration lifecycle
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            tile_state <= TILE_IDLE;
            ext_partials_done_pending <= 1'b0;
        end
        else begin
            tile_state <= tile_state_n;
            if (iter_start)
                ext_partials_done_pending <= 1'b0;
            else if (ext_partials_done_i)
                ext_partials_done_pending <= 1'b1;
        end
    end

    always_comb begin
        tile_state_n = tile_state;

        unique case (tile_state)
            TILE_IDLE: begin
                if (iter_start)
                    tile_state_n = state_fill_done_i ? TILE_START_NODE : TILE_FILL_STATES;
            end
            TILE_FILL_STATES: begin
                if (state_fill_done_i)
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
