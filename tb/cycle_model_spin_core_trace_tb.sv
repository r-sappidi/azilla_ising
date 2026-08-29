`timescale 1ns/1ps

import ising_pkg::*;

// Deterministic trace oracle for the integrated Python SpinCore model.
module cycle_model_spin_core_trace_tb;
    logic clk = 1'b0;
    logic rst = 1'b1;
    logic init_start = 1'b0;
    logic init_done;
    logic weight_init_valid = 1'b0;
    logic weight_init_ready;
    logic [DATA_W-1:0] weight_init_data = '0;
    logic iter_start = 1'b0;
    logic partials_done = 1'b0;
    logic commit = 1'b0;
    logic iter_done;
    logic h0_partial_valid = 1'b0;
    logic h0_partial_ready;
    logic signed [DATA_W-1:0] h0_partial_data = '0;
    logic ext_partial_valid = 1'b0;
    logic ext_partial_ready;
    logic signed [DATA_W-1:0] ext_partial_data = '0;
    logic [SPIN_COUNT-1:0] state_next;
    logic [SPIN_COUNT-1:0] state_current;
    int trace_cycle = 0;

    always #5 clk = ~clk;

    function automatic logic [DATA_W-1:0] make_row(input int row);
        logic [DATA_W-1:0] row_word;
        int value;
        row_word = '0;
        for (int column = 0; column < SPIN_COUNT; column++) begin
            value = ((row * 5 + column * 11) % 19) - 9;
            row_word[column*WEIGHT_W +: WEIGHT_W] = WEIGHT_W'(value);
        end
        return row_word;
    endfunction

    function automatic logic [DATA_W-1:0] make_partial(
        input int beat, input int scale
    );
        logic [DATA_W-1:0] word;
        int value;
        word = '0;
        for (int lane = 0; lane < DATA_W/ACC_W; lane++) begin
            value = scale * (beat * (DATA_W/ACC_W) + lane - 16);
            word[lane*ACC_W +: ACC_W] = ACC_W'(value);
        end
        return word;
    endfunction

    spin_core dut (
        .clk, .rst, .init_start, .init_done,
        .weight_init_valid, .weight_init_ready, .weight_init_data,
        .noise_seed(32'h12345678),
        .coeff_a(16'sd3), .coeff_b(-16'sd2), .coeff_c(16'sd0),
        .noise_amplitude(16'sd5), .init_state(32'ha55aa55a),
        .iter_start, .partials_done, .commit, .noise_decay('0),
        .iter_done, .done(1'b0),
        .h0_partial_valid, .h0_partial_ready, .h0_partial_data,
        .ext_partial_valid, .ext_partial_ready, .ext_partial_data,
        .state_next, .state_current
    );

    task automatic step_and_trace;
        @(posedge clk);
        @(negedge clk);
        $display("CORE %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
                 trace_cycle, dut.core_state, init_done, weight_init_ready,
                 h0_partial_ready, ext_partial_ready, iter_done,
                 state_current, state_next, dut.local_compute_done,
                 dut.h0_partial_beat_count, dut.ext_partial_beat_count);
        trace_cycle++;
    endtask

    initial begin
        step_and_trace();
        step_and_trace();
        rst = 1'b0;

        init_start = 1'b1;
        step_and_trace();
        init_start = 1'b0;

        for (int row = 0; row < SPIN_COUNT; row++) begin
            weight_init_valid = 1'b1;
            weight_init_data = make_row(row);
            step_and_trace();
        end
        weight_init_valid = 1'b0;
        step_and_trace();

        iter_start = 1'b1;
        step_and_trace();
        iter_start = 1'b0;

        for (int beat = 0; beat < SPIN_COUNT*ACC_W/DATA_W; beat++) begin
            h0_partial_valid = 1'b1;
            ext_partial_valid = 1'b1;
            h0_partial_data = make_partial(beat, 1);
            ext_partial_data = make_partial(beat, -2);
            step_and_trace();
        end
        h0_partial_valid = 1'b0;
        ext_partial_valid = 1'b0;
        partials_done = 1'b1;
        step_and_trace();
        partials_done = 1'b0;

        while (!iter_done)
            step_and_trace();

        for (int spin = 0; spin < SPIN_COUNT; spin++)
            $display("TOTAL %0d %0d", spin,
                     $signed(dut.accumulator_total[spin]));

        commit = 1'b1;
        step_and_trace();
        commit = 1'b0;
        step_and_trace();
        $finish;
    end
endmodule
