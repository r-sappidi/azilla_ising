import ising_pkg::*;

// Per-H1 round-robin, packet-locked arbiter over the H0 uplinks whose head
// flit is bound for another H1 (model/cpp_exact/anchored.cpp,
// UplinkInjectionArbiter).  Its output takes the cross node's slot in the
// local injection arbiter of top_node; cross nodes carry no jobs in the
// anchored mode.
module uplink_injection_arbiter #(
    parameter int H0_COUNT          = 2,
    parameter int GLOBAL_BLOCK_ID_W = 16,
    parameter int X_W               = 3,
    parameter int Y_W               = 3,
    parameter int SOURCE_ID_W       = 8,
    parameter int EPOCH_W           = 16
) (
    input logic clk,
    input logic rst,

    input  logic [H0_COUNT-1:0]                         src_valid_i,
    output logic [H0_COUNT-1:0]                         src_ready_o,
    input  logic [H0_COUNT-1:0][DATA_W-1:0]             src_data_i,
    input  logic [H0_COUNT-1:0][1:0]                    src_type_i,
    input  logic [H0_COUNT-1:0][X_W-1:0]                src_dest_x_i,
    input  logic [H0_COUNT-1:0][Y_W-1:0]                src_dest_y_i,
    input  logic [H0_COUNT-1:0][SOURCE_ID_W-1:0]        src_source_id_i,
    input  logic [H0_COUNT-1:0][EPOCH_W-1:0]            src_epoch_i,
    input  logic [H0_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]  src_block_id_i,
    input  logic [H0_COUNT-1:0]                         src_last_i,

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
    localparam int H0_ID_W = (H0_COUNT > 1) ? $clog2(H0_COUNT) : 1;

    logic output_locked;
    logic [H0_ID_W-1:0] locked_h0;
    logic [H0_ID_W-1:0] round_robin_start;
    logic [H0_ID_W-1:0] selected_h0;
    logic selected_valid;

    always_comb begin
        logic found;
        int candidate;
        found = 1'b0;
        selected_h0 = locked_h0;
        selected_valid = 1'b0;
        if (output_locked) begin
            selected_valid = src_valid_i[locked_h0];
        end
        else begin
            for (int offset = 0; offset < H0_COUNT; offset++) begin
                candidate = (int'(round_robin_start) + offset) % H0_COUNT;
                if (!found && src_valid_i[candidate]) begin
                    selected_h0 = H0_ID_W'(candidate);
                    selected_valid = 1'b1;
                    found = 1'b1;
                end
            end
        end

        tx_valid_o = selected_valid;
        tx_data_o = src_data_i[selected_h0];
        tx_type_o = src_type_i[selected_h0];
        tx_dest_x_o = src_dest_x_i[selected_h0];
        tx_dest_y_o = src_dest_y_i[selected_h0];
        tx_source_id_o = src_source_id_i[selected_h0];
        tx_epoch_o = src_epoch_i[selected_h0];
        tx_block_id_o = src_block_id_i[selected_h0];
        tx_last_o = src_last_i[selected_h0];

        src_ready_o = '0;
        if (selected_valid)
            src_ready_o[selected_h0] = tx_ready_i;
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            output_locked <= 1'b0;
            locked_h0 <= '0;
            round_robin_start <= '0;
        end
        else if (tx_valid_o && tx_ready_i) begin
            if (output_locked) begin
                if (tx_last_o) begin
                    output_locked <= 1'b0;
                    round_robin_start <= H0_ID_W'((int'(selected_h0) + 1) % H0_COUNT);
                end
            end
            else if (!tx_last_o) begin
                output_locked <= 1'b1;
                locked_h0 <= selected_h0;
            end
            else begin
                round_robin_start <= H0_ID_W'((int'(selected_h0) + 1) % H0_COUNT);
            end
        end
    end
endmodule
