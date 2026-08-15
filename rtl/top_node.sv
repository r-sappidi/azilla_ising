import ising_pkg::*;

// One top-level mesh location containing a NoC router and a cross-H1 compute
// endpoint. A second local endpoint interface connects the co-located H1 tile:
// it injects STATE/EPOCH_DONE packets and receives PARTIAL/EPOCH_DONE packets.
//
// link index: 0=north, 1=south, 2=east, 3=west.
module top_node #(
    parameter int STATE_ENTRY_COUNT  = 32768,
    parameter int MVM_COUNT          = 16,
    parameter int GLOBAL_BLOCK_ID_W  = 16,
    parameter int X_W                = 3,
    parameter int Y_W                = 3,
    parameter int SOURCE_ID_W        = 8,
    parameter int EPOCH_W            = 16,
    parameter int BLOCKS_PER_H1      = 512,
    parameter int MESH_X_COUNT       = 8,
    parameter int FIFO_DEPTH         = 8,
    parameter int NODE_ID            = 0,
    parameter int NODE_X             = 0,
    parameter int NODE_Y             = 0
) (
    input logic clk,
    input logic rst,

    // Four physical mesh links.
    input  logic [3:0]                         link_in_valid_i,
    output logic [3:0]                         link_in_ready_o,
    input  logic [3:0][DATA_W-1:0]             link_in_data_i,
    input  logic [3:0][1:0]                    link_in_type_i,
    input  logic [3:0][X_W-1:0]                link_in_dest_x_i,
    input  logic [3:0][Y_W-1:0]                link_in_dest_y_i,
    input  logic [3:0][SOURCE_ID_W-1:0]        link_in_source_id_i,
    input  logic [3:0][EPOCH_W-1:0]            link_in_epoch_i,
    input  logic [3:0][GLOBAL_BLOCK_ID_W-1:0]  link_in_block_id_i,
    input  logic [3:0]                         link_in_last_i,

    output logic [3:0]                         link_out_valid_o,
    input  logic [3:0]                         link_out_ready_i,
    output logic [3:0][DATA_W-1:0]             link_out_data_o,
    output logic [3:0][1:0]                    link_out_type_o,
    output logic [3:0][X_W-1:0]                link_out_dest_x_o,
    output logic [3:0][Y_W-1:0]                link_out_dest_y_o,
    output logic [3:0][SOURCE_ID_W-1:0]        link_out_source_id_o,
    output logic [3:0][EPOCH_W-1:0]            link_out_epoch_o,
    output logic [3:0][GLOBAL_BLOCK_ID_W-1:0]  link_out_block_id_o,
    output logic [3:0]                         link_out_last_o,

    // Co-located H1 endpoint injection. The H1-side adapter is responsible for
    // state publication/multicast scheduling and supplies complete NoC flits.
    input  logic                                        h1_tx_valid_i,
    output logic                                        h1_tx_ready_o,
    input  logic [DATA_W-1:0]                           h1_tx_data_i,
    input  logic [1:0]                                  h1_tx_type_i,
    input  logic [X_W-1:0]                              h1_tx_dest_x_i,
    input  logic [Y_W-1:0]                              h1_tx_dest_y_i,
    input  logic [SOURCE_ID_W-1:0]                      h1_tx_source_id_i,
    input  logic [EPOCH_W-1:0]                          h1_tx_epoch_i,
    input  logic [GLOBAL_BLOCK_ID_W-1:0]                h1_tx_block_id_i,
    input  logic                                        h1_tx_last_i,

    // Co-located H1 endpoint ejection.
    output logic                                        h1_rx_valid_o,
    input  logic                                        h1_rx_ready_i,
    output logic [DATA_W-1:0]                           h1_rx_data_o,
    output logic [1:0]                                  h1_rx_type_o,
    output logic [SOURCE_ID_W-1:0]                      h1_rx_source_id_o,
    output logic [EPOCH_W-1:0]                          h1_rx_epoch_o,
    output logic [GLOBAL_BLOCK_ID_W-1:0]                h1_rx_block_id_o,
    output logic                                        h1_rx_last_o,

    // Cross-H1 compute control and memory-streamer interface.
    input  logic                                        cross_iter_start_i,
    output logic                                        cross_iter_done_o,
    input  logic [EPOCH_W-1:0]                          epoch_i,
    input  logic                                        cross_schedule_done_i,
    input  logic [MVM_COUNT-1:0]                        cross_dma_cmd_valid_i,
    output logic [MVM_COUNT-1:0]                        cross_dma_cmd_ready_o,
    input  logic [MVM_COUNT-1:0]
                 [((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                        cross_dma_state_a_index_i,
    input  logic [MVM_COUNT-1:0]
                 [((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                        cross_dma_state_b_index_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                        cross_dma_block_a_id_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                        cross_dma_block_b_id_i,
    input  logic [MVM_COUNT-1:0]                        cross_dma_weight_valid_i,
    output logic [MVM_COUNT-1:0]                        cross_dma_weight_ready_o,
    input  logic [MVM_COUNT-1:0][DATA_W-1:0]            cross_dma_weight_data_i
);
    localparam int LOCAL = 0;

    logic [4:0] router_in_valid;
    logic [4:0] router_in_ready;
    logic [4:0][DATA_W-1:0] router_in_data;
    logic [4:0][1:0] router_in_type;
    logic [4:0][X_W-1:0] router_in_dest_x;
    logic [4:0][Y_W-1:0] router_in_dest_y;
    logic [4:0][SOURCE_ID_W-1:0] router_in_source_id;
    logic [4:0][EPOCH_W-1:0] router_in_epoch;
    logic [4:0][GLOBAL_BLOCK_ID_W-1:0] router_in_block_id;
    logic [4:0] router_in_last;

    logic [4:0] router_out_valid;
    logic [4:0] router_out_ready;
    logic [4:0][DATA_W-1:0] router_out_data;
    logic [4:0][1:0] router_out_type;
    logic [4:0][X_W-1:0] router_out_dest_x;
    logic [4:0][Y_W-1:0] router_out_dest_y;
    logic [4:0][SOURCE_ID_W-1:0] router_out_source_id;
    logic [4:0][EPOCH_W-1:0] router_out_epoch;
    logic [4:0][GLOBAL_BLOCK_ID_W-1:0] router_out_block_id;
    logic [4:0] router_out_last;

    logic cross_state_valid;
    logic cross_state_ready;
    logic cross_tx_valid;
    logic cross_tx_ready;
    logic [DATA_W-1:0] cross_tx_data;
    logic [1:0] cross_tx_type;
    logic [X_W-1:0] cross_tx_dest_x;
    logic [Y_W-1:0] cross_tx_dest_y;
    logic [SOURCE_ID_W-1:0] cross_tx_source_id;
    logic [EPOCH_W-1:0] cross_tx_epoch;
    logic [GLOBAL_BLOCK_ID_W-1:0] cross_tx_block_id;
    logic cross_tx_last;

    logic injection_locked;
    logic injection_cross_selected;
    logic select_cross;

    // Map packed external link indices onto router cardinal ports 1 through 4.
    always_comb begin
        router_in_valid = '0;
        router_in_data = '0;
        router_in_type = '0;
        router_in_dest_x = '0;
        router_in_dest_y = '0;
        router_in_source_id = '0;
        router_in_epoch = '0;
        router_in_block_id = '0;
        router_in_last = '0;
        link_in_ready_o = '0;

        router_out_ready = '0;
        link_out_valid_o = '0;
        link_out_data_o = '0;
        link_out_type_o = '0;
        link_out_dest_x_o = '0;
        link_out_dest_y_o = '0;
        link_out_source_id_o = '0;
        link_out_epoch_o = '0;
        link_out_block_id_o = '0;
        link_out_last_o = '0;

        for (int link_index = 0; link_index < 4; link_index++) begin
            router_in_valid[link_index+1] = link_in_valid_i[link_index];
            router_in_data[link_index+1] = link_in_data_i[link_index];
            router_in_type[link_index+1] = link_in_type_i[link_index];
            router_in_dest_x[link_index+1] = link_in_dest_x_i[link_index];
            router_in_dest_y[link_index+1] = link_in_dest_y_i[link_index];
            router_in_source_id[link_index+1] = link_in_source_id_i[link_index];
            router_in_epoch[link_index+1] = link_in_epoch_i[link_index];
            router_in_block_id[link_index+1] = link_in_block_id_i[link_index];
            router_in_last[link_index+1] = link_in_last_i[link_index];
            link_in_ready_o[link_index] = router_in_ready[link_index+1];

            link_out_valid_o[link_index] = router_out_valid[link_index+1];
            link_out_data_o[link_index] = router_out_data[link_index+1];
            link_out_type_o[link_index] = router_out_type[link_index+1];
            link_out_dest_x_o[link_index] = router_out_dest_x[link_index+1];
            link_out_dest_y_o[link_index] = router_out_dest_y[link_index+1];
            link_out_source_id_o[link_index] = router_out_source_id[link_index+1];
            link_out_epoch_o[link_index] = router_out_epoch[link_index+1];
            link_out_block_id_o[link_index] = router_out_block_id[link_index+1];
            link_out_last_o[link_index] = router_out_last[link_index+1];
            router_out_ready[link_index+1] = link_out_ready_i[link_index];
        end

        // Local injection arbitration. Cross-H1 partials have priority when
        // unlocked; either source retains ownership through its tail.
        select_cross = injection_locked ? injection_cross_selected : cross_tx_valid;
        cross_tx_ready = 1'b0;
        h1_tx_ready_o = 1'b0;

        router_in_valid[LOCAL] = select_cross ? cross_tx_valid : h1_tx_valid_i;
        router_in_data[LOCAL] = select_cross ? cross_tx_data : h1_tx_data_i;
        router_in_type[LOCAL] = select_cross ? cross_tx_type : h1_tx_type_i;
        router_in_dest_x[LOCAL] = select_cross ? cross_tx_dest_x : h1_tx_dest_x_i;
        router_in_dest_y[LOCAL] = select_cross ? cross_tx_dest_y : h1_tx_dest_y_i;
        router_in_source_id[LOCAL] =
            select_cross ? cross_tx_source_id : h1_tx_source_id_i;
        router_in_epoch[LOCAL] = select_cross ? cross_tx_epoch : h1_tx_epoch_i;
        router_in_block_id[LOCAL] =
            select_cross ? cross_tx_block_id : h1_tx_block_id_i;
        router_in_last[LOCAL] = select_cross ? cross_tx_last : h1_tx_last_i;

        if (select_cross)
            cross_tx_ready = router_in_ready[LOCAL];
        else
            h1_tx_ready_o = router_in_ready[LOCAL];

        // STATE packets belong to the cross-H1 state table. Other local
        // packets are passed to the co-located H1 endpoint.
        cross_state_valid = router_out_valid[LOCAL] &&
            router_out_type[LOCAL] == NOC_STATE;
        h1_rx_valid_o = router_out_valid[LOCAL] &&
            router_out_type[LOCAL] != NOC_STATE;
        router_out_ready[LOCAL] =
            router_out_type[LOCAL] == NOC_STATE ?
                cross_state_ready : h1_rx_ready_i;

        h1_rx_data_o = router_out_data[LOCAL];
        h1_rx_type_o = router_out_type[LOCAL];
        h1_rx_source_id_o = router_out_source_id[LOCAL];
        h1_rx_epoch_o = router_out_epoch[LOCAL];
        h1_rx_block_id_o = router_out_block_id[LOCAL];
        h1_rx_last_o = router_out_last[LOCAL];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            injection_locked <= 1'b0;
            injection_cross_selected <= 1'b0;
        end
        else if (router_in_valid[LOCAL] && router_in_ready[LOCAL]) begin
            if (!injection_locked && !router_in_last[LOCAL]) begin
                injection_locked <= 1'b1;
                injection_cross_selected <= select_cross;
            end
            else if (injection_locked && router_in_last[LOCAL]) begin
                injection_locked <= 1'b0;
            end
        end
    end

    noc_router #(
        .X_W(X_W),
        .Y_W(Y_W),
        .SOURCE_ID_W(SOURCE_ID_W),
        .EPOCH_W(EPOCH_W),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .FIFO_DEPTH(FIFO_DEPTH),
        .ROUTER_X(NODE_X),
        .ROUTER_Y(NODE_Y)
    ) router (
        .clk,
        .rst,
        .in_valid_i(router_in_valid),
        .in_ready_o(router_in_ready),
        .in_data_i(router_in_data),
        .in_type_i(router_in_type),
        .in_dest_x_i(router_in_dest_x),
        .in_dest_y_i(router_in_dest_y),
        .in_source_id_i(router_in_source_id),
        .in_epoch_i(router_in_epoch),
        .in_block_id_i(router_in_block_id),
        .in_last_i(router_in_last),
        .out_valid_o(router_out_valid),
        .out_ready_i(router_out_ready),
        .out_data_o(router_out_data),
        .out_type_o(router_out_type),
        .out_dest_x_o(router_out_dest_x),
        .out_dest_y_o(router_out_dest_y),
        .out_source_id_o(router_out_source_id),
        .out_epoch_o(router_out_epoch),
        .out_block_id_o(router_out_block_id),
        .out_last_o(router_out_last)
    );

    cross_h1_node #(
        .STATE_ENTRY_COUNT(STATE_ENTRY_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .X_W(X_W),
        .Y_W(Y_W),
        .SOURCE_ID_W(SOURCE_ID_W),
        .EPOCH_W(EPOCH_W),
        .BLOCKS_PER_H1(BLOCKS_PER_H1),
        .MESH_X_COUNT(MESH_X_COUNT),
        .NODE_ID(NODE_ID)
    ) cross_compute (
        .clk,
        .rst,
        .iter_start_i(cross_iter_start_i),
        .iter_done_o(cross_iter_done_o),
        .epoch_i,
        .state_valid_i(cross_state_valid),
        .state_ready_o(cross_state_ready),
        .state_data_i(router_out_data[LOCAL]),
        .state_type_i(router_out_type[LOCAL]),
        .state_epoch_i(router_out_epoch[LOCAL]),
        .state_block_id_i(router_out_block_id[LOCAL]),
        .schedule_done_i(cross_schedule_done_i),
        .dma_cmd_valid_i(cross_dma_cmd_valid_i),
        .dma_cmd_ready_o(cross_dma_cmd_ready_o),
        .dma_state_a_index_i(cross_dma_state_a_index_i),
        .dma_state_b_index_i(cross_dma_state_b_index_i),
        .dma_block_a_id_i(cross_dma_block_a_id_i),
        .dma_block_b_id_i(cross_dma_block_b_id_i),
        .dma_weight_valid_i(cross_dma_weight_valid_i),
        .dma_weight_ready_o(cross_dma_weight_ready_o),
        .dma_weight_data_i(cross_dma_weight_data_i),
        .tx_valid_o(cross_tx_valid),
        .tx_ready_i(cross_tx_ready),
        .tx_data_o(cross_tx_data),
        .tx_type_o(cross_tx_type),
        .tx_dest_x_o(cross_tx_dest_x),
        .tx_dest_y_o(cross_tx_dest_y),
        .tx_source_id_o(cross_tx_source_id),
        .tx_epoch_o(cross_tx_epoch),
        .tx_block_id_o(cross_tx_block_id),
        .tx_last_o(cross_tx_last)
    );
endmodule
