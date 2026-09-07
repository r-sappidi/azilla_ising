import ising_pkg::*;

// One 32-spin endpoint and its resident diagonal interaction block.
//
// The local MVM evaluates J_ii*x_i. Partial streams from the H0 node and all
// higher levels are accumulated independently, then combined only after the
// controller marks the iteration's partial traffic complete. state_current is
// frozen for the entire iteration; commit is the only operation that replaces
// it with state_next.
module spin_core #(
    parameter bit CORES_ONLY = 1'b0,
    parameter int GLOBAL_BLOCK_ID_W = 16
) (
    input  logic                         clk,
    input  logic                         rst,

    // Initialization of resident state, coefficients, and diagonal J block.
    input  logic                         init_start,
    output logic                         init_done,
    input  logic                         weight_init_valid,
    output logic                         weight_init_ready,
    input  logic [DATA_W-1:0]            weight_init_data,

    // Static problem configuration.
    input  logic [31:0]                  noise_seed,
    input  logic signed [COEFF_W-1:0]    coeff_a,
    input  logic signed [COEFF_W-1:0]    coeff_b,
    input  logic signed [COEFF_W-1:0]    coeff_c,
    input  logic signed [COEFF_W-1:0]    noise_amplitude,
    input  logic [SPIN_COUNT-1:0]        init_state,

    // Iteration control.
    input  logic                         iter_start,
    input  logic                         partials_done,
    input  logic                         commit,
    input  logic [16:0]                  noise_decay,
    output logic                         iter_done,
    input  logic                         done,

    // Cross-core partials produced within H0.
    input  logic                         h0_partial_valid,
    output logic                         h0_partial_ready,
    input  logic signed [DATA_W-1:0]     h0_partial_data,

    // Partials produced by H1/H2.
    input  logic                         ext_partial_valid,
    output logic                         ext_partial_ready,
    input  logic signed [DATA_W-1:0]     ext_partial_data,

    // Optional destination-local interaction path. CORES_ONLY shares the
    // resident MVM with diagonal work; no second MVM is instantiated.
    input  logic                         core_job_valid,
    output logic                         core_job_ready,
    input  logic [GLOBAL_BLOCK_ID_W-1:0] core_job_source_block_id,
    input  logic                         core_job_transpose,
    output logic                         core_state_req_valid,
    input  logic                         core_state_req_ready,
    output logic [GLOBAL_BLOCK_ID_W-1:0] core_state_req_block_id,
    input  logic                         core_state_rsp_valid,
    input  logic [SPIN_COUNT-1:0]        core_state_rsp_data,
    input  logic                         core_weight_valid,
    output logic                         core_weight_ready,
    input  logic [DATA_W-1:0]            core_weight_data,
    output logic                         core_job_done,

    // Frozen state for the current iteration.
    output logic [SPIN_COUNT-1:0]        state_next,
    output logic [SPIN_COUNT-1:0]        state_current
);

    localparam int WEIGHT_BLOCK_BITS = SPIN_COUNT * SPIN_COUNT * WEIGHT_W;
    localparam int WEIGHT_BEATS      = WEIGHT_BLOCK_BITS / DATA_W;
    localparam int WEIGHT_BEAT_W     = (WEIGHT_BEATS > 1) ? $clog2(WEIGHT_BEATS) : 1;
    localparam int PARTIAL_BITS      = SPIN_COUNT * ACC_W;
    localparam int PARTIAL_BEATS     = PARTIAL_BITS / DATA_W;
    localparam int PARTIAL_BEAT_W    =
        (PARTIAL_BEATS > 1) ? $clog2(PARTIAL_BEATS) : 1;
    localparam int PARTIAL_LANES     = DATA_W / ACC_W;

    typedef enum logic [2:0] {
        CORE_RESET,
        CORE_INIT,
        CORE_IDLE,
        CORE_ACCUMULATE,
        CORE_FINALIZE,
        CORE_WAIT_COMMIT,
        CORE_COMMIT,
        CORE_DONE
    } core_state_t;

    core_state_t core_state;
    core_state_t core_state_n;

    // Keep contributions separate until finalization. This mirrors hierarchy
    // ownership and makes accepted partial traffic independently observable.
    logic signed [ACC_W-1:0] accumulator_local [0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] accumulator_h0    [0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] accumulator_ext   [0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] accumulator_total [0:SPIN_COUNT-1];
    logic signed [COEFF_W-1:0] coeff_a_reg;
    logic signed [COEFF_W-1:0] coeff_b_reg;

    logic [31:0] lfsr_state;

    logic [WEIGHT_BEAT_W:0] weight_beat_count;
    logic [WEIGHT_BEAT_W-1:0] local_weight_read_row;
    logic [WEIGHT_W*SPIN_COUNT-1:0] local_weight_read_data;
    logic local_weight_write_enable;

    logic [PARTIAL_BEAT_W-1:0] h0_partial_beat_count;
    logic [PARTIAL_BEAT_W-1:0] ext_partial_beat_count;
    logic                      partials_done_pending;
    logic                      local_compute_done;
    logic                      done_latched;
    logic                      feedback;
    typedef enum logic [2:0] {
        JOB_IDLE, JOB_STATE_REQ, JOB_STATE_WAIT, JOB_LOAD, JOB_RUN, JOB_RETIRE
    } job_state_t;
    job_state_t job_state;
    logic [GLOBAL_BLOCK_ID_W-1:0] job_source;
    logic [SPIN_COUNT-1:0] job_source_state;
    logic job_transpose;
    logic [WEIGHT_BEAT_W-1:0] job_weight_beat;
    logic diagonal_done;
    logic signed [ACC_W-1:0] diagonal_result [0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] mvm_result [0:SPIN_COUNT-1];
    logic mvm_done;
    logic mvm_start;
    logic mvm_offdiagonal;
    logic [SPIN_COUNT-1:0] mvm_state;

    assign core_job_ready = CORES_ONLY && (core_state == CORE_ACCUMULATE) &&
                            diagonal_done && (job_state == JOB_IDLE) &&
                            !partials_done_pending;
    assign core_state_req_valid = CORES_ONLY && (job_state == JOB_STATE_REQ);
    assign core_state_req_block_id = job_source;
    assign core_weight_ready = CORES_ONLY && (job_state == JOB_LOAD);
    assign core_job_done = CORES_ONLY && (job_state == JOB_RETIRE);
    assign mvm_offdiagonal = CORES_ONLY &&
                            ((job_state == JOB_LOAD) || (job_state == JOB_RUN) ||
                             (job_state == JOB_RETIRE));
    assign mvm_start = (iter_start && (core_state == CORE_IDLE)) ||
                      (CORES_ONLY && core_weight_valid && core_weight_ready &&
                       job_weight_beat == WEIGHT_BEAT_W'(WEIGHT_BEATS-1));
    assign mvm_state = mvm_offdiagonal ? job_source_state : state_current;
    assign local_compute_done = CORES_ONLY ? diagonal_done : mvm_done;
    always_comb begin
        for (int spin=0; spin<SPIN_COUNT; spin++)
            accumulator_local[spin] = CORES_ONLY ? diagonal_result[spin] : mvm_result[spin];
    end

    assign weight_init_ready = (core_state == CORE_INIT) &&
                               (weight_beat_count < WEIGHT_BEATS);
    assign h0_partial_ready = !CORES_ONLY && (core_state == CORE_ACCUMULATE);
    assign ext_partial_ready = !CORES_ONLY && (core_state == CORE_ACCUMULATE);
    assign iter_done = (core_state == CORE_WAIT_COMMIT);
    assign local_weight_write_enable = weight_init_valid && weight_init_ready;

    // Non-negative fields map to stored spin bit 1 (+1).
    always_comb begin
        for (int spin = 0; spin < SPIN_COUNT; spin++)
            state_next[spin] = ~accumulator_total[spin][ACC_W-1];
    end

    // x^32 + x^22 + x^2 + x + 1 Fibonacci LFSR feedback.
    assign feedback = lfsr_state[31] ^
                      lfsr_state[21] ^
                      lfsr_state[1]  ^
                      lfsr_state[0];

    // The core's diagonal 32x32 block is resident in SRAM slot zero.
    j_block_sram #(
        .ROW_COUNT(SPIN_COUNT),
        .ROW_W(SPIN_COUNT*WEIGHT_W)
    ) local_weight_sram (
        .clk,
        .write_enable_i(local_weight_write_enable || (core_weight_valid && core_weight_ready)),
        .write_slot_i(CORES_ONLY && core_weight_valid && core_weight_ready),
        .write_row_i(core_weight_ready ? job_weight_beat : weight_beat_count[WEIGHT_BEAT_W-1:0]),
        .write_data_i(core_weight_ready ? core_weight_data : weight_init_data),
        .read_slot_i(mvm_offdiagonal),
        .read_row_i(local_weight_read_row),
        .read_data_o(local_weight_read_data)
    );

    // The local MVM starts with the iteration and runs independently of
    // incoming hierarchy partials.
    mvm #(.SUPPORT_TRANSPOSE(CORES_ONLY)) mvm_local (
        .clk,
        .rst,
        .start(mvm_start),
        .weight_row_o(local_weight_read_row),
        .weight_data_i(local_weight_read_data),
        .result(mvm_result),
        .state(mvm_state),
        .transpose_i(mvm_offdiagonal && job_transpose),
        .done(mvm_done)
    );

    // ------------------------------------------------------------------
    // Datapath and lifecycle registers
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            core_state             <= CORE_RESET;
            state_current          <= '0;
            coeff_a_reg            <= '0;
            coeff_b_reg            <= '0;
            weight_beat_count      <= '0;
            h0_partial_beat_count  <= '0;
            ext_partial_beat_count <= '0;
            partials_done_pending  <= 1'b0;
            init_done              <= 1'b0;

            accumulator_h0  <= '{default: '0};
            accumulator_ext <= '{default: '0};
            done_latched    <= 1'b0;
            lfsr_state      <= '0;
            job_state <= JOB_IDLE;
            job_source <= '0;
            job_source_state <= '0;
            job_transpose <= 1'b0;
            job_weight_beat <= '0;
            diagonal_done <= 1'b0;
            diagonal_result <= '{default:'0};
        end
        else begin
            done_latched <= done ? 1'b1 : done_latched;
            core_state <= core_state_n;

            // Advance noise once per iteration. A completion pulse that
            // arrives early is retained until the local MVM has also drained.
            if (iter_start) begin
                lfsr_state <= {lfsr_state[30:0], feedback};
                partials_done_pending <= 1'b0;
            end
            else if (partials_done)
                partials_done_pending <= 1'b1;

            if (init_start) begin
                state_current <= init_state;
                coeff_a_reg <= coeff_a;
                coeff_b_reg <= coeff_b;
                lfsr_state <= noise_seed;
            end

            // Coefficients may be annealed by the controller. Sample them at
            // the iteration boundary so they remain stable while partials are
            // accumulated and the final field is evaluated.
            if ((core_state == CORE_IDLE) && iter_start) begin
                coeff_a_reg <= coeff_a;
                coeff_b_reg <= coeff_b;
            end

            if ((core_state == CORE_INIT) &&
                weight_init_valid && weight_init_ready) begin
                weight_beat_count <= weight_beat_count + 1'b1;
            end

            if (core_state_n == CORE_IDLE) begin
                init_done <= 1'b1;
            end

            if ((core_state == CORE_IDLE) && iter_start) begin
                accumulator_h0  <= '{default: '0};
                accumulator_ext <= '{default: '0};
                h0_partial_beat_count <= '0;
                ext_partial_beat_count <= '0;
                partials_done_pending <= 1'b0;
                diagonal_done <= 1'b0;
                job_state <= JOB_IDLE;
            end

            if (CORES_ONLY && core_state == CORE_ACCUMULATE) begin
                if (!diagonal_done && mvm_done) begin
                    diagonal_result <= mvm_result;
                    diagonal_done <= 1'b1;
                end
                unique case (job_state)
                    JOB_IDLE: if (core_job_valid && core_job_ready) begin
                        job_source <= core_job_source_block_id;
                        job_transpose <= core_job_transpose;
                        job_state <= JOB_STATE_REQ;
                    end
                    JOB_STATE_REQ: if (core_state_req_valid && core_state_req_ready)
                        job_state <= JOB_STATE_WAIT;
                    JOB_STATE_WAIT: if (core_state_rsp_valid) begin
                        job_source_state <= core_state_rsp_data;
                        job_weight_beat <= '0;
                        job_state <= JOB_LOAD;
                    end
                    JOB_LOAD: if (core_weight_valid && core_weight_ready) begin
                        if (job_weight_beat == WEIGHT_BEAT_W'(WEIGHT_BEATS-1)) begin
                            job_weight_beat <= '0;
                            job_state <= JOB_RUN;
                        end else job_weight_beat <= job_weight_beat + 1'b1;
                    end
                    JOB_RUN: if (mvm_done) job_state <= JOB_RETIRE;
                    JOB_RETIRE: begin
                        for (int spin=0;spin<SPIN_COUNT;spin++)
                            accumulator_h0[spin] <= accumulator_h0[spin] + mvm_result[spin];
                        job_state <= JOB_IDLE;
                    end
                    default: job_state <= JOB_IDLE;
                endcase
            end

            // Each accepted flit contributes PARTIAL_LANES accumulator words.
            // Beat counters wrap at packet boundaries; multiple packets add
            // into the same 32-lane accumulator bank.
            if (core_state == CORE_ACCUMULATE) begin
                if (h0_partial_valid && h0_partial_ready) begin
                    for (int lane = 0; lane < PARTIAL_LANES; lane++) begin
                        accumulator_h0[
                            h0_partial_beat_count*PARTIAL_LANES + lane
                        ] <= accumulator_h0[
                            h0_partial_beat_count*PARTIAL_LANES + lane
                        ] + $signed(h0_partial_data[lane*ACC_W +: ACC_W]);
                    end

                    if (h0_partial_beat_count ==
                        PARTIAL_BEAT_W'(PARTIAL_BEATS-1))
                        h0_partial_beat_count <= '0;
                    else
                        h0_partial_beat_count <= h0_partial_beat_count + 1'b1;
                end

                if (ext_partial_valid && ext_partial_ready) begin
                    for (int lane = 0; lane < PARTIAL_LANES; lane++) begin
                        accumulator_ext[
                            ext_partial_beat_count*PARTIAL_LANES + lane
                        ] <= accumulator_ext[
                            ext_partial_beat_count*PARTIAL_LANES + lane
                        ] + $signed(ext_partial_data[lane*ACC_W +: ACC_W]);
                    end

                    if (ext_partial_beat_count ==
                        PARTIAL_BEAT_W'(PARTIAL_BEATS-1))
                        ext_partial_beat_count <= '0;
                    else
                        ext_partial_beat_count <= ext_partial_beat_count + 1'b1;
                end
            end

            // Evaluate a*x + b*sum(J*x) + noise. coeff_c and noise_decay are
            // retained at the interface for controller compatibility; the
            // controller currently supplies the already-scaled amplitude.
            if (core_state == CORE_FINALIZE) begin
                for (int spin = 0; spin < SPIN_COUNT; spin++) begin
                    logic signed [COEFF_W-1:0] a_term;
                    logic signed [COEFF_W-1:0] noise;
                    a_term = state_current[spin] ?
                        +coeff_a_reg : -coeff_a_reg;
                    noise = lfsr_state[spin] ?
                        +noise_amplitude : -noise_amplitude;
                    accumulator_total[spin] <= a_term +
                        coeff_b_reg * (accumulator_local[spin] +
                                       accumulator_h0[spin] +
                                       accumulator_ext[spin]) +
                        noise;
                end
            end

            if (core_state == CORE_COMMIT)
                state_current <= state_next;
        end
    end

    // ------------------------------------------------------------------
    // Core lifecycle control
    // ------------------------------------------------------------------
    always_comb begin
        core_state_n = core_state;
        unique case (core_state)
            CORE_RESET:
                if (init_start)
                    core_state_n = CORE_INIT;
            CORE_INIT:
                if (!weight_init_ready)
                    core_state_n = CORE_IDLE;
            CORE_IDLE:
                if (iter_start)
                    core_state_n = CORE_ACCUMULATE;
            CORE_ACCUMULATE:
                if (local_compute_done && partials_done_pending &&
                    (!CORES_ONLY || (job_state == JOB_IDLE && !core_job_valid)) &&
                    (h0_partial_beat_count == '0) &&
                    (ext_partial_beat_count == '0))
                    core_state_n = CORE_FINALIZE;
            CORE_FINALIZE:
                core_state_n = CORE_WAIT_COMMIT;
            CORE_WAIT_COMMIT:
                if (commit)
                    core_state_n = CORE_COMMIT;
            CORE_COMMIT:
                core_state_n = (done_latched || done) ?
                    CORE_DONE : CORE_IDLE;
            CORE_DONE:
                core_state_n = CORE_DONE;
        endcase

    end
endmodule
