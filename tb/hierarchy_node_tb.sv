`timescale 1ns/1ps

import ising_pkg::*;

module hierarchy_node_tb;
    localparam int STATE_ENTRY_COUNT = 3;
    localparam int MVM_COUNT = 1;
    localparam int STATE_INDEX_W = $clog2(STATE_ENTRY_COUNT);
    localparam int GLOBAL_BLOCK_ID_W = 16;
    localparam int PARTIAL_LANES = DATA_W / ACC_W;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic iter_start, iter_done, schedule_done;
    logic state_valid, state_ready;
    logic [STATE_INDEX_W-1:0] state_index;
    logic [SPIN_COUNT-1:0] state_data;
    logic [MVM_COUNT-1:0] cmd_valid, cmd_ready;
    logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0] cmd_state_a, cmd_state_b;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] cmd_block_a, cmd_block_b;
    logic [MVM_COUNT-1:0] weight_valid, weight_ready;
    logic [MVM_COUNT-1:0][DATA_W-1:0] weight_data;
    logic [MVM_COUNT-1:0] partial_valid, partial_ready, partial_last;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] partial_data;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] partial_block_id;

    logic [SPIN_COUNT-1:0] states [0:STATE_ENTRY_COUNT-1];
    integer signed weights [0:SPIN_COUNT-1][0:SPIN_COUNT-1];
    integer signed expected [0:1][0:SPIN_COUNT-1];
    integer signed received [0:1][0:SPIN_COUNT-1];
    int receive_beat;
    int errors;

    hierarchy_node #(
        .STATE_ENTRY_COUNT(STATE_ENTRY_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W)
    ) dut (
        .clk, .rst, .iter_start, .iter_done,
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

    always_ff @(posedge clk) begin
        if (!rst && partial_valid[0] && partial_ready[0]) begin
            int destination;
            destination = partial_block_id[0] == 16'd100 ? 0 : 1;
            if (partial_block_id[0] != 16'd100 && partial_block_id[0] != 16'd200) begin
                $error("unexpected destination block %0d", partial_block_id[0]);
                errors++;
            end
            for (int lane = 0; lane < PARTIAL_LANES; lane++)
                received[destination][receive_beat*PARTIAL_LANES + lane] <=
                    received[destination][receive_beat*PARTIAL_LANES + lane] +
                    $signed(partial_data[0][lane*ACC_W +: ACC_W]);
            if (partial_last[0] != (receive_beat == 3)) begin
                $error("partial_last mismatch on beat %0d", receive_beat);
                errors++;
            end
            receive_beat <= partial_last[0] ? 0 : receive_beat + 1;
        end
    end

    task automatic load_state(input int index);
        @(negedge clk);
        state_index = STATE_INDEX_W'(index);
        state_data = states[index];
        state_valid = 1'b1;
        while (!state_ready) @(negedge clk);
        @(negedge clk);
        state_valid = 1'b0;
    endtask

    task automatic send_block;
        logic [DATA_W-1:0] beat;
        @(negedge clk);
        cmd_state_a[0] = 0;
        cmd_state_b[0] = 1;
        cmd_block_a[0] = 16'd100;
        cmd_block_b[0] = 16'd200;
        cmd_valid[0] = 1'b1;
        while (!cmd_ready[0]) @(negedge clk);
        @(negedge clk);
        cmd_valid[0] = 1'b0;

        for (int row = 0; row < SPIN_COUNT; row++) begin
            beat = '0;
            for (int column = 0; column < SPIN_COUNT; column++)
                beat[column*WEIGHT_W +: WEIGHT_W] =
                    weights[row][column][WEIGHT_W-1:0];
            weight_data[0] = beat;
            weight_valid[0] = 1'b1;
            while (!weight_ready[0]) @(negedge clk);
            @(negedge clk);
        end
        weight_valid[0] = 1'b0;
    endtask

    initial begin
        iter_start = 0;
        schedule_done = 0;
        state_valid = 0;
        state_index = '0;
        state_data = '0;
        cmd_valid = '0;
        cmd_state_a = '0;
        cmd_state_b = '0;
        cmd_block_a = '0;
        cmd_block_b = '0;
        weight_valid = '0;
        weight_data = '0;
        partial_ready = 1;
        receive_beat = 0;
        errors = 0;
        states[0] = 32'ha596_3cc3;
        states[1] = 32'h5a69_c33c;
        states[2] = 32'hc3a5_9669;

        for (int destination = 0; destination < 2; destination++)
            for (int spin = 0; spin < SPIN_COUNT; spin++) begin
                expected[destination][spin] = 0;
                received[destination][spin] = 0;
            end

        for (int row = 0; row < SPIN_COUNT; row++)
            for (int column = 0; column < SPIN_COUNT; column++) begin
                weights[row][column] = ((row*5 + column*3 + 1) % 15) - 7;
                expected[0][row] += states[1][column]
                    ? weights[row][column] : -weights[row][column];
                expected[1][column] += states[0][row]
                    ? weights[row][column] : -weights[row][column];
            end

        repeat (4) @(negedge clk);
        rst = 0;
        repeat (2) @(negedge clk);
        load_state(0);
        load_state(1);

        iter_start = 1;
        @(negedge clk);
        iter_start = 0;
        send_block();
        schedule_done = 1;
        @(negedge clk);
        schedule_done = 0;

        wait (iter_done);
        @(negedge clk);
        for (int destination = 0; destination < 2; destination++)
            for (int spin = 0; spin < SPIN_COUNT; spin++)
                if (received[destination][spin] !== expected[destination][spin]) begin
                    $error("destination %0d spin %0d got %0d expected %0d",
                           destination, spin, received[destination][spin],
                           expected[destination][spin]);
                    errors++;
                end

        if (errors == 0)
            $display("PASS: adapter-ready hierarchy compute node matched golden arithmetic");
        else
            $fatal(1, "FAIL: hierarchy node produced %0d errors", errors);
        $finish;
    end

    initial begin
        #100000;
        $fatal(1, "FAIL: hierarchy node simulation timeout");
    end
endmodule
