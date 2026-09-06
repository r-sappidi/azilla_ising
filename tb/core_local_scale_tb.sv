`timescale 1ns/1ps
import ising_pkg::*;

// Capacity-scaling check for the destination-stationary cores-only datapath.
// Every 32-spin core concurrently evaluates one directed interaction from its
// successor block. This validates replicated RTL arithmetic, global IDs,
// handshakes, and accounting without claiming full-mesh/DRAM timing coverage.
module core_local_scale_tb #(
    parameter int TOTAL_CORES = 512
);
    localparam int BLOCK_ID_W = (TOTAL_CORES > 1) ? $clog2(TOTAL_CORES) : 1;

    logic clk = 0;
    logic rst = 1;
    logic [TOTAL_CORES-1:0] job_valid, job_ready;
    logic [TOTAL_CORES-1:0][BLOCK_ID_W-1:0] source_id;
    logic [TOTAL_CORES-1:0][SPIN_COUNT-1:0] source_state;
    logic [TOTAL_CORES-1:0] source_remote;
    logic [TOTAL_CORES-1:0] weight_valid, weight_ready;
    logic [TOTAL_CORES-1:0][DATA_W-1:0] weight_data;
    logic [TOTAL_CORES-1:0] result_valid, result_ready;
    logic signed [ACC_W-1:0] result [0:TOTAL_CORES-1][0:SPIN_COUNT-1];
    logic [TOTAL_CORES-1:0][BLOCK_ID_W-1:0] result_source_id;
    logic [63:0] jobs [0:TOTAL_CORES-1];
    logic [63:0] weight_bytes [0:TOTAL_CORES-1];
    logic [63:0] state_bytes [0:TOTAL_CORES-1];
    logic [63:0] remote_bytes [0:TOTAL_CORES-1];
    logic [63:0] first_cycle [0:TOTAL_CORES-1];
    logic [63:0] last_cycle [0:TOTAL_CORES-1];
    int errors = 0;

    always #5 clk = ~clk;

    function automatic logic signed [WEIGHT_W-1:0] weight(
        input int dst, input int src, input int row, input int col);
        int value;
        value = ((dst * 3 + src * 5 + row * 7 + col * 11) % 15) - 7;
        return WEIGHT_W'(value);
    endfunction

    function automatic logic state_bit(input int block, input int bit_index);
        return ((block * 7 + bit_index * 3) % 5) < 2;
    endfunction

    function automatic logic signed [ACC_W-1:0] expected_result(
        input int dst, input int row);
        logic signed [ACC_W-1:0] accumulator;
        int src;
        accumulator = '0;
        src = (dst + 1) % TOTAL_CORES;
        for (int col = 0; col < SPIN_COUNT; col++)
            accumulator += state_bit(src, col)
                ? weight(dst, src, row, col)
                : -weight(dst, src, row, col);
        return accumulator;
    endfunction

    generate
        for (genvar core = 0; core < TOTAL_CORES; core++) begin : gen_engines
            core_local_mvm_engine #(.GLOBAL_BLOCK_ID_W(BLOCK_ID_W)) dut (
                .clk, .rst,
                .job_valid_i(job_valid[core]), .job_ready_o(job_ready[core]),
                .source_block_id_i(source_id[core]),
                .source_state_i(source_state[core]),
                .source_remote_i(source_remote[core]),
                .weight_valid_i(weight_valid[core]),
                .weight_ready_o(weight_ready[core]),
                .weight_data_i(weight_data[core]),
                .result_valid_o(result_valid[core]),
                .result_ready_i(result_ready[core]),
                .result_o(result[core]),
                .result_source_block_id_o(result_source_id[core]),
                .jobs_completed_o(jobs[core]),
                .weight_bytes_o(weight_bytes[core]),
                .source_state_bytes_o(state_bytes[core]),
                .remote_source_state_bytes_o(remote_bytes[core]),
                .first_job_cycle_o(first_cycle[core]),
                .last_job_cycle_o(last_cycle[core])
            );
        end
    endgenerate

    initial begin
        job_valid = '0;
        weight_valid = '0;
        result_ready = '0;
        source_remote = '1;
        source_id = '0;
        source_state = '0;
        weight_data = '0;
        repeat (3) @(posedge clk);
        @(negedge clk);
        rst = 0;

        for (int dst = 0; dst < TOTAL_CORES; dst++) begin
            source_id[dst] = BLOCK_ID_W'((dst + 1) % TOTAL_CORES);
            for (int bit_index = 0; bit_index < SPIN_COUNT; bit_index++)
                source_state[dst][bit_index] =
                    state_bit((dst + 1) % TOTAL_CORES, bit_index);
        end
        job_valid = '1;
        do @(posedge clk); while (job_ready != {TOTAL_CORES{1'b1}});
        @(negedge clk);
        job_valid = '0;

        for (int row = 0; row < SPIN_COUNT; row++) begin
            for (int dst = 0; dst < TOTAL_CORES; dst++) begin
                for (int col = 0; col < SPIN_COUNT; col++)
                    weight_data[dst][col*WEIGHT_W +: WEIGHT_W] =
                        weight(dst, (dst + 1) % TOTAL_CORES, row, col);
            end
            weight_valid = '1;
            do @(posedge clk); while (weight_ready != {TOTAL_CORES{1'b1}});
            @(negedge clk);
        end
        weight_valid = '0;

        do @(posedge clk); while (result_valid != {TOTAL_CORES{1'b1}});
        @(negedge clk);
        for (int dst = 0; dst < TOTAL_CORES; dst++) begin
            if (result_source_id[dst] !=
                BLOCK_ID_W'((dst + 1) % TOTAL_CORES)) begin
                $error("source ID mismatch dst=%0d expected=%0d got=%0d",
                       dst, (dst + 1) % TOTAL_CORES, result_source_id[dst]);
                errors++;
            end
            for (int row = 0; row < SPIN_COUNT; row++) begin
                if (result[dst][row] !== expected_result(dst, row)) begin
                    $error("arithmetic mismatch dst=%0d row=%0d expected=%0d got=%0d",
                           dst, row, expected_result(dst, row),
                           result[dst][row]);
                    errors++;
                end
            end
        end
        result_ready = '1;
        @(posedge clk);
        @(negedge clk);
        result_ready = '0;
        for (int dst = 0; dst < TOTAL_CORES; dst++) begin
            if (jobs[dst] != 1 || weight_bytes[dst] != 1024 ||
                state_bytes[dst] != 4 || remote_bytes[dst] != 4) begin
                $error("counter mismatch dst=%0d jobs=%0d weights=%0d state=%0d remote=%0d",
                       dst, jobs[dst], weight_bytes[dst], state_bytes[dst],
                       remote_bytes[dst]);
                errors++;
            end
        end
        if (errors)
            $fatal(1, "cores-only scale check failed errors=%0d", errors);
        $display("PASS: cores-only replicated RTL capacity=%0d spins cores=%0d completion_cycle=%0d",
                 TOTAL_CORES * SPIN_COUNT, TOTAL_CORES, last_cycle[0]);
        $finish;
    end
endmodule
