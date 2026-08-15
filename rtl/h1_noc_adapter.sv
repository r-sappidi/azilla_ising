import ising_pkg::*;

// Protocol adapter between one H1 tile and the local endpoint of top_node.
//
// Scheduling remains external. For state publication, the testbench (and
// eventually a schedule player) selects the state block and its destination.
// The adapter only converts that request into a single-flit NOC_STATE packet.
// In the receive direction, NOC_PARTIAL flits are passed to the H1 tile's
// parent-partial input, and a matching-epoch NOC_EPOCH_DONE packet becomes a
// one-cycle parent_partials_done pulse.
module h1_noc_adapter #(
    parameter int GLOBAL_BLOCK_ID_W = 16,
    parameter int X_W               = 3,
    parameter int Y_W               = 3,
    parameter int SOURCE_ID_W       = 8,
    parameter int EPOCH_W           = 16,
    parameter int SOURCE_ID         = 0
) (
    input logic clk,
    input logic rst,

    input logic [EPOCH_W-1:0] current_epoch_i,

    // Testbench/schedule-player controlled state publication request.
    input  logic                         state_publish_valid_i,
    output logic                         state_publish_ready_o,
    input  logic [SPIN_COUNT-1:0]        state_publish_data_i,
    input  logic [GLOBAL_BLOCK_ID_W-1:0] state_publish_block_id_i,
    input  logic [X_W-1:0]               state_publish_dest_x_i,
    input  logic [Y_W-1:0]               state_publish_dest_y_i,

    // Testbench/global-controller generated completion packet. This remains
    // separate from state publication so no schedule hardware is implied.
    input  logic                         done_publish_valid_i,
    output logic                         done_publish_ready_o,
    input  logic [X_W-1:0]               done_publish_dest_x_i,
    input  logic [Y_W-1:0]               done_publish_dest_y_i,

    // Packet stream injected into top_node.
    output logic                         noc_tx_valid_o,
    input  logic                         noc_tx_ready_i,
    output logic [DATA_W-1:0]            noc_tx_data_o,
    output logic [1:0]                   noc_tx_type_o,
    output logic [X_W-1:0]               noc_tx_dest_x_o,
    output logic [Y_W-1:0]               noc_tx_dest_y_o,
    output logic [SOURCE_ID_W-1:0]       noc_tx_source_id_o,
    output logic [EPOCH_W-1:0]           noc_tx_epoch_o,
    output logic [GLOBAL_BLOCK_ID_W-1:0] noc_tx_block_id_o,
    output logic                         noc_tx_last_o,

    // Packet stream ejected from top_node.
    input  logic                         noc_rx_valid_i,
    output logic                         noc_rx_ready_o,
    input  logic [DATA_W-1:0]            noc_rx_data_i,
    input  logic [1:0]                   noc_rx_type_i,
    input  logic [SOURCE_ID_W-1:0]       noc_rx_source_id_i,
    input  logic [EPOCH_W-1:0]           noc_rx_epoch_i,
    input  logic [GLOBAL_BLOCK_ID_W-1:0] noc_rx_block_id_i,
    input  logic                         noc_rx_last_i,

    // Parent partial interface presented to h1_tile.
    output logic                         parent_partial_valid_o,
    input  logic                         parent_partial_ready_i,
    output logic signed [DATA_W-1:0]     parent_partial_data_o,
    output logic [GLOBAL_BLOCK_ID_W-1:0] parent_partial_block_id_o,
    output logic                         parent_partials_done_o
);
    logic receive_current_epoch;
    logic receive_partial;
    logic receive_epoch_done;

    logic select_done;

    // A published state occupies the low SPIN_COUNT bits of a single flit.
    // Completion has priority because it is only issued after state traffic
    // for the epoch has finished. Both packet kinds contain one flit.
    // All packet fields remain stable automatically while noc_tx_ready_i is
    // low because they are driven directly from the producer's ready/valid
    // interface.
    assign select_done = done_publish_valid_i;
    assign state_publish_ready_o = !select_done && noc_tx_ready_i;
    assign done_publish_ready_o = select_done && noc_tx_ready_i;

    always_comb begin
        noc_tx_valid_o = select_done ? done_publish_valid_i :
                                       state_publish_valid_i;
        noc_tx_data_o = '0;
        if (!select_done)
            noc_tx_data_o[SPIN_COUNT-1:0] = state_publish_data_i;
        noc_tx_type_o = select_done ? NOC_EPOCH_DONE : NOC_STATE;
        noc_tx_dest_x_o = select_done ? done_publish_dest_x_i :
                                        state_publish_dest_x_i;
        noc_tx_dest_y_o = select_done ? done_publish_dest_y_i :
                                        state_publish_dest_y_i;
        noc_tx_source_id_o = SOURCE_ID_W'(SOURCE_ID);
        noc_tx_epoch_o = current_epoch_i;
        noc_tx_block_id_o = select_done ? '0 : state_publish_block_id_i;
        noc_tx_last_o = 1'b1;
    end

    // Only current-epoch partial packets are exposed to the arithmetic tile.
    // Completion and stale/unsupported packets are consumed locally so that
    // they cannot block the router's local ejection port.
    assign receive_current_epoch = noc_rx_epoch_i == current_epoch_i;
    assign receive_partial = noc_rx_type_i == NOC_PARTIAL &&
                             receive_current_epoch;
    assign receive_epoch_done = noc_rx_type_i == NOC_EPOCH_DONE &&
                                receive_current_epoch;
    assign noc_rx_ready_o = receive_partial ? parent_partial_ready_i : 1'b1;

    always_comb begin
        parent_partial_valid_o = noc_rx_valid_i && receive_partial;
        parent_partial_data_o = $signed(noc_rx_data_i);
        parent_partial_block_id_o = noc_rx_block_id_i;
    end

    // The completion indication is deliberately a pulse. h1_tile latches it
    // internally, so it may arrive before the H1-local computation finishes.
    always_ff @(posedge clk) begin
        if (rst)
            parent_partials_done_o <= 1'b0;
        else begin
            parent_partials_done_o <= 1'b0;
            if (noc_rx_valid_i && noc_rx_ready_o && receive_epoch_done &&
                noc_rx_last_i)
                parent_partials_done_o <= 1'b1;
        end
    end

    // These fields are carried for system-level observability and future
    // completion accounting; no source-specific behavior is required here.
    logic unused_rx_metadata;
    assign unused_rx_metadata = ^noc_rx_source_id_i;
endmodule
