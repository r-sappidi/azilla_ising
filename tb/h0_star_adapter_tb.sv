`timescale 1ns/1ps

import ising_pkg::*;

module h0_star_adapter_tb;
    localparam int CORE_COUNT = 2;
    localparam int MVM_COUNT = 2;
    localparam int GLOBAL_BLOCK_ID_W = 8;

    logic clk = 0;
    logic rst = 1;
    logic [MVM_COUNT-1:0] partial_valid;
    logic [MVM_COUNT-1:0] partial_ready;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] partial_data;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] partial_block_id;
    logic [MVM_COUNT-1:0] partial_last;
    logic [CORE_COUNT-1:0] core_valid;
    logic [CORE_COUNT-1:0] core_ready;
    logic signed [CORE_COUNT-1:0][DATA_W-1:0] core_data;

    int engine_beat [0:MVM_COUNT-1];
    int core_beat [0:CORE_COUNT-1];
    int errors;
    logic collision_phase;

    h0_star_adapter #(
        .CORE_COUNT(CORE_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .BASE_BLOCK_ID(8'd10)
    ) dut (
        .clk,
        .rst,
        .partial_valid_i(partial_valid),
        .partial_ready_o(partial_ready),
        .partial_data_i(partial_data),
        .partial_block_id_i(partial_block_id),
        .partial_last_i(partial_last),
        .core_partial_valid_o(core_valid),
        .core_partial_ready_i(core_ready),
        .core_partial_data_o(core_data)
    );

    always #5 clk = ~clk;

    always_comb begin
        for (int engine = 0; engine < MVM_COUNT; engine++) begin
            partial_data[engine] = '0;
            partial_data[engine][7:0] = 8'(8'h40*engine + engine_beat[engine]);
            partial_last[engine] = engine_beat[engine] == 3;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst) begin
            for (int engine = 0; engine < MVM_COUNT; engine++) begin
                if (partial_valid[engine] && partial_ready[engine]) begin
                    engine_beat[engine] <= partial_last[engine] ? 0 : engine_beat[engine] + 1;
                    if (partial_last[engine])
                        partial_valid[engine] <= 1'b0;
                end
            end

            for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
                if (core_valid[core_index] && core_ready[core_index]) begin
                    if (collision_phase && core_index == 0) begin
                        if (core_beat[0] < 4 && core_data[0][7:6] != 2'b00) begin
                            $error("engine packets interleaved at core zero");
                            errors++;
                        end
                        if (core_beat[0] >= 4 && core_data[0][7:6] != 2'b01) begin
                            $error("second packet did not follow first packet");
                            errors++;
                        end
                    end
                    core_beat[core_index] <= core_beat[core_index] + 1;
                end
            end
        end
    end

    initial begin
        partial_valid = '0;
        partial_block_id = '0;
        core_ready = '1;
        collision_phase = 1'b1;
        errors = 0;
        for (int index = 0; index < MVM_COUNT; index++) engine_beat[index] = 0;
        for (int index = 0; index < CORE_COUNT; index++) core_beat[index] = 0;

        repeat (3) @(negedge clk);
        rst = 0;

        // Both engines target core zero. Engine zero must retain the output
        // for all four beats before engine one is accepted.
        partial_block_id[0] = 8'd10;
        partial_block_id[1] = 8'd10;
        partial_valid = 2'b11;
        wait (core_beat[0] == 8);
        @(negedge clk);
        partial_valid = '0;

        // Different destinations must transfer concurrently.
        collision_phase = 1'b0;
        core_beat = '{default: 0};
        partial_block_id[0] = 8'd10;
        partial_block_id[1] = 8'd11;
        partial_valid = 2'b11;
        repeat (4) begin
            @(posedge clk);
            if (!(core_valid[0] && core_valid[1] &&
                  core_ready[0] && core_ready[1])) begin
                $error("independent core outputs did not transfer concurrently");
                errors++;
            end
            @(negedge clk);
        end
        partial_valid = '0;

        if (errors == 0)
            $display("PASS: H0 star adapter preserved packets and parallel destinations");
        else
            $fatal(1, "FAIL: H0 star adapter produced %0d errors", errors);
        $finish;
    end

    initial begin
        #10000;
        $fatal(1, "FAIL: H0 star adapter timeout");
    end
endmodule
