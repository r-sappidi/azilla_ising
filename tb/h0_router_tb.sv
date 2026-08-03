`timescale 1ns/1ps

import ising_pkg::*;

module h0_router_tb;
    localparam int CORE_COUNT = 3;
    localparam int MVM_COUNT = 1;
    localparam int CORE_ID_W = $clog2(CORE_COUNT);
    localparam int PARTIAL_LANES = DATA_W / ACC_W;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic iter_start;
    logic iter_done;
    logic [CORE_COUNT-1:0][SPIN_COUNT-1:0] state_current;
    logic schedule_done;
    logic [MVM_COUNT-1:0] cmd_valid, cmd_ready;
    logic [MVM_COUNT-1:0][CORE_ID_W-1:0] core_a, core_b;
    logic [MVM_COUNT-1:0] weight_valid, weight_ready;
    logic [MVM_COUNT-1:0][DATA_W-1:0] weight_data;
    logic [CORE_COUNT-1:0] partial_valid, partial_ready;
    logic signed [CORE_COUNT-1:0][DATA_W-1:0] partial_data;

    integer signed j01 [0:SPIN_COUNT-1][0:SPIN_COUNT-1];
    integer signed j12 [0:SPIN_COUNT-1][0:SPIN_COUNT-1];
    integer signed expected [0:CORE_COUNT-1][0:SPIN_COUNT-1];
    integer signed received [0:CORE_COUNT-1][0:SPIN_COUNT-1];
    int receive_beat [0:CORE_COUNT-1];
    int errors;

    h0_router #(.CORE_COUNT(CORE_COUNT), .MVM_COUNT(MVM_COUNT)) dut (
        .clk, .rst, .iter_start, .iter_done,
        .state_current_i(state_current),
        .schedule_done_i(schedule_done),
        .dma_cmd_valid_i(cmd_valid), .dma_cmd_ready_o(cmd_ready),
        .dma_core_a_i(core_a), .dma_core_b_i(core_b),
        .dma_weight_valid_i(weight_valid), .dma_weight_ready_o(weight_ready),
        .dma_weight_data_i(weight_data),
        .partial_valid_o(partial_valid), .partial_ready_i(partial_ready),
        .partial_data_o(partial_data)
    );

    always #5 clk = ~clk;

    // Model the endpoint cores by accumulating every accepted partial beat.
    always_ff @(posedge clk) begin
        if (!rst) begin
            for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
                if (partial_valid[core_index] && partial_ready[core_index]) begin
                    for (int lane = 0; lane < PARTIAL_LANES; lane++) begin
                        received[core_index]
                            [receive_beat[core_index]*PARTIAL_LANES + lane] <=
                            received[core_index]
                                [receive_beat[core_index]*PARTIAL_LANES + lane] +
                            $signed(partial_data[core_index][lane*ACC_W +: ACC_W]);
                    end
                    receive_beat[core_index] <=
                        receive_beat[core_index] == 3 ? 0 : receive_beat[core_index] + 1;
                end
            end
        end
    end

    task automatic send_block(
        input logic [CORE_ID_W-1:0] destination_a,
        input logic [CORE_ID_W-1:0] destination_b,
        input logic second_block
    );
        logic [DATA_W-1:0] beat;
        begin
            @(negedge clk);
            core_a[0] = destination_a;
            core_b[0] = destination_b;
            cmd_valid[0] = 1'b1;
            while (!cmd_ready[0]) @(negedge clk);
            @(negedge clk);
            cmd_valid[0] = 1'b0;

            for (int row = 0; row < SPIN_COUNT; row++) begin
                beat = '0;
                for (int column = 0; column < SPIN_COUNT; column++) begin
                    if (second_block)
                        beat[column*WEIGHT_W +: WEIGHT_W] =
                            j12[row][column][WEIGHT_W-1:0];
                    else
                        beat[column*WEIGHT_W +: WEIGHT_W] =
                            j01[row][column][WEIGHT_W-1:0];
                end
                @(negedge clk);
                weight_data[0] = beat;
                weight_valid[0] = 1'b1;
                while (!weight_ready[0]) @(negedge clk);
            end
            @(negedge clk);
            weight_valid[0] = 1'b0;
        end
    endtask

    initial begin
        iter_start = 1'b0;
        schedule_done = 1'b0;
        cmd_valid = '0;
        core_a = '0;
        core_b = '0;
        weight_valid = '0;
        weight_data = '0;
        partial_ready = '1;
        state_current[0] = 32'ha596_3cc3;
        state_current[1] = 32'h5a69_c33c;
        state_current[2] = 32'hc3a5_9669;
        errors = 0;

        for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
            receive_beat[core_index] = 0;
            for (int spin = 0; spin < SPIN_COUNT; spin++) begin
                expected[core_index][spin] = 0;
                received[core_index][spin] = 0;
            end
        end

        for (int row = 0; row < SPIN_COUNT; row++) begin
            for (int column = 0; column < SPIN_COUNT; column++) begin
                j01[row][column] = ((row*5 + column*3 + 1) % 15) - 7;
                j12[row][column] = ((row*7 + column*2 + 4) % 17) - 8;

                expected[0][row] += state_current[1][column]
                    ? j01[row][column] : -j01[row][column];
                expected[1][column] += state_current[0][row]
                    ? j01[row][column] : -j01[row][column];
                expected[1][row] += state_current[2][column]
                    ? j12[row][column] : -j12[row][column];
                expected[2][column] += state_current[1][row]
                    ? j12[row][column] : -j12[row][column];
            end
        end

        repeat (4) @(negedge clk);
        rst = 1'b0;
        repeat (2) @(negedge clk);
        iter_start = 1'b1;
        @(negedge clk);
        iter_start = 1'b0;

        send_block(0, 1, 1'b0);
        send_block(1, 2, 1'b1);

        @(negedge clk);
        schedule_done = 1'b1;
        @(negedge clk);
        schedule_done = 1'b0;

        wait (iter_done);
        @(negedge clk);

        for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
            for (int spin = 0; spin < SPIN_COUNT; spin++) begin
                if (received[core_index][spin] !== expected[core_index][spin]) begin
                    $error("core %0d spin %0d got %0d expected %0d",
                           core_index, spin, received[core_index][spin],
                           expected[core_index][spin]);
                    errors++;
                end
            end
        end

        if (errors == 0)
            $display("PASS: H0 router matched both symmetric golden MVM blocks");
        else
            $fatal(1, "FAIL: H0 router produced %0d errors", errors);
        $finish;
    end

    initial begin
        #100000;
        $fatal(1, "FAIL: H0 router simulation timeout");
    end
endmodule
