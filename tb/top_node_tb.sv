`timescale 1ns/1ps

import ising_pkg::*;

module top_node_tb;
    localparam int STATE_ENTRY_COUNT = 4;
    localparam int MVM_COUNT = 1;
    localparam int X_W = 2;
    localparam int Y_W = 2;
    localparam int SOURCE_ID_W = 4;
    localparam int EPOCH_W = 8;
    localparam int GLOBAL_BLOCK_ID_W = 8;
    localparam int STATE_INDEX_W = $clog2(STATE_ENTRY_COUNT);
    localparam int PARTIAL_LANES = DATA_W / ACC_W;

    logic clk = 0;
    logic rst = 1;
    logic [3:0] link_in_valid, link_in_ready;
    logic [3:0][DATA_W-1:0] link_in_data;
    logic [3:0][1:0] link_in_type;
    logic [3:0][X_W-1:0] link_in_dest_x;
    logic [3:0][Y_W-1:0] link_in_dest_y;
    logic [3:0][SOURCE_ID_W-1:0] link_in_source_id;
    logic [3:0][EPOCH_W-1:0] link_in_epoch;
    logic [3:0][GLOBAL_BLOCK_ID_W-1:0] link_in_block_id;
    logic [3:0] link_in_last;
    logic [3:0] link_out_valid, link_out_ready;
    logic [3:0][DATA_W-1:0] link_out_data;
    logic [3:0][1:0] link_out_type;
    logic [3:0][X_W-1:0] link_out_dest_x;
    logic [3:0][Y_W-1:0] link_out_dest_y;
    logic [3:0][SOURCE_ID_W-1:0] link_out_source_id;
    logic [3:0][EPOCH_W-1:0] link_out_epoch;
    logic [3:0][GLOBAL_BLOCK_ID_W-1:0] link_out_block_id;
    logic [3:0] link_out_last;

    logic h1_tx_valid, h1_tx_ready;
    logic [DATA_W-1:0] h1_tx_data;
    logic [1:0] h1_tx_type;
    logic [X_W-1:0] h1_tx_dest_x;
    logic [Y_W-1:0] h1_tx_dest_y;
    logic [SOURCE_ID_W-1:0] h1_tx_source_id;
    logic [EPOCH_W-1:0] h1_tx_epoch;
    logic [GLOBAL_BLOCK_ID_W-1:0] h1_tx_block_id;
    logic h1_tx_last;
    logic h1_rx_valid, h1_rx_ready;
    logic [DATA_W-1:0] h1_rx_data;
    logic [1:0] h1_rx_type;
    logic [SOURCE_ID_W-1:0] h1_rx_source_id;
    logic [EPOCH_W-1:0] h1_rx_epoch;
    logic [GLOBAL_BLOCK_ID_W-1:0] h1_rx_block_id;
    logic h1_rx_last;

    logic cross_iter_start, cross_iter_done;
    logic [EPOCH_W-1:0] epoch;
    logic cross_schedule_done;
    logic [MVM_COUNT-1:0] cross_cmd_valid, cross_cmd_ready;
    logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0]
        cross_state_a_index, cross_state_b_index;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
        cross_block_a_id, cross_block_b_id;
    logic [MVM_COUNT-1:0] cross_weight_valid, cross_weight_ready;
    logic [MVM_COUNT-1:0][DATA_W-1:0] cross_weight_data;
    int local_beats;
    int east_beats;
    int errors;

    top_node #(
        .STATE_ENTRY_COUNT(STATE_ENTRY_COUNT), .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W), .X_W(X_W), .Y_W(Y_W),
        .SOURCE_ID_W(SOURCE_ID_W), .EPOCH_W(EPOCH_W),
        .BLOCKS_PER_H1(2), .MESH_X_COUNT(2), .FIFO_DEPTH(4),
        .NODE_ID(0), .NODE_X(0), .NODE_Y(0)
    ) dut (.*,
        .link_in_valid_i(link_in_valid), .link_in_ready_o(link_in_ready),
        .link_in_data_i(link_in_data), .link_in_type_i(link_in_type),
        .link_in_dest_x_i(link_in_dest_x), .link_in_dest_y_i(link_in_dest_y),
        .link_in_source_id_i(link_in_source_id), .link_in_epoch_i(link_in_epoch),
        .link_in_block_id_i(link_in_block_id), .link_in_last_i(link_in_last),
        .link_out_valid_o(link_out_valid), .link_out_ready_i(link_out_ready),
        .link_out_data_o(link_out_data), .link_out_type_o(link_out_type),
        .link_out_dest_x_o(link_out_dest_x), .link_out_dest_y_o(link_out_dest_y),
        .link_out_source_id_o(link_out_source_id), .link_out_epoch_o(link_out_epoch),
        .link_out_block_id_o(link_out_block_id), .link_out_last_o(link_out_last),
        .h1_tx_valid_i(h1_tx_valid), .h1_tx_ready_o(h1_tx_ready),
        .h1_tx_data_i(h1_tx_data), .h1_tx_type_i(h1_tx_type),
        .h1_tx_dest_x_i(h1_tx_dest_x), .h1_tx_dest_y_i(h1_tx_dest_y),
        .h1_tx_source_id_i(h1_tx_source_id), .h1_tx_epoch_i(h1_tx_epoch),
        .h1_tx_block_id_i(h1_tx_block_id), .h1_tx_last_i(h1_tx_last),
        .h1_rx_valid_o(h1_rx_valid), .h1_rx_ready_i(h1_rx_ready),
        .h1_rx_data_o(h1_rx_data), .h1_rx_type_o(h1_rx_type),
        .h1_rx_source_id_o(h1_rx_source_id), .h1_rx_epoch_o(h1_rx_epoch),
        .h1_rx_block_id_o(h1_rx_block_id), .h1_rx_last_o(h1_rx_last),
        .cross_iter_start_i(cross_iter_start), .cross_iter_done_o(cross_iter_done),
        .epoch_i(epoch), .cross_schedule_done_i(cross_schedule_done),
        .cross_dma_cmd_valid_i(cross_cmd_valid),
        .cross_dma_cmd_ready_o(cross_cmd_ready),
        .cross_dma_state_a_index_i(cross_state_a_index),
        .cross_dma_state_b_index_i(cross_state_b_index),
        .cross_dma_block_a_id_i(cross_block_a_id),
        .cross_dma_block_b_id_i(cross_block_b_id),
        .cross_dma_weight_valid_i(cross_weight_valid),
        .cross_dma_weight_ready_o(cross_weight_ready),
        .cross_dma_weight_data_i(cross_weight_data)
    );

    always #5 clk = ~clk;

    task automatic send_state(
        input logic [GLOBAL_BLOCK_ID_W-1:0] block_id,
        input logic [SPIN_COUNT-1:0] state
    );
        // Enter through the west physical link and route to this node.
        link_in_data[3] = DATA_W'(state);
        link_in_type[3] = NOC_STATE;
        link_in_dest_x[3] = 0;
        link_in_dest_y[3] = 0;
        link_in_epoch[3] = epoch;
        link_in_block_id[3] = block_id;
        link_in_last[3] = 1;
        link_in_valid[3] = 1;
        while (!link_in_ready[3]) @(negedge clk);
        @(negedge clk);
        link_in_valid[3] = 0;
    endtask

    always_ff @(posedge clk) begin
        if (!rst && h1_rx_valid && h1_rx_ready) begin
            if (h1_rx_type != NOC_PARTIAL || h1_rx_block_id != 0) begin
                $error("unexpected local packet type/block");
                errors++;
            end
            for (int lane = 0; lane < PARTIAL_LANES; lane++)
                if ($signed(h1_rx_data[lane*ACC_W +: ACC_W]) !== -32) begin
                    $error("local lane got %0d expected -32",
                        $signed(h1_rx_data[lane*ACC_W +: ACC_W]));
                    errors++;
                end
            local_beats++;
        end

        // Link 2 is east. Block 2 belongs to H1 1 at coordinate (1,0).
        if (!rst && link_out_valid[2] && link_out_ready[2]) begin
            if (link_out_type[2] != NOC_PARTIAL || link_out_block_id[2] != 2 ||
                link_out_dest_x[2] != 1 || link_out_dest_y[2] != 0) begin
                $error("unexpected east packet metadata");
                errors++;
            end
            for (int lane = 0; lane < PARTIAL_LANES; lane++)
                if ($signed(link_out_data[2][lane*ACC_W +: ACC_W]) !== 32) begin
                    $error("east lane got %0d expected 32",
                        $signed(link_out_data[2][lane*ACC_W +: ACC_W]));
                    errors++;
                end
            east_beats++;
        end
    end

    initial begin
        link_in_valid = '0;
        link_in_data = '0;
        link_in_type = '0;
        link_in_dest_x = '0;
        link_in_dest_y = '0;
        link_in_source_id = '0;
        link_in_epoch = '0;
        link_in_block_id = '0;
        link_in_last = '0;
        link_out_ready = '1;
        h1_tx_valid = 0;
        h1_tx_data = '0;
        h1_tx_type = NOC_STATE;
        h1_tx_dest_x = '0;
        h1_tx_dest_y = '0;
        h1_tx_source_id = '0;
        h1_tx_epoch = '0;
        h1_tx_block_id = '0;
        h1_tx_last = 1;
        h1_rx_ready = 1;
        cross_iter_start = 0;
        epoch = 8'd3;
        cross_schedule_done = 0;
        cross_cmd_valid = '0;
        cross_state_a_index = '0;
        cross_state_b_index = '0;
        cross_block_a_id = '0;
        cross_block_b_id = '0;
        cross_weight_valid = '0;
        cross_weight_data = '0;
        local_beats = 0;
        east_beats = 0;
        errors = 0;

        repeat (4) @(negedge clk);
        rst = 0;
        send_state(0, '1);
        send_state(2, '0);
        repeat (4) @(negedge clk);

        cross_iter_start = 1;
        @(negedge clk);
        cross_iter_start = 0;

        cross_state_a_index[0] = 0;
        cross_state_b_index[0] = 2;
        cross_block_a_id[0] = 0;
        cross_block_b_id[0] = 2;
        cross_cmd_valid[0] = 1;
        while (!cross_cmd_ready[0]) @(negedge clk);
        @(negedge clk);
        cross_cmd_valid[0] = 0;

        cross_weight_data[0] = {DATA_W/WEIGHT_W{8'h01}};
        cross_weight_valid[0] = 1;
        for (int beat = 0; beat < SPIN_COUNT; beat++) begin
            while (!cross_weight_ready[0]) @(negedge clk);
            @(negedge clk);
        end
        cross_weight_valid[0] = 0;
        cross_schedule_done = 1;
        @(negedge clk);
        cross_schedule_done = 0;

        wait (cross_iter_done && local_beats == 4 && east_beats == 4);
        repeat (2) @(negedge clk);
        if (errors == 0)
            $display("PASS: top node received states, computed, and routed both partials");
        else
            $fatal(1, "FAIL: top node produced %0d errors", errors);
        $finish;
    end

    initial begin
        #200000;
        $fatal(1, "FAIL: top node timeout");
    end
endmodule
