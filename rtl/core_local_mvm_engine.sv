import ising_pkg::*;

// Destination-stationary, one-sided interaction engine used by the no-CIR
// baseline.  Every command is directed: the weight rows implement J_dst,src,
// source_state_i is x_src, and the only result is accumulated at dst.  No
// arithmetic or partial reduction occurs in the communication hierarchy.
//
// The surrounding core-local scheduler is responsible for issuing every
// directed off-diagonal block owned by the destination core.  A symmetric
// unordered block therefore produces two commands (one at each endpoint),
// with the second command consuming the transposed weight layout.
module core_local_mvm_engine #(
    parameter int GLOBAL_BLOCK_ID_W = 16
) (
    input  logic clk,
    input  logic rst,

    input  logic                         job_valid_i,
    output logic                         job_ready_o,
    input  logic [GLOBAL_BLOCK_ID_W-1:0] source_block_id_i,
    input  logic [SPIN_COUNT-1:0]        source_state_i,
    input  logic                         source_remote_i,

    input  logic                         weight_valid_i,
    output logic                         weight_ready_o,
    input  logic [DATA_W-1:0]            weight_data_i,

    output logic                         result_valid_o,
    input  logic                         result_ready_i,
    output logic signed [ACC_W-1:0]      result_o [0:SPIN_COUNT-1],
    output logic [GLOBAL_BLOCK_ID_W-1:0] result_source_block_id_o,

    output logic [63:0]                  jobs_completed_o,
    output logic [63:0]                  weight_bytes_o,
    output logic [63:0]                  source_state_bytes_o,
    output logic [63:0]                  remote_source_state_bytes_o,
    output logic [63:0]                  first_job_cycle_o,
    output logic [63:0]                  last_job_cycle_o
);
    localparam int WEIGHT_BEATS = SPIN_COUNT * SPIN_COUNT * WEIGHT_W / DATA_W;
    localparam int BEAT_W = (WEIGHT_BEATS > 1) ? $clog2(WEIGHT_BEATS) : 1;

    typedef enum logic [1:0] {ENGINE_IDLE, ENGINE_LOAD, ENGINE_RUN,
                              ENGINE_RESULT} engine_state_t;
    engine_state_t engine_state;
    logic [BEAT_W-1:0] weight_beat;
    logic [SPIN_COUNT-1:0] source_state;
    logic [GLOBAL_BLOCK_ID_W-1:0] source_block_id;
    logic source_remote;
    logic [BEAT_W-1:0] read_row;
    logic [SPIN_COUNT*WEIGHT_W-1:0] read_data;
    logic mvm_start, mvm_done;
    logic [63:0] cycle_count;
    logic first_job_seen;

    assign job_ready_o = engine_state == ENGINE_IDLE;
    assign weight_ready_o = engine_state == ENGINE_LOAD;
    assign result_valid_o = engine_state == ENGINE_RESULT;
    assign result_source_block_id_o = source_block_id;
    assign mvm_start = engine_state == ENGINE_LOAD && weight_valid_i &&
                       weight_ready_o && weight_beat == BEAT_W'(WEIGHT_BEATS-1);

    j_block_sram #(.ROW_COUNT(SPIN_COUNT), .ROW_W(SPIN_COUNT*WEIGHT_W)) weights (
        .clk,
        .write_enable_i(weight_valid_i && weight_ready_o),
        .write_slot_i(1'b0), .write_row_i(weight_beat),
        .write_data_i(weight_data_i),
        .read_slot_i(1'b0), .read_row_i(read_row), .read_data_o(read_data)
    );

    mvm directed_mvm (
        .clk, .rst, .start(mvm_start), .weight_row_o(read_row),
        .weight_data_i(read_data), .result(result_o),
        .state(source_state), .done(mvm_done)
    );

    always_ff @(posedge clk) begin
        if (rst) begin
            engine_state <= ENGINE_IDLE;
            weight_beat <= '0;
            source_state <= '0;
            source_block_id <= '0;
            source_remote <= 1'b0;
            cycle_count <= '0;
            jobs_completed_o <= '0;
            weight_bytes_o <= '0;
            source_state_bytes_o <= '0;
            remote_source_state_bytes_o <= '0;
            first_job_cycle_o <= '0;
            last_job_cycle_o <= '0;
            first_job_seen <= 1'b0;
        end else begin
            cycle_count <= cycle_count + 1'b1;
            unique case (engine_state)
                ENGINE_IDLE: if (job_valid_i && job_ready_o) begin
                    source_state <= source_state_i;
                    source_block_id <= source_block_id_i;
                    source_remote <= source_remote_i;
                    source_state_bytes_o <= source_state_bytes_o + SPIN_COUNT/8;
                    if (source_remote_i)
                        remote_source_state_bytes_o <=
                            remote_source_state_bytes_o + SPIN_COUNT/8;
                    weight_beat <= '0;
                    engine_state <= ENGINE_LOAD;
                    if (!first_job_seen) begin
                        first_job_cycle_o <= cycle_count;
                        first_job_seen <= 1'b1;
                    end
                end
                ENGINE_LOAD: if (weight_valid_i && weight_ready_o) begin
                    weight_bytes_o <= weight_bytes_o + DATA_W/8;
                    if (weight_beat == BEAT_W'(WEIGHT_BEATS-1)) begin
                        weight_beat <= '0;
                        engine_state <= ENGINE_RUN;
                    end else begin
                        weight_beat <= weight_beat + 1'b1;
                    end
                end
                ENGINE_RUN: if (mvm_done)
                    engine_state <= ENGINE_RESULT;
                ENGINE_RESULT: if (result_valid_o && result_ready_i) begin
                    jobs_completed_o <= jobs_completed_o + 1'b1;
                    last_job_cycle_o <= cycle_count;
                    engine_state <= ENGINE_IDLE;
                end
                default: engine_state <= ENGINE_IDLE;
            endcase
        end
    end
endmodule
