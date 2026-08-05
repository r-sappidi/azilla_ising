import ising_pkg::*;

// H0-local star adapter.
//
// Routes the independent partial stream from each hierarchy-node MVM engine
// to one of the H0 tile's spin cores. Each core is an independent output bank,
// so transfers to different cores proceed concurrently. A fixed-priority
// arbiter resolves collisions at each core and locks for the complete packet.
module h0_star_adapter #(
    parameter int CORE_COUNT        = 32,
    parameter int MVM_COUNT         = 16,
    parameter int GLOBAL_BLOCK_ID_W = 16,
    parameter int BASE_BLOCK_ID     = 0
) (
    input logic clk,
    input logic rst,

    input  logic [MVM_COUNT-1:0]                         partial_valid_i,
    output logic [MVM_COUNT-1:0]                         partial_ready_o,
    input  logic signed [MVM_COUNT-1:0][DATA_W-1:0]      partial_data_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]  partial_block_id_i,
    input  logic [MVM_COUNT-1:0]                         partial_last_i,

    output logic [CORE_COUNT-1:0]                        core_partial_valid_o,
    input  logic [CORE_COUNT-1:0]                        core_partial_ready_i,
    output logic signed [CORE_COUNT-1:0][DATA_W-1:0]     core_partial_data_o
);
    localparam int ENGINE_ID_W = (MVM_COUNT > 1) ? $clog2(MVM_COUNT) : 1;

    logic [CORE_COUNT-1:0] output_locked;
    logic [CORE_COUNT-1:0][ENGINE_ID_W-1:0] locked_engine;
    logic [CORE_COUNT-1:0][ENGINE_ID_W-1:0] selected_engine;
    logic [CORE_COUNT-1:0] selected_valid;
    logic [CORE_COUNT-1:0] selected_last;

    always_comb begin
        partial_ready_o = '0;
        core_partial_valid_o = '0;
        core_partial_data_o = '0;
        selected_engine = '0;
        selected_valid = '0;
        selected_last = '0;

        for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
            logic found_engine;
            found_engine = 1'b0;

            if (output_locked[core_index]) begin
                selected_engine[core_index] = locked_engine[core_index];
                selected_valid[core_index] =
                    partial_valid_i[locked_engine[core_index]];
                selected_last[core_index] =
                    partial_last_i[locked_engine[core_index]];
                core_partial_valid_o[core_index] = selected_valid[core_index];
                core_partial_data_o[core_index] =
                    partial_data_i[locked_engine[core_index]];
                partial_ready_o[locked_engine[core_index]] =
                    core_partial_ready_i[core_index];
            end
            else begin
                for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
                    if (!found_engine && partial_valid_i[engine_index] &&
                        partial_block_id_i[engine_index] ==
                            GLOBAL_BLOCK_ID_W'(BASE_BLOCK_ID + core_index)) begin
                        selected_engine[core_index] = ENGINE_ID_W'(engine_index);
                        selected_valid[core_index] = 1'b1;
                        selected_last[core_index] = partial_last_i[engine_index];
                        core_partial_valid_o[core_index] = 1'b1;
                        core_partial_data_o[core_index] = partial_data_i[engine_index];
                        partial_ready_o[engine_index] = core_partial_ready_i[core_index];
                        found_engine = 1'b1;
                    end
                end
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            output_locked <= '0;
            locked_engine <= '0;
        end
        else begin
            for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
                if (!output_locked[core_index] &&
                    selected_valid[core_index] &&
                    core_partial_ready_i[core_index] &&
                    !selected_last[core_index]) begin
                    output_locked[core_index] <= 1'b1;
                    locked_engine[core_index] <= selected_engine[core_index];
                end
                else if (output_locked[core_index] &&
                         selected_valid[core_index] &&
                         core_partial_ready_i[core_index] &&
                         selected_last[core_index]) begin
                    output_locked[core_index] <= 1'b0;
                end
            end
        end
    end
endmodule
