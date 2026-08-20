import ising_pkg::*;

// Cross-H1 compute endpoint.
//
// STATE packets populate a global-block-indexed frozen state table. The
// external schedule/weight streamer then drives a generic hierarchy node for
// the H1 pairs assigned to this endpoint. Completed partials from all MVM
// engines are arbitrated onto one packet stream for injection into the NoC.
//
// A cross-H1 scheduler may place work at any mesh location. Therefore the
// state table is indexed by global block ID, and each result independently
// derives its destination H1 and XY coordinate from that global ID.
module cross_h1_node #(
    parameter int STATE_ENTRY_COUNT  = 32768,
    parameter int MVM_COUNT          = 16,
    parameter int GLOBAL_BLOCK_ID_W  = 16,
    parameter int X_W                = 3,
    parameter int Y_W                = 3,
    parameter int SOURCE_ID_W        = 8,
    parameter int EPOCH_W            = 16,
    parameter int BLOCKS_PER_H1      = 512,
    parameter int MESH_X_COUNT       = 8,
    parameter int NODE_ID            = 0
) (
    input logic clk,
    input logic rst,

    input  logic                 iter_start_i,
    output logic                 iter_done_o,
    input  logic [EPOCH_W-1:0]   epoch_i,

    // State packets ejected by the local router. One state packet is one flit;
    // its low SPIN_COUNT payload bits contain one binary state block.
    input  logic                                        state_valid_i,
    output logic                                        state_ready_o,
    input  logic [DATA_W-1:0]                           state_data_i,
    input  logic [1:0]                                  state_type_i,
    input  logic [EPOCH_W-1:0]                          state_epoch_i,
    input  logic [GLOBAL_BLOCK_ID_W-1:0]                state_block_id_i,

    // Compute-facing side of the schedule player and weight streamer.
    input  logic                                        schedule_done_i,
    input  logic [MVM_COUNT-1:0]                        dma_cmd_valid_i,
    output logic [MVM_COUNT-1:0]                        dma_cmd_ready_o,
    input  logic [MVM_COUNT-1:0]
                 [((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                        dma_state_a_index_i,
    input  logic [MVM_COUNT-1:0]
                 [((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                        dma_state_b_index_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                        dma_block_a_id_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                        dma_block_b_id_i,
    input  logic [MVM_COUNT-1:0]                        dma_weight_valid_i,
    output logic [MVM_COUNT-1:0]                        dma_weight_ready_o,
    input  logic [MVM_COUNT-1:0][DATA_W-1:0]            dma_weight_data_i,

    // Partial packet stream for local-router injection.
    output logic                                        tx_valid_o,
    input  logic                                        tx_ready_i,
    output logic [DATA_W-1:0]                           tx_data_o,
    output logic [1:0]                                  tx_type_o,
    output logic [X_W-1:0]                              tx_dest_x_o,
    output logic [Y_W-1:0]                              tx_dest_y_o,
    output logic [SOURCE_ID_W-1:0]                      tx_source_id_o,
    output logic [EPOCH_W-1:0]                          tx_epoch_o,
    output logic [GLOBAL_BLOCK_ID_W-1:0]                tx_block_id_o,
    output logic                                        tx_last_o
);
    localparam int STATE_INDEX_W =
        (STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1;
    localparam int ENGINE_ID_W = (MVM_COUNT > 1) ? $clog2(MVM_COUNT) : 1;

    logic hierarchy_state_valid;
    logic hierarchy_state_ready;
    logic [STATE_INDEX_W-1:0] hierarchy_state_index;

    logic [MVM_COUNT-1:0] partial_valid;
    logic [MVM_COUNT-1:0] partial_ready;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] partial_data;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] partial_block_id;
    logic [MVM_COUNT-1:0] partial_last;

    logic output_locked;
    logic [ENGINE_ID_W-1:0] locked_engine;
    logic [ENGINE_ID_W-1:0] selected_engine;
    logic selected_valid;
    logic [ENGINE_ID_W-1:0] round_robin_start;
    integer candidate_index;
    integer destination_h1;

    // Invalid or stale STATE packets are consumed and discarded so that they
    // cannot permanently block the router's local ejection port.
    assign state_ready_o = hierarchy_state_ready;
    assign hierarchy_state_valid = state_valid_i &&
        state_type_i == NOC_STATE && state_epoch_i == epoch_i &&
        int'(state_block_id_i) < STATE_ENTRY_COUNT;
    assign hierarchy_state_index = STATE_INDEX_W'(state_block_id_i);

    hierarchy_node #(
        .STATE_ENTRY_COUNT(STATE_ENTRY_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W)
    ) compute (
        .clk,
        .rst,
        .iter_start(iter_start_i),
        .iter_done(iter_done_o),
        .state_valid_i(hierarchy_state_valid),
        .state_ready_o(hierarchy_state_ready),
        .state_index_i(hierarchy_state_index),
        .state_data_i(state_data_i[SPIN_COUNT-1:0]),
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
        .partial_valid_o(partial_valid),
        .partial_ready_i(partial_ready),
        .partial_data_o(partial_data),
        .partial_block_id_o(partial_block_id),
        .partial_last_o(partial_last)
    );

    // Select one engine packet at a time. The selection remains locked through
    // the accepted tail flit, preserving the four-beat partial packet.
    always_comb begin
        logic found_engine;
        found_engine = 1'b0;
        selected_engine = locked_engine;
        selected_valid = 1'b0;
        candidate_index = 0;
        destination_h1 = 0;

        if (output_locked) begin
            selected_valid = partial_valid[locked_engine];
        end
        else begin
            for (int offset = 0; offset < MVM_COUNT; offset++) begin
                candidate_index =
                    (int'(round_robin_start) + offset) % MVM_COUNT;
                if (!found_engine && partial_valid[candidate_index]) begin
                    selected_engine = ENGINE_ID_W'(candidate_index);
                    selected_valid = 1'b1;
                    found_engine = 1'b1;
                end
            end
        end

        tx_valid_o = selected_valid;
        tx_data_o = '0;
        tx_type_o = NOC_PARTIAL;
        tx_dest_x_o = '0;
        tx_dest_y_o = '0;
        tx_source_id_o = SOURCE_ID_W'(NODE_ID);
        tx_epoch_o = epoch_i;
        tx_block_id_o = '0;
        tx_last_o = 1'b0;

        if (selected_valid) begin
            destination_h1 =
                int'(partial_block_id[selected_engine]) / BLOCKS_PER_H1;
            tx_data_o = partial_data[selected_engine];
            tx_dest_x_o = X_W'(destination_h1 % MESH_X_COUNT);
            tx_dest_y_o = Y_W'(destination_h1 / MESH_X_COUNT);
            tx_block_id_o = partial_block_id[selected_engine];
            tx_last_o = partial_last[selected_engine];
        end
    end

    always_comb begin
        partial_ready = '0;
        if (selected_valid)
            partial_ready[selected_engine] = tx_ready_i;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            output_locked <= 1'b0;
            locked_engine <= '0;
            round_robin_start <= '0;
        end
        else if (tx_valid_o && tx_ready_i) begin
            if (output_locked) begin
                if (tx_last_o) begin
                    output_locked <= 1'b0;
                    round_robin_start <= ENGINE_ID_W'(
                        (int'(selected_engine) + 1) % MVM_COUNT);
                end
            end
            else if (!tx_last_o) begin
                output_locked <= 1'b1;
                locked_engine <= selected_engine;
            end
            else begin
                round_robin_start <= ENGINE_ID_W'(
                    (int'(selected_engine) + 1) % MVM_COUNT);
            end
        end
    end
endmodule
