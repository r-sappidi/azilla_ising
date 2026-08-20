`timescale 1ns/1ps

import ising_pkg::*;

module azilla_floo_router_tb;
    localparam int X_W = 2;
    localparam int Y_W = 2;
    localparam int SOURCE_ID_W = 8;
    localparam int EPOCH_W = 16;
    localparam int BLOCK_ID_W = 16;

    localparam int LOCAL = 0;
    localparam int NORTH = 1;
    localparam int SOUTH = 2;
    localparam int EAST = 3;
    localparam int WEST = 4;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic [4:0] in_valid;
    logic [4:0] in_ready;
    logic [4:0][DATA_W-1:0] in_data;
    logic [4:0][1:0] in_type;
    logic [4:0][X_W-1:0] in_dest_x;
    logic [4:0][Y_W-1:0] in_dest_y;
    logic [4:0][SOURCE_ID_W-1:0] in_source_id;
    logic [4:0][EPOCH_W-1:0] in_epoch;
    logic [4:0][BLOCK_ID_W-1:0] in_block_id;
    logic [4:0] in_last;
    logic [4:0] out_valid;
    logic [4:0] out_ready;
    logic [4:0][DATA_W-1:0] out_data;
    logic [4:0][1:0] out_type;
    logic [4:0][X_W-1:0] out_dest_x;
    logic [4:0][Y_W-1:0] out_dest_y;
    logic [4:0][SOURCE_ID_W-1:0] out_source_id;
    logic [4:0][EPOCH_W-1:0] out_epoch;
    logic [4:0][BLOCK_ID_W-1:0] out_block_id;
    logic [4:0] out_last;

    always #5 clk = ~clk;

    azilla_floo_router #(
        .X_W(X_W), .Y_W(Y_W), .SOURCE_ID_W(SOURCE_ID_W),
        .EPOCH_W(EPOCH_W), .GLOBAL_BLOCK_ID_W(BLOCK_ID_W),
        .FIFO_DEPTH(4), .ROUTER_X(1), .ROUTER_Y(1)
    ) dut (.*,
        .in_valid_i(in_valid), .in_ready_o(in_ready), .in_data_i(in_data),
        .in_type_i(in_type), .in_dest_x_i(in_dest_x), .in_dest_y_i(in_dest_y),
        .in_source_id_i(in_source_id), .in_epoch_i(in_epoch),
        .in_block_id_i(in_block_id), .in_last_i(in_last),
        .out_valid_o(out_valid), .out_ready_i(out_ready), .out_data_o(out_data),
        .out_type_o(out_type), .out_dest_x_o(out_dest_x),
        .out_dest_y_o(out_dest_y), .out_source_id_o(out_source_id),
        .out_epoch_o(out_epoch), .out_block_id_o(out_block_id),
        .out_last_o(out_last)
    );

    task automatic send_flit(
        input int input_port,
        input logic [X_W-1:0] destination_x,
        input logic [Y_W-1:0] destination_y,
        input logic [DATA_W-1:0] data,
        input logic [1:0] packet_type,
        input logic last
    );
        @(negedge clk);
        in_valid[input_port] = 1'b1;
        in_dest_x[input_port] = destination_x;
        in_dest_y[input_port] = destination_y;
        in_data[input_port] = data;
        in_type[input_port] = packet_type;
        in_source_id[input_port] = 8'h5a;
        in_epoch[input_port] = 16'h1234;
        in_block_id[input_port] = 16'h4321;
        in_last[input_port] = last;
        do @(posedge clk); while (!in_ready[input_port]);
        @(negedge clk);
        in_valid[input_port] = 1'b0;
    endtask

    task automatic expect_flit(
        input int output_port,
        input logic [DATA_W-1:0] expected_data,
        input logic [1:0] expected_type,
        input logic expected_last
    );
        int timeout;
        timeout = 0;
        while (!out_valid[output_port] && timeout < 30) begin
            @(posedge clk);
            timeout++;
        end
        assert (out_valid[output_port]) else $fatal(1, "timed out waiting for port %0d", output_port);
        assert (out_data[output_port] == expected_data) else $fatal(1, "payload mismatch");
        assert (out_type[output_port] == expected_type) else $fatal(1, "type mismatch");
        assert (out_source_id[output_port] == 8'h5a) else $fatal(1, "source mismatch");
        assert (out_epoch[output_port] == 16'h1234) else $fatal(1, "epoch mismatch");
        assert (out_block_id[output_port] == 16'h4321) else $fatal(1, "block mismatch");
        assert (out_last[output_port] == expected_last) else $fatal(1, "last mismatch");
        @(posedge clk);
    endtask

    initial begin
        in_valid = '0;
        in_data = '0;
        in_type = '0;
        in_dest_x = '0;
        in_dest_y = '0;
        in_source_id = '0;
        in_epoch = '0;
        in_block_id = '0;
        in_last = '1;
        out_ready = '1;

        repeat (4) @(posedge clk);
        rst = 1'b0;
        repeat (2) @(posedge clk);

        // X is resolved first: a local packet for (2,1) leaves east.
        fork
            send_flit(LOCAL, 2, 1, DATA_W'(64'h1111), NOC_STATE, 1'b1);
            expect_flit(EAST, DATA_W'(64'h1111), NOC_STATE, 1'b1);
        join

        // A packet arriving from the west for this coordinate is ejected.
        fork
            send_flit(WEST, 1, 1, DATA_W'(64'h2222), NOC_PARTIAL, 1'b1);
            expect_flit(LOCAL, DATA_W'(64'h2222), NOC_PARTIAL, 1'b1);
        join

        // Local loopback is required for co-located state publication.
        fork
            send_flit(LOCAL, 1, 1, DATA_W'(64'h3333), NOC_EPOCH_DONE, 1'b1);
            expect_flit(LOCAL, DATA_W'(64'h3333), NOC_EPOCH_DONE, 1'b1);
        join

        $display("azilla_floo_router_tb PASS");
        $finish;
    end
endmodule
