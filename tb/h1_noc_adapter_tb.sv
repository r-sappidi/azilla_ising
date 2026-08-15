`timescale 1ns/1ps

import ising_pkg::*;

module h1_noc_adapter_tb;
    localparam int GLOBAL_BLOCK_ID_W = 8;
    localparam int X_W = 2;
    localparam int Y_W = 2;
    localparam int SOURCE_ID_W = 4;
    localparam int EPOCH_W = 8;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic [EPOCH_W-1:0] current_epoch;

    logic state_publish_valid, state_publish_ready;
    logic [SPIN_COUNT-1:0] state_publish_data;
    logic [GLOBAL_BLOCK_ID_W-1:0] state_publish_block_id;
    logic [X_W-1:0] state_publish_dest_x;
    logic [Y_W-1:0] state_publish_dest_y;
    logic done_publish_valid, done_publish_ready;
    logic [X_W-1:0] done_publish_dest_x;
    logic [Y_W-1:0] done_publish_dest_y;

    logic noc_tx_valid, noc_tx_ready;
    logic [DATA_W-1:0] noc_tx_data;
    logic [1:0] noc_tx_type;
    logic [X_W-1:0] noc_tx_dest_x;
    logic [Y_W-1:0] noc_tx_dest_y;
    logic [SOURCE_ID_W-1:0] noc_tx_source_id;
    logic [EPOCH_W-1:0] noc_tx_epoch;
    logic [GLOBAL_BLOCK_ID_W-1:0] noc_tx_block_id;
    logic noc_tx_last;

    logic noc_rx_valid, noc_rx_ready;
    logic [DATA_W-1:0] noc_rx_data;
    logic [1:0] noc_rx_type;
    logic [SOURCE_ID_W-1:0] noc_rx_source_id;
    logic [EPOCH_W-1:0] noc_rx_epoch;
    logic [GLOBAL_BLOCK_ID_W-1:0] noc_rx_block_id;
    logic noc_rx_last;

    logic parent_partial_valid, parent_partial_ready;
    logic signed [DATA_W-1:0] parent_partial_data;
    logic [GLOBAL_BLOCK_ID_W-1:0] parent_partial_block_id;
    logic parent_partials_done;
    int errors = 0;
    int partial_beats = 0;
    int done_pulses = 0;

    h1_noc_adapter #(
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .X_W(X_W), .Y_W(Y_W), .SOURCE_ID_W(SOURCE_ID_W),
        .EPOCH_W(EPOCH_W), .SOURCE_ID(5)
    ) dut (
        .clk, .rst, .current_epoch_i(current_epoch),
        .state_publish_valid_i(state_publish_valid),
        .state_publish_ready_o(state_publish_ready),
        .state_publish_data_i(state_publish_data),
        .state_publish_block_id_i(state_publish_block_id),
        .state_publish_dest_x_i(state_publish_dest_x),
        .state_publish_dest_y_i(state_publish_dest_y),
        .done_publish_valid_i(done_publish_valid),
        .done_publish_ready_o(done_publish_ready),
        .done_publish_dest_x_i(done_publish_dest_x),
        .done_publish_dest_y_i(done_publish_dest_y),
        .noc_tx_valid_o(noc_tx_valid), .noc_tx_ready_i(noc_tx_ready),
        .noc_tx_data_o(noc_tx_data), .noc_tx_type_o(noc_tx_type),
        .noc_tx_dest_x_o(noc_tx_dest_x), .noc_tx_dest_y_o(noc_tx_dest_y),
        .noc_tx_source_id_o(noc_tx_source_id), .noc_tx_epoch_o(noc_tx_epoch),
        .noc_tx_block_id_o(noc_tx_block_id), .noc_tx_last_o(noc_tx_last),
        .noc_rx_valid_i(noc_rx_valid), .noc_rx_ready_o(noc_rx_ready),
        .noc_rx_data_i(noc_rx_data), .noc_rx_type_i(noc_rx_type),
        .noc_rx_source_id_i(noc_rx_source_id), .noc_rx_epoch_i(noc_rx_epoch),
        .noc_rx_block_id_i(noc_rx_block_id), .noc_rx_last_i(noc_rx_last),
        .parent_partial_valid_o(parent_partial_valid),
        .parent_partial_ready_i(parent_partial_ready),
        .parent_partial_data_o(parent_partial_data),
        .parent_partial_block_id_o(parent_partial_block_id),
        .parent_partials_done_o(parent_partials_done)
    );

    always #5 clk = ~clk;

    always_ff @(posedge clk) begin
        if (!rst && parent_partial_valid && parent_partial_ready) begin
            if (parent_partial_block_id != 8'd19 ||
                parent_partial_data !== $signed(noc_rx_data)) begin
                $error("partial metadata/data mismatch");
                errors++;
            end
            partial_beats++;
        end
        if (!rst && parent_partials_done)
            done_pulses++;
    end

    initial begin
        current_epoch = 8'd7;
        state_publish_valid = 1'b0;
        state_publish_data = 32'h96a5_3cc3;
        state_publish_block_id = 8'd19;
        state_publish_dest_x = 2'd2;
        state_publish_dest_y = 2'd1;
        done_publish_valid = 1'b0;
        done_publish_dest_x = 2'd3;
        done_publish_dest_y = 2'd2;
        noc_tx_ready = 1'b0;
        noc_rx_valid = 1'b0;
        noc_rx_data = '0;
        noc_rx_type = NOC_PARTIAL;
        noc_rx_source_id = 4'd3;
        noc_rx_epoch = current_epoch;
        noc_rx_block_id = 8'd19;
        noc_rx_last = 1'b0;
        parent_partial_ready = 1'b0;

        repeat (3) @(negedge clk);
        rst = 1'b0;

        // A publication must remain asserted and backpressured until the NoC
        // accepts its single STATE flit.
        state_publish_valid = 1'b1;
        repeat (2) begin
            @(negedge clk);
            if (state_publish_ready || !noc_tx_valid || noc_tx_type != NOC_STATE ||
                noc_tx_data[SPIN_COUNT-1:0] != state_publish_data ||
                noc_tx_block_id != 8'd19 || noc_tx_dest_x != 2'd2 ||
                noc_tx_dest_y != 2'd1 || noc_tx_source_id != 4'd5 ||
                noc_tx_epoch != current_epoch || !noc_tx_last) begin
                $error("state publication packet mismatch while stalled");
                errors++;
            end
        end
        noc_tx_ready = 1'b1;
        @(negedge clk);
        if (!state_publish_ready) begin
            $error("state publication did not accept ready");
            errors++;
        end
        state_publish_valid = 1'b0;

        // Forward one four-beat partial while preserving receiver backpressure.
        noc_rx_type = NOC_PARTIAL;
        noc_rx_valid = 1'b1;
        for (int beat = 0; beat < 4; beat++) begin
            noc_rx_data = DATA_W'(32'h1000 + beat);
            noc_rx_last = beat == 3;
            parent_partial_ready = beat != 1;
            @(negedge clk);
            if (beat == 1) begin
                if (noc_rx_ready || !parent_partial_valid) begin
                    $error("partial backpressure was not propagated");
                    errors++;
                end
                parent_partial_ready = 1'b1;
                @(negedge clk);
            end
        end
        noc_rx_valid = 1'b0;
        @(negedge clk);

        // A stale done packet is consumed but must not complete this epoch.
        noc_rx_type = NOC_EPOCH_DONE;
        noc_rx_epoch = 8'd6;
        noc_rx_last = 1'b1;
        noc_rx_valid = 1'b1;
        @(negedge clk);
        noc_rx_valid = 1'b0;
        repeat (2) @(negedge clk);
        if (done_pulses != 0) begin
            $error("stale epoch generated completion");
            errors++;
        end

        // A matching done packet produces exactly one pulse.
        noc_rx_epoch = current_epoch;
        noc_rx_valid = 1'b1;
        @(negedge clk);
        noc_rx_valid = 1'b0;
        repeat (2) @(negedge clk);

        if (partial_beats != 4) begin
            $error("received %0d partial beats expected 4", partial_beats);
            errors++;
        end
        if (done_pulses != 1) begin
            $error("received %0d done pulses expected 1", done_pulses);
            errors++;
        end

        if (errors == 0)
            $display("PASS: H1 NoC adapter packetized state and translated partial/done traffic");
        else
            $fatal(1, "FAIL: %0d errors", errors);
        $finish;
    end
endmodule
