`timescale 1ns/1ps

import ising_pkg::*;

module noc_router_tb;
    localparam int X_W = 2;
    localparam int Y_W = 2;
    localparam int SOURCE_ID_W = 4;
    localparam int EPOCH_W = 4;
    localparam int GLOBAL_BLOCK_ID_W = 8;

    logic clk = 0;
    logic rst = 1;
    logic [4:0] in_valid, in_ready;
    logic [4:0][DATA_W-1:0] in_data;
    logic [4:0][1:0] in_type;
    logic [4:0][X_W-1:0] in_dest_x;
    logic [4:0][Y_W-1:0] in_dest_y;
    logic [4:0][SOURCE_ID_W-1:0] in_source_id;
    logic [4:0][EPOCH_W-1:0] in_epoch;
    logic [4:0][GLOBAL_BLOCK_ID_W-1:0] in_block_id;
    logic [4:0] in_last;
    logic [4:0] out_valid, out_ready;
    logic [4:0][DATA_W-1:0] out_data;
    logic [4:0][1:0] out_type;
    logic [4:0][X_W-1:0] out_dest_x;
    logic [4:0][Y_W-1:0] out_dest_y;
    logic [4:0][SOURCE_ID_W-1:0] out_source_id;
    logic [4:0][EPOCH_W-1:0] out_epoch;
    logic [4:0][GLOBAL_BLOCK_ID_W-1:0] out_block_id;
    logic [4:0] out_last;
    int east_count;
    int local_count;
    int errors;

    noc_router #(
        .X_W(X_W), .Y_W(Y_W), .SOURCE_ID_W(SOURCE_ID_W),
        .EPOCH_W(EPOCH_W), .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .FIFO_DEPTH(8), .ROUTER_X(1), .ROUTER_Y(1)
    ) dut (.*,
        .in_valid_i(in_valid), .in_ready_o(in_ready), .in_data_i(in_data),
        .in_type_i(in_type), .in_dest_x_i(in_dest_x),
        .in_dest_y_i(in_dest_y), .in_source_id_i(in_source_id),
        .in_epoch_i(in_epoch), .in_block_id_i(in_block_id), .in_last_i(in_last),
        .out_valid_o(out_valid), .out_ready_i(out_ready), .out_data_o(out_data),
        .out_type_o(out_type), .out_dest_x_o(out_dest_x),
        .out_dest_y_o(out_dest_y), .out_source_id_o(out_source_id),
        .out_epoch_o(out_epoch), .out_block_id_o(out_block_id),
        .out_last_o(out_last)
    );

    always #5 clk = ~clk;

    task automatic send_flit(
        input int port,
        input int value,
        input int destination_x,
        input int destination_y,
        input logic last
    );
        in_data[port] = DATA_W'(value);
        in_dest_x[port] = X_W'(destination_x);
        in_dest_y[port] = Y_W'(destination_y);
        in_last[port] = last;
        in_valid[port] = 1;
        while (!in_ready[port]) @(negedge clk);
        @(negedge clk);
        in_valid[port] = 0;
    endtask

    always_ff @(posedge clk) begin
        if (!rst && out_valid[3] && out_ready[3]) begin
            int expected;
            expected = east_count < 4 ? 16 + east_count : 32;
            if (out_data[3] !== DATA_W'(expected)) begin
                $error("east flit %0d got %0d expected %0d",
                    east_count, out_data[3], expected);
                errors++;
            end
            if (out_last[3] !== (east_count == 3 || east_count == 4)) begin
                $error("east last mismatch at flit %0d", east_count);
                errors++;
            end
            east_count++;
        end
        if (!rst && out_valid[0] && out_ready[0]) begin
            if (out_data[0] !== DATA_W'(48) || !out_last[0]) begin
                $error("local delivery mismatch");
                errors++;
            end
            local_count++;
        end
    end

    initial begin
        in_valid = '0;
        in_data = '0;
        in_type = '{default: NOC_PARTIAL};
        in_dest_x = '0;
        in_dest_y = '0;
        in_source_id = '0;
        in_epoch = '0;
        in_block_id = '0;
        in_last = '0;
        out_ready = '1;
        east_count = 0;
        local_count = 0;
        errors = 0;

        repeat (4) @(negedge clk);
        rst = 0;

        // Local port packet A and north-port packet B contend for east. Local
        // wins the initial round-robin choice and must retain east through A3.
        fork
            begin
                send_flit(0, 16, 2, 1, 0);
                send_flit(0, 17, 2, 1, 0);
                send_flit(0, 18, 2, 1, 0);
                send_flit(0, 19, 2, 1, 1);
            end
            begin
                send_flit(1, 32, 2, 1, 1);
            end
            begin
                send_flit(4, 48, 1, 1, 1);
            end
        join

        wait (east_count == 5 && local_count == 1);
        repeat (2) @(negedge clk);
        if (errors == 0)
            $display("PASS: NoC router preserved packet lock and XY routes");
        else
            $fatal(1, "FAIL: NoC router produced %0d errors", errors);
        $finish;
    end

    initial begin
        #100000;
        $fatal(1, "FAIL: NoC router timeout");
    end
endmodule
