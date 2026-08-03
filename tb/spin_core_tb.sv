`timescale 1ns/1ps

import ising_pkg::*;

module spin_core_tb;
    localparam int WEIGHT_BEATS  = SPIN_COUNT * SPIN_COUNT * WEIGHT_W / DATA_W;
    localparam int PARTIAL_BEATS = SPIN_COUNT * ACC_W / DATA_W;
    localparam int LANES_PER_BEAT = DATA_W / ACC_W;

    logic clk = 1'b0;
    logic rst = 1'b1;

    logic init_start;
    logic init_done;
    logic weight_init_valid;
    logic weight_init_ready;
    logic [DATA_W-1:0] weight_init_data;

    logic [31:0] noise_seed;
    logic signed [COEFF_W-1:0] coeff_a;
    logic signed [COEFF_W-1:0] coeff_b;
    logic signed [COEFF_W-1:0] coeff_c;
    logic signed [COEFF_W-1:0] noise_amplitude;
    logic [SPIN_COUNT-1:0] init_state;

    logic iter_start;
    logic partials_done;
    logic commit;
    logic [16:0] noise_decay;
    logic iter_done;
    logic done;

    logic h0_partial_valid;
    logic h0_partial_ready;
    logic signed [DATA_W-1:0] h0_partial_data;
    logic ext_partial_valid;
    logic ext_partial_ready;
    logic signed [DATA_W-1:0] ext_partial_data;

    logic [SPIN_COUNT-1:0] state_next;
    logic [SPIN_COUNT-1:0] state_current;

    integer signed weight [0:SPIN_COUNT-1][0:SPIN_COUNT-1];
    integer signed h0_partial [0:SPIN_COUNT-1];
    integer signed ext_partial [0:SPIN_COUNT-1];
    integer signed expected_total [0:SPIN_COUNT-1];
    logic [SPIN_COUNT-1:0] expected_state;
    logic [31:0] golden_lfsr;
    int error_count;

    spin_core dut (
        .clk,
        .rst,
        .init_start,
        .init_done,
        .weight_init_valid,
        .weight_init_ready,
        .weight_init_data,
        .noise_seed,
        .coeff_a,
        .coeff_b,
        .coeff_c,
        .noise_amplitude,
        .init_state,
        .iter_start,
        .partials_done,
        .commit,
        .noise_decay,
        .iter_done,
        .done,
        .h0_partial_valid,
        .h0_partial_ready,
        .h0_partial_data,
        .ext_partial_valid,
        .ext_partial_ready,
        .ext_partial_data,
        .state_next,
        .state_current
    );

    always #5 clk = ~clk;

    function automatic logic [31:0] lfsr_step(input logic [31:0] value);
        logic feedback;
        begin
            feedback = value[31] ^ value[21] ^ value[1] ^ value[0];
            lfsr_step = {value[30:0], feedback};
        end
    endfunction

    task automatic pulse_init;
        begin
            @(negedge clk);
            init_start = 1'b1;
            @(negedge clk);
            init_start = 1'b0;
        end
    endtask

    task automatic load_weights;
        logic [DATA_W-1:0] beat;
        begin
            for (int row = 0; row < WEIGHT_BEATS; row++) begin
                beat = '0;
                for (int col = 0; col < SPIN_COUNT; col++)
                    beat[col*WEIGHT_W +: WEIGHT_W] = weight[row][col][WEIGHT_W-1:0];

                @(negedge clk);
                weight_init_data  = beat;
                weight_init_valid = 1'b1;
                while (!weight_init_ready)
                    @(negedge clk);
                @(negedge clk);
                weight_init_valid = 1'b0;
            end
        end
    endtask

    task automatic pulse_iter_start;
        begin
            @(negedge clk);
            iter_start = 1'b1;
            @(negedge clk);
            iter_start = 1'b0;
        end
    endtask

    task automatic send_h0_packet;
        logic [DATA_W-1:0] beat;
        begin
            for (int beat_idx = 0; beat_idx < PARTIAL_BEATS; beat_idx++) begin
                beat = '0;
                for (int lane = 0; lane < LANES_PER_BEAT; lane++)
                    beat[lane*ACC_W +: ACC_W] =
                        h0_partial[beat_idx*LANES_PER_BEAT + lane][ACC_W-1:0];

                @(negedge clk);
                h0_partial_data  = beat;
                h0_partial_valid = 1'b1;
                while (!h0_partial_ready)
                    @(negedge clk);
                @(negedge clk);
                h0_partial_valid = 1'b0;
            end
        end
    endtask

    task automatic send_ext_packet;
        logic [DATA_W-1:0] beat;
        begin
            for (int beat_idx = 0; beat_idx < PARTIAL_BEATS; beat_idx++) begin
                beat = '0;
                for (int lane = 0; lane < LANES_PER_BEAT; lane++)
                    beat[lane*ACC_W +: ACC_W] =
                        ext_partial[beat_idx*LANES_PER_BEAT + lane][ACC_W-1:0];

                @(negedge clk);
                ext_partial_data  = beat;
                ext_partial_valid = 1'b1;
                while (!ext_partial_ready)
                    @(negedge clk);
                @(negedge clk);
                ext_partial_valid = 1'b0;
            end
        end
    endtask

    task automatic pulse_partials_done;
        begin
            @(negedge clk);
            partials_done = 1'b1;
            @(negedge clk);
            partials_done = 1'b0;
        end
    endtask

    task automatic pulse_commit(input logic final_iteration);
        begin
            @(negedge clk);
            done   = final_iteration;
            commit = 1'b1;
            @(negedge clk);
            commit = 1'b0;
            done   = 1'b0;
        end
    endtask

    task automatic calculate_expected;
        integer signed local_sum;
        integer signed a_term;
        integer signed noise_term;
        begin
            golden_lfsr = lfsr_step(golden_lfsr);
            for (int row = 0; row < SPIN_COUNT; row++) begin
                local_sum = 0;
                for (int col = 0; col < SPIN_COUNT; col++) begin
                    if (state_current[col])
                        local_sum += weight[row][col];
                    else
                        local_sum -= weight[row][col];
                end

                a_term = state_current[row] ? $signed(coeff_a) : -$signed(coeff_a);
                noise_term = golden_lfsr[row]
                    ? $signed(noise_amplitude)
                    : -$signed(noise_amplitude);

                expected_total[row] =
                    a_term +
                    $signed(coeff_b) *
                    (local_sum + h0_partial[row] + ext_partial[row]) +
                    noise_term;
                expected_state[row] = expected_total[row] >= 0;
            end
        end
    endtask

    task automatic check_iteration(input int iteration);
        begin
            wait (iter_done);
            #1;
            for (int i = 0; i < SPIN_COUNT; i++) begin
                if ($signed(dut.accumulator_total[i]) !== expected_total[i]) begin
                    $error("iteration %0d spin %0d total: got %0d expected %0d",
                           iteration, i, $signed(dut.accumulator_total[i]),
                           expected_total[i]);
                    error_count++;
                end
            end
            if (state_next !== expected_state) begin
                $error("iteration %0d state_next: got %h expected %h",
                       iteration, state_next, expected_state);
                error_count++;
            end
        end
    endtask

    initial begin
        init_start         = 1'b0;
        weight_init_valid = 1'b0;
        weight_init_data  = '0;
        noise_seed         = 32'h1ace_b00c;
        coeff_a            = 16'sd3;
        coeff_b            = -16'sd2;
        coeff_c            = '0;
        noise_amplitude    = 16'sd5;
        init_state         = 32'ha596_3cc3;
        iter_start         = 1'b0;
        partials_done      = 1'b0;
        commit             = 1'b0;
        noise_decay        = '0;
        done               = 1'b0;
        h0_partial_valid   = 1'b0;
        h0_partial_data    = '0;
        ext_partial_valid  = 1'b0;
        ext_partial_data   = '0;
        error_count        = 0;

        for (int row = 0; row < SPIN_COUNT; row++) begin
            h0_partial[row] = (row % 9) - 4;
            ext_partial[row] = 6 - (row % 11);
            for (int col = 0; col < SPIN_COUNT; col++) begin
                if (row == col)
                    weight[row][col] = 0;
                else
                    weight[row][col] = ((row * 11 + col * 7 + 3) % 17) - 8;
            end
        end

        repeat (4) @(negedge clk);
        rst = 1'b0;

        fork
            pulse_init();
            begin
                wait (weight_init_ready);
                load_weights();
            end
        join

        wait (init_done);
        if (state_current !== init_state) begin
            $error("initial state: got %h expected %h", state_current, init_state);
            error_count++;
        end
        golden_lfsr = noise_seed;

        pulse_iter_start();
        calculate_expected();
        fork
            send_h0_packet();
            send_ext_packet();
        join
        pulse_partials_done();
        check_iteration(0);
        pulse_commit(1'b0);
        repeat (2) @(negedge clk);
        if (state_current !== expected_state) begin
            $error("committed state after iteration 0: got %h expected %h",
                   state_current, expected_state);
            error_count++;
        end

        for (int i = 0; i < SPIN_COUNT; i++) begin
            h0_partial[i] = 3 - (i % 7);
            ext_partial[i] = (i % 5) - 2;
        end
        noise_amplitude = 16'sd2;

        pulse_iter_start();
        calculate_expected();
        fork
            send_h0_packet();
            send_ext_packet();
        join
        pulse_partials_done();
        check_iteration(1);
        pulse_commit(1'b0);
        repeat (2) @(negedge clk);
        if (state_current !== expected_state) begin
            $error("committed state after iteration 1: got %h expected %h",
                   state_current, expected_state);
            error_count++;
        end

        for (int iteration = 2; iteration < 10; iteration++) begin
            for (int i = 0; i < SPIN_COUNT; i++) begin
                h0_partial[i] = ((iteration * 5 + i * 3) % 19) - 9;
                ext_partial[i] = ((iteration * 7 + i * 2) % 23) - 11;
            end
            noise_amplitude = iteration + 1;

            pulse_iter_start();
            calculate_expected();
            fork
                send_h0_packet();
                send_ext_packet();
            join
            pulse_partials_done();
            check_iteration(iteration);
            pulse_commit(iteration == 9);
            repeat (2) @(negedge clk);
            if (state_current !== expected_state) begin
                $error("committed state after iteration %0d: got %h expected %h",
                       iteration, state_current, expected_state);
                error_count++;
            end
        end

        if (error_count == 0)
            $display("PASS: spin_core matched the golden model for ten iterations");
        else
            $fatal(1, "FAIL: spin_core produced %0d errors", error_count);

        $finish;
    end

    initial begin
        #100000;
        $fatal(1, "FAIL: simulation timeout");
    end
endmodule
