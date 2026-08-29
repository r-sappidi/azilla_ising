`timescale 1ns/1ps

import ising_pkg::*;

// Deterministic trace oracle for model/azilla_cycle_model/compute.py.
module cycle_model_compute_trace_tb;
    localparam int ROW_W = $clog2(SPIN_COUNT);

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic start = 1'b0;
    logic write_enable = 1'b0;
    logic write_slot = 1'b0;
    logic [ROW_W-1:0] write_row = '0;
    logic [DATA_W-1:0] write_data = '0;
    logic [ROW_W-1:0] read_row;
    logic [DATA_W-1:0] read_data;
    logic [SPIN_COUNT-1:0] state_a = 32'ha5a55a5a;
    logic [SPIN_COUNT-1:0] state_b = 32'h13579bdf;
    logic signed [ACC_W-1:0] result_a [0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] result_b [0:SPIN_COUNT-1];
    logic done;
    int trace_cycle;

    always #5 clk = ~clk;

    function automatic logic [DATA_W-1:0] make_row(input int row);
        logic [DATA_W-1:0] row_word;
        int value;
        row_word = '0;
        for (int column = 0; column < SPIN_COUNT; column++) begin
            value = ((row * 7 + column * 3) % 17) - 8;
            row_word[column*WEIGHT_W +: WEIGHT_W] = WEIGHT_W'(value);
        end
        return row_word;
    endfunction

    j_block_sram #(
        .ROW_COUNT(SPIN_COUNT), .ROW_W(DATA_W)
    ) weights (
        .clk,
        .write_enable_i(write_enable),
        .write_slot_i(write_slot),
        .write_row_i(write_row),
        .write_data_i(write_data),
        .read_slot_i(1'b0),
        .read_row_i(read_row),
        .read_data_o(read_data)
    );

    symmetric_mvm dut (
        .clk, .rst, .start, .state_a, .state_b,
        .weight_row_o(read_row), .weight_data_i(read_data),
        .result_a, .result_b, .done
    );

    initial begin
        repeat (2) @(posedge clk);
        @(negedge clk);
        rst = 1'b0;

        for (int row = 0; row < SPIN_COUNT; row++) begin
            write_enable = 1'b1;
            write_row = ROW_W'(row);
            write_data = make_row(row);
            @(posedge clk);
            @(negedge clk);
        end
        write_enable = 1'b0;

        trace_cycle = 0;
        start = 1'b1;
        @(posedge clk);
        @(negedge clk);
        $display("TRACE %0d %0d %0d", trace_cycle, read_row, done);
        start = 1'b0;

        while (!done) begin
            trace_cycle++;
            @(posedge clk);
            @(negedge clk);
            $display("TRACE %0d %0d %0d", trace_cycle, read_row, done);
        end

        for (int index = 0; index < SPIN_COUNT; index++)
            $display("RESULT %0d %0d %0d", index,
                     $signed(result_a[index]), $signed(result_b[index]));
        $finish;
    end
endmodule
