`timescale 1ns/1ps

import ising_pkg::*;

module cycle_model_router_trace_tb;
    logic clk = 1'b0;
    logic rst = 1'b1;
    logic [4:0] in_valid = '0;
    logic [4:0] in_ready;
    logic [4:0][DATA_W-1:0] in_data = '0;
    logic [4:0][1:0] in_type = '0;
    logic [4:0][1:0] in_dest_x = '0;
    logic [4:0][1:0] in_dest_y = '0;
    logic [4:0][7:0] in_source_id = '0;
    logic [4:0][15:0] in_epoch = '0;
    logic [4:0][15:0] in_block_id = '0;
    logic [4:0] in_last = '0;
    logic [4:0] out_valid;
    logic [4:0] out_ready = '1;
    logic [4:0][DATA_W-1:0] out_data;
    logic [4:0][1:0] out_type;
    logic [4:0][1:0] out_dest_x;
    logic [4:0][1:0] out_dest_y;
    logic [4:0][7:0] out_source_id;
    logic [4:0][15:0] out_epoch;
    logic [4:0][15:0] out_block_id;
    logic [4:0] out_last;
    int local_index = 0;
    int north_index = 0;
    int trace_cycle = 0;
    bit sources_enabled = 1'b0;

    always #5 clk = ~clk;

    azilla_floo_router #(
        .X_W(2), .Y_W(2), .SOURCE_ID_W(8), .EPOCH_W(16),
        .GLOBAL_BLOCK_ID_W(16), .FIFO_DEPTH(4),
        .ROUTER_X(0), .ROUTER_Y(0)
    ) dut (
        .clk, .rst,
        .in_valid_i(in_valid), .in_ready_o(in_ready),
        .in_data_i(in_data), .in_type_i(in_type),
        .in_dest_x_i(in_dest_x), .in_dest_y_i(in_dest_y),
        .in_source_id_i(in_source_id), .in_epoch_i(in_epoch),
        .in_block_id_i(in_block_id), .in_last_i(in_last),
        .out_valid_o(out_valid), .out_ready_i(out_ready),
        .out_data_o(out_data), .out_type_o(out_type),
        .out_dest_x_o(out_dest_x), .out_dest_y_o(out_dest_y),
        .out_source_id_o(out_source_id), .out_epoch_o(out_epoch),
        .out_block_id_o(out_block_id), .out_last_o(out_last)
    );

    task automatic drive_sources;
        in_valid = '0;
        in_data = '0;
        in_type = '0;
        in_dest_x = '0;
        in_dest_y = '0;
        in_source_id = '0;
        in_epoch = '0;
        in_block_id = '0;
        in_last = '0;
        if (sources_enabled && local_index < 5) begin
            in_valid[0] = 1'b1;
            in_data[0] = DATA_W'(10 + local_index);
            in_dest_x[0] = 2'd1;
            in_last[0] = local_index == 3 || local_index == 4;
        end
        if (sources_enabled && north_index < 5) begin
            // WEST is a legal competing input for an EAST output under XY.
            in_valid[4] = 1'b1;
            in_data[4] = DATA_W'(30 + north_index);
            in_dest_x[4] = 2'd1;
            in_last[4] = north_index == 0 || north_index == 4;
        end
    endtask

    task automatic step(input bit east_ready);
        bit accept_local;
        bit accept_north;
        out_ready = '1;
        out_ready[3] = east_ready;
        drive_sources();
        #1;
        accept_local = in_valid[0] && in_ready[0];
        accept_north = in_valid[4] && in_ready[4];
        @(posedge clk);
        @(negedge clk);
        $display("RTR %0d %0d %0d %0d %0d %0d %0d",
                 trace_cycle, in_ready, out_valid, out_valid[3],
                 out_data[3][31:0], out_last[3], east_ready);
        if (accept_local)
            local_index++;
        if (accept_north)
            north_index++;
        trace_cycle++;
    endtask

    initial begin
        step(1'b1);
        step(1'b1);
        rst = 1'b0;
        sources_enabled = 1'b1;
        for (int cycle = 0; cycle < 6; cycle++)
            step(1'b0);
        for (int cycle = 0; cycle < 20; cycle++)
            step(1'b1);
        $finish;
    end
endmodule
