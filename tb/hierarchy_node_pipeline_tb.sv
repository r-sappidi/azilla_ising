`timescale 1ns/1ps

import ising_pkg::*;

module hierarchy_node_pipeline_tb;
    localparam int STATE_ENTRY_COUNT = 2;
    localparam int MVM_COUNT = 1;
    localparam int GLOBAL_BLOCK_ID_W = 16;
    localparam int PARTIAL_LANES = DATA_W / ACC_W;
    localparam int PARTIAL_BEATS = SPIN_COUNT * ACC_W / DATA_W;

    logic clk = 0;
    logic rst = 1;
    logic iter_start, iter_done, schedule_done;
    logic state_valid, state_ready;
    logic [$clog2(STATE_ENTRY_COUNT)-1:0] state_index;
    logic [SPIN_COUNT-1:0] state_data;
    logic [MVM_COUNT-1:0] cmd_valid, cmd_ready;
    logic [MVM_COUNT-1:0][$clog2(STATE_ENTRY_COUNT)-1:0]
        cmd_state_a, cmd_state_b;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
        cmd_block_a, cmd_block_b;
    logic [MVM_COUNT-1:0] weight_valid, weight_ready;
    logic [MVM_COUNT-1:0][DATA_W-1:0] weight_data;
    logic [MVM_COUNT-1:0] partial_valid, partial_ready, partial_last;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] partial_data;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] partial_block_id;
    int accepted_beats;
    int errors;

    hierarchy_node #(
        .STATE_ENTRY_COUNT(STATE_ENTRY_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W)
    ) dut (.*,
        .state_valid_i(state_valid), .state_ready_o(state_ready),
        .state_index_i(state_index), .state_data_i(state_data),
        .schedule_done_i(schedule_done),
        .dma_cmd_valid_i(cmd_valid), .dma_cmd_ready_o(cmd_ready),
        .dma_state_a_index_i(cmd_state_a),
        .dma_state_b_index_i(cmd_state_b),
        .dma_block_a_i(cmd_block_a), .dma_block_b_i(cmd_block_b),
        .dma_weight_valid_i(weight_valid), .dma_weight_ready_o(weight_ready),
        .dma_weight_data_i(weight_data),
        .partial_valid_o(partial_valid), .partial_ready_i(partial_ready),
        .partial_data_o(partial_data), .partial_block_id_o(partial_block_id),
        .partial_last_o(partial_last)
    );

    always #5 clk = ~clk;

    task automatic load_state(input logic index);
        @(negedge clk);
        state_index = index;
        state_data = '1;
        state_valid = 1;
        while (!state_ready) @(negedge clk);
        @(negedge clk);
        state_valid = 0;
    endtask

    task automatic send_block(
        input logic [GLOBAL_BLOCK_ID_W-1:0] block_a,
        input logic [GLOBAL_BLOCK_ID_W-1:0] block_b,
        input logic signed [WEIGHT_W-1:0] weight
    );
        cmd_state_a[0] = 0;
        cmd_state_b[0] = 1;
        cmd_block_a[0] = block_a;
        cmd_block_b[0] = block_b;
        cmd_valid[0] = 1;
        while (!cmd_ready[0]) @(negedge clk);
        @(negedge clk);
        cmd_valid[0] = 0;

        weight_data[0] = {DATA_W/WEIGHT_W{weight}};
        weight_valid[0] = 1;
        for (int beat = 0; beat < SPIN_COUNT; beat++) begin
            while (!weight_ready[0]) @(negedge clk);
            @(negedge clk);
        end
        weight_valid[0] = 0;
    endtask

    always_ff @(posedge clk) begin
        if (!rst && partial_valid[0] && partial_ready[0]) begin
            int expected_value;
            expected_value = partial_block_id[0] < 16'd300 ? 32 : 64;
            for (int lane = 0; lane < PARTIAL_LANES; lane++) begin
                if ($signed(partial_data[0][lane*ACC_W +: ACC_W]) !==
                    expected_value) begin
                    $error("block %0d lane %0d got %0d expected %0d",
                        partial_block_id[0], lane,
                        $signed(partial_data[0][lane*ACC_W +: ACC_W]),
                        expected_value);
                    errors++;
                end
            end
            accepted_beats++;
        end
    end

    initial begin
        iter_start = 0;
        schedule_done = 0;
        state_valid = 0;
        state_index = 0;
        state_data = '0;
        cmd_valid = '0;
        cmd_state_a = '0;
        cmd_state_b = '0;
        cmd_block_a = '0;
        cmd_block_b = '0;
        weight_valid = '0;
        weight_data = '0;
        partial_ready = '0;
        accepted_beats = 0;
        errors = 0;

        repeat (4) @(negedge clk);
        rst = 0;
        repeat (2) @(negedge clk);
        load_state(0);
        load_state(1);

        iter_start = 1;
        @(negedge clk);
        iter_start = 0;

        send_block(16'd100, 16'd200, 8'sd1);
        send_block(16'd300, 16'd400, 8'sd2);

        schedule_done = 1;
        @(negedge clk);
        schedule_done = 0;

        // Output remains blocked. Both bits becoming valid proves that the
        // second block computed while the first result was awaiting transfer.
        wait (dut.engine_result_slot_valid[0] == 2'b11);
        if (accepted_beats != 0) begin
            $error("result transferred while partial_ready was low");
            errors++;
        end

        partial_ready = '1;
        wait (iter_done);
        @(negedge clk);

        if (accepted_beats != 4*PARTIAL_BEATS) begin
            $error("accepted %0d beats, expected %0d",
                accepted_beats, 4*PARTIAL_BEATS);
            errors++;
        end

        if (errors == 0)
            $display("PASS: hierarchy compute overlapped with blocked serialization");
        else
            $fatal(1, "FAIL: pipelined hierarchy node produced %0d errors", errors);
        $finish;
    end

    initial begin
        #200000;
        $fatal(1, "FAIL: hierarchy pipeline test timeout");
    end
endmodule
