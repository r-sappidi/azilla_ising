import ising_pkg::*;

// H0 compute node.
//
// An H0 contains a parameterizable number of 32-spin cores and symmetric
// cross-core MVM engines.  Diagonal 32x32 blocks are loaded directly into the
// spin cores; this module evaluates only blocks connecting two different
// cores.
//
// The DMA/dispatcher is intentionally outside this module.  It supplies a
// command and a fixed-length weight stream independently to every pair engine.
// Each pair engine has two block-buffer slots, allowing the DMA to load the
// next J block while the current block is being evaluated.
module ising_h0 #(
    parameter int CORE_COUNT = 128,
    parameter int MVM_COUNT  = 16
) (
    input logic clk,
    input logic rst,

    // H0/core initialization.  Each core owns one diagonal 32x32 J block.
    input  logic                                        init_start,
    output logic                                        init_done,
    input  logic [CORE_COUNT-1:0]                       core_weight_valid_i,
    output logic [CORE_COUNT-1:0]                       core_weight_ready_o,
    input  logic [CORE_COUNT-1:0][DATA_W-1:0]           core_weight_data_i,
    input  logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]       init_state_i,
    input  logic [CORE_COUNT-1:0][31:0]                 noise_seed_i,
    input  logic signed [COEFF_W-1:0]                   coeff_a_i,
    input  logic signed [COEFF_W-1:0]                   coeff_b_i,
    input  logic signed [COEFF_W-1:0]                   noise_amplitude_i,

    // Global iteration/barrier control.
    input  logic                                        iter_start,
    input  logic                                        commit,
    input  logic                                        done,
    output logic                                        iter_done,

    // The external dispatcher asserts this only after it has issued every
    // local cross-core block for the current iteration.
    input  logic                                        local_schedule_done_i,

    // Per-engine command interfaces from the DMA-side dispatcher.  A command
    // reserves one of that engine's two J buffers.  core_a must differ from
    // core_b; diagonal blocks belong to the spin cores themselves.
    input  logic [MVM_COUNT-1:0]                        dma_cmd_valid_i,
    output logic [MVM_COUNT-1:0]                        dma_cmd_ready_o,
    input  logic [MVM_COUNT-1:0][$clog2(CORE_COUNT)-1:0] dma_core_a_i,
    input  logic [MVM_COUNT-1:0][$clog2(CORE_COUNT)-1:0] dma_core_b_i,

    // Per-engine J streams.  With the current parameters, one 256-bit beat is
    // one matrix row and a complete 32x32 int8 block contains 32 beats.
    input  logic [MVM_COUNT-1:0]                        dma_weight_valid_i,
    output logic [MVM_COUNT-1:0]                        dma_weight_ready_o,
    input  logic [MVM_COUNT-1:0][DATA_W-1:0]            dma_weight_data_i,

    // One packetized hierarchy-partial stream.  core_id must remain stable for
    // all beats of a 32-entry partial packet.
    input  logic                                        ext_partial_valid_i,
    output logic                                        ext_partial_ready_o,
    input  logic [$clog2(CORE_COUNT)-1:0]               ext_partial_core_i,
    input  logic signed [DATA_W-1:0]                    ext_partial_data_i,
    input  logic                                        ext_partials_done_i,

    // Frozen and next state vectors, grouped in the same 32-spin slices used
    // throughout the hierarchy.
    output logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]       state_current_o,
    output logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]       state_next_o
);

    localparam int CORE_ID_W = (CORE_COUNT > 1) ? $clog2(CORE_COUNT) : 1;
    localparam int WEIGHT_BLOCK_BITS = SPIN_COUNT * SPIN_COUNT * WEIGHT_W;
    localparam int WEIGHT_BEATS = WEIGHT_BLOCK_BITS / DATA_W;
    localparam int WEIGHT_BEAT_W = (WEIGHT_BEATS > 1) ? $clog2(WEIGHT_BEATS) : 1;
    localparam int PARTIAL_BEATS = SPIN_COUNT * ACC_W / DATA_W;
    localparam int PARTIAL_BEAT_W = (PARTIAL_BEATS > 1) ? $clog2(PARTIAL_BEATS) : 1;
    localparam int PARTIAL_LANES = DATA_W / ACC_W;

    typedef enum logic [2:0] {
        H0_RESET,
        H0_INIT,
        H0_IDLE,
        H0_RUN,
        H0_WAIT_COMMIT,
        H0_COMMIT,
        H0_DONE
    } h0_state_t;

    typedef enum logic [2:0] {
        ENGINE_IDLE,
        ENGINE_START,
        ENGINE_COMPUTE,
        ENGINE_SEND_A,
        ENGINE_SEND_B
    } engine_state_t;

    h0_state_t h0_state, h0_state_n;
    engine_state_t engine_state [0:MVM_COUNT-1];
    engine_state_t engine_state_n [0:MVM_COUNT-1];

    // ---------------------------------------------------------------------
    // Spin-core connections
    // ---------------------------------------------------------------------
    logic [CORE_COUNT-1:0] core_init_done;
    logic [CORE_COUNT-1:0] core_iter_done;

    logic [CORE_COUNT-1:0] core_h0_partial_valid;
    logic [CORE_COUNT-1:0] core_h0_partial_ready;
    logic signed [CORE_COUNT-1:0][DATA_W-1:0] core_h0_partial_data;

    logic [CORE_COUNT-1:0] core_ext_partial_valid;
    logic [CORE_COUNT-1:0] core_ext_partial_ready;
    logic signed [CORE_COUNT-1:0][DATA_W-1:0] core_ext_partial_data;

    logic partials_done_to_cores;
    logic local_compute_done;
    logic local_schedule_done_pending;
    logic ext_partials_done_pending;

    // ---------------------------------------------------------------------
    // Per-engine double-buffered J storage and command metadata
    // ---------------------------------------------------------------------
    logic [WEIGHT_W*SPIN_COUNT-1:0]
        engine_weight [0:MVM_COUNT-1][0:1][0:SPIN_COUNT-1];

    logic [1:0] engine_slot_valid [0:MVM_COUNT-1];
    logic [CORE_ID_W-1:0] engine_slot_core_a [0:MVM_COUNT-1][0:1];
    logic [CORE_ID_W-1:0] engine_slot_core_b [0:MVM_COUNT-1][0:1];

    logic engine_load_active [0:MVM_COUNT-1];
    logic engine_load_slot [0:MVM_COUNT-1];
    logic [WEIGHT_BEAT_W-1:0] engine_weight_beat [0:MVM_COUNT-1];
    logic engine_active_slot [0:MVM_COUNT-1];

    // ---------------------------------------------------------------------
    // MVM datapaths
    // ---------------------------------------------------------------------
    logic [MVM_COUNT-1:0] engine_start;
    logic [MVM_COUNT-1:0] engine_done_a;
    logic [MVM_COUNT-1:0] engine_done_b;

    logic [SPIN_COUNT-1:0] engine_state_a [0:MVM_COUNT-1];
    logic [SPIN_COUNT-1:0] engine_state_b [0:MVM_COUNT-1];
    logic [WEIGHT_W*SPIN_COUNT-1:0] engine_active_weight [0:MVM_COUNT-1][0:SPIN_COUNT-1];
    logic [WEIGHT_W*SPIN_COUNT-1:0] engine_active_weight_transpose [0:MVM_COUNT-1][0:SPIN_COUNT-1];

    logic signed [ACC_W-1:0] engine_result_a [0:MVM_COUNT-1][0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] engine_result_b [0:MVM_COUNT-1][0:SPIN_COUNT-1];

    // Each engine serializes one 32-entry result onto four 256-bit beats.
    // The destination-banked arbiter below allows unrelated cores to accept
    // engine results concurrently.
    logic [PARTIAL_BEAT_W-1:0] engine_partial_beat [0:MVM_COUNT-1];
    logic [MVM_COUNT-1:0] engine_partial_valid;
    logic [MVM_COUNT-1:0] engine_partial_grant;
    logic [MVM_COUNT-1:0][CORE_ID_W-1:0] engine_partial_core;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] engine_partial_data;

    // ---------------------------------------------------------------------
    // Core instances
    // ---------------------------------------------------------------------
    generate
        for (genvar core_index = 0; core_index < CORE_COUNT; core_index++) begin : gen_cores
            spin_core core (
                .clk,
                .rst,
                .init_start,
                .init_done(core_init_done[core_index]),
                .weight_init_valid(core_weight_valid_i[core_index]),
                .weight_init_ready(core_weight_ready_o[core_index]),
                .weight_init_data(core_weight_data_i[core_index]),
                .noise_seed(noise_seed_i[core_index]),
                .coeff_a(coeff_a_i),
                .coeff_b(coeff_b_i),
                .coeff_c('0),
                .noise_amplitude(noise_amplitude_i),
                .init_state(init_state_i[core_index]),
                .iter_start,
                .partials_done(partials_done_to_cores),
                .commit,
                .noise_decay('0),
                .iter_done(core_iter_done[core_index]),
                .done,
                .h0_partial_valid(core_h0_partial_valid[core_index]),
                .h0_partial_ready(core_h0_partial_ready[core_index]),
                .h0_partial_data(core_h0_partial_data[core_index]),
                .ext_partial_valid(core_ext_partial_valid[core_index]),
                .ext_partial_ready(core_ext_partial_ready[core_index]),
                .ext_partial_data(core_ext_partial_data[core_index]),
                .state_next(state_next_o[core_index]),
                .state_current(state_current_o[core_index])
            );
        end
    endgenerate

    // ---------------------------------------------------------------------
    // Symmetric pair-engine instances
    //
    // For J_ab, direction A computes J_ab*x_b.  Direction B computes
    // transpose(J_ab)*x_a.  Both directions therefore reuse one DMA fetch.
    // ---------------------------------------------------------------------
    generate
        for (genvar engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin : gen_engines
            always_comb begin
                engine_state_a[engine_index] =
                    state_current_o[engine_slot_core_a[engine_index][engine_active_slot[engine_index]]];
                engine_state_b[engine_index] =
                    state_current_o[engine_slot_core_b[engine_index][engine_active_slot[engine_index]]];

                for (int row = 0; row < SPIN_COUNT; row++) begin
                    engine_active_weight[engine_index][row] =
                        engine_weight[engine_index][engine_active_slot[engine_index]][row];
                    for (int column = 0; column < SPIN_COUNT; column++) begin
                        engine_active_weight_transpose[engine_index][row]
                            [column*WEIGHT_W +: WEIGHT_W] =
                            engine_weight[engine_index][engine_active_slot[engine_index]][column]
                                [row*WEIGHT_W +: WEIGHT_W];
                    end
                end
            end

            mvm mvm_to_a (
                .clk,
                .rst,
                .start(engine_start[engine_index]),
                .weight_mem(engine_active_weight[engine_index]),
                .result(engine_result_a[engine_index]),
                .state(engine_state_b[engine_index]),
                .done(engine_done_a[engine_index])
            );

            mvm mvm_to_b (
                .clk,
                .rst,
                .start(engine_start[engine_index]),
                .weight_mem(engine_active_weight_transpose[engine_index]),
                .result(engine_result_b[engine_index]),
                .state(engine_state_a[engine_index]),
                .done(engine_done_b[engine_index])
            );
        end
    endgenerate

    assign init_done = &core_init_done;
    assign iter_done = &core_iter_done;
    assign partials_done_to_cores =
        local_compute_done && ext_partials_done_pending;

    // Route the single external partial stream to its destination core.
    always_comb begin
        core_ext_partial_valid = '0;
        core_ext_partial_data = '0;
        ext_partial_ready_o = 1'b0;

        if (ext_partial_core_i < CORE_COUNT) begin
            core_ext_partial_valid[ext_partial_core_i] = ext_partial_valid_i;
            core_ext_partial_data[ext_partial_core_i] = ext_partial_data_i;
            ext_partial_ready_o = core_ext_partial_ready[ext_partial_core_i];
        end
    end

    // Build each engine's current partial beat and destination request.
    always_comb begin
        engine_partial_valid = '0;
        engine_partial_core = '0;
        engine_partial_data = '0;

        for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
            if (engine_state[engine_index] == ENGINE_SEND_A) begin
                engine_partial_valid[engine_index] = 1'b1;
                engine_partial_core[engine_index] =
                    engine_slot_core_a[engine_index][engine_active_slot[engine_index]];
                for (int lane = 0; lane < PARTIAL_LANES; lane++) begin
                    engine_partial_data[engine_index][lane*ACC_W +: ACC_W] =
                        engine_result_a[engine_index]
                            [engine_partial_beat[engine_index]*PARTIAL_LANES + lane];
                end
            end
            else if (engine_state[engine_index] == ENGINE_SEND_B) begin
                engine_partial_valid[engine_index] = 1'b1;
                engine_partial_core[engine_index] =
                    engine_slot_core_b[engine_index][engine_active_slot[engine_index]];
                for (int lane = 0; lane < PARTIAL_LANES; lane++) begin
                    engine_partial_data[engine_index][lane*ACC_W +: ACC_W] =
                        engine_result_b[engine_index]
                            [engine_partial_beat[engine_index]*PARTIAL_LANES + lane];
                end
            end
        end
    end

    // One arbiter per destination core.  Different destination cores can
    // accept partial beats in the same cycle.  Fixed engine priority keeps the
    // first version simple; all schedules are finite, so it cannot lose data.
    always_comb begin
        core_h0_partial_valid = '0;
        core_h0_partial_data = '0;
        engine_partial_grant = '0;

        for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
            logic found_engine;
            found_engine = 1'b0;

            for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
                if (!found_engine && engine_partial_valid[engine_index] &&
                    engine_partial_core[engine_index] == CORE_ID_W'(core_index)) begin
                    core_h0_partial_valid[core_index] = 1'b1;
                    core_h0_partial_data[core_index] = engine_partial_data[engine_index];
                    engine_partial_grant[engine_index] =
                        core_h0_partial_ready[core_index];
                    found_engine = 1'b1;
                end
            end
        end
    end

    // The H0 is locally finished after the dispatcher has ended the schedule,
    // both buffer slots are empty, no block load is active, and every engine
    // has drained both directional results.
    always_comb begin
        local_compute_done = local_schedule_done_pending;
        for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
            if (engine_load_active[engine_index] ||
                engine_slot_valid[engine_index] != 2'b00 ||
                engine_state[engine_index] != ENGINE_IDLE)
                local_compute_done = 1'b0;
        end
    end

    // Dispatcher-facing ready signals.  A command chooses a free slot, and
    // weight beats are accepted only after that command has been captured.
    always_comb begin
        dma_cmd_ready_o = '0;
        dma_weight_ready_o = '0;

        for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
            dma_cmd_ready_o[engine_index] =
                h0_state == H0_RUN &&
                !engine_load_active[engine_index] &&
                engine_slot_valid[engine_index] != 2'b11;
            dma_weight_ready_o[engine_index] =
                h0_state == H0_RUN && engine_load_active[engine_index];
        end
    end

    // Engine next-state logic follows the same style as spin_core: defaults
    // hold the current state, and each case only describes a transition.
    always_comb begin
        for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
            engine_state_n[engine_index] = engine_state[engine_index];
            engine_start[engine_index] = 1'b0;

            unique case (engine_state[engine_index])
                ENGINE_IDLE: begin
                    if (engine_slot_valid[engine_index] != 2'b00)
                        engine_state_n[engine_index] = ENGINE_START;
                end
                ENGINE_START: begin
                    engine_start[engine_index] = 1'b1;
                    engine_state_n[engine_index] = ENGINE_COMPUTE;
                end
                ENGINE_COMPUTE: begin
                    if (engine_done_a[engine_index] && engine_done_b[engine_index])
                        engine_state_n[engine_index] = ENGINE_SEND_A;
                end
                ENGINE_SEND_A: begin
                    if (engine_partial_grant[engine_index] &&
                        engine_partial_beat[engine_index] == PARTIAL_BEAT_W'(PARTIAL_BEATS-1))
                        engine_state_n[engine_index] = ENGINE_SEND_B;
                end
                ENGINE_SEND_B: begin
                    if (engine_partial_grant[engine_index] &&
                        engine_partial_beat[engine_index] == PARTIAL_BEAT_W'(PARTIAL_BEATS-1))
                        engine_state_n[engine_index] = ENGINE_IDLE;
                end
                default: engine_state_n[engine_index] = ENGINE_IDLE;
            endcase
        end
    end

    // H0 state register.
    always_ff @(posedge clk) begin
        if (rst) begin
            h0_state <= H0_RESET;
            local_schedule_done_pending <= 1'b0;
            ext_partials_done_pending <= 1'b0;
        end
        else begin
            h0_state <= h0_state_n;

            // Completion may arrive in either order.  Remember both inputs
            // until all local engines and all hierarchy sources are finished.
            if (iter_start) begin
                local_schedule_done_pending <= 1'b0;
                ext_partials_done_pending <= 1'b0;
            end
            else begin
                if (local_schedule_done_i)
                    local_schedule_done_pending <= 1'b1;
                if (ext_partials_done_i)
                    ext_partials_done_pending <= 1'b1;
            end
        end
    end

    // Sequential engine datapaths.  A generate block makes each engine an
    // explicit hardware replica and avoids one large procedural array loop.
    generate
        for (genvar engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin : gen_engine_control
            always_ff @(posedge clk) begin
                if (rst) begin
                    engine_state[engine_index] <= ENGINE_IDLE;
                    engine_slot_valid[engine_index] <= 2'b00;
                    engine_load_active[engine_index] <= 1'b0;
                    engine_load_slot[engine_index] <= 1'b0;
                    engine_weight_beat[engine_index] <= '0;
                    engine_active_slot[engine_index] <= 1'b0;
                    engine_partial_beat[engine_index] <= '0;
                    engine_slot_core_a[engine_index] <= '{default: '0};
                    engine_slot_core_b[engine_index] <= '{default: '0};
                end
                else begin
                    engine_state[engine_index] <= engine_state_n[engine_index];

                    // Reserve a free buffer slot for the next DMA command.
                    if (dma_cmd_valid_i[engine_index] && dma_cmd_ready_o[engine_index]) begin
                        if (!engine_slot_valid[engine_index][0]) begin
                            engine_load_slot[engine_index] <= 1'b0;
                            engine_slot_core_a[engine_index][0] <= dma_core_a_i[engine_index];
                            engine_slot_core_b[engine_index][0] <= dma_core_b_i[engine_index];
                        end
                        else begin
                            engine_load_slot[engine_index] <= 1'b1;
                            engine_slot_core_a[engine_index][1] <= dma_core_a_i[engine_index];
                            engine_slot_core_b[engine_index][1] <= dma_core_b_i[engine_index];
                        end
                        engine_load_active[engine_index] <= 1'b1;
                        engine_weight_beat[engine_index] <= '0;
                    end

                    // Store one complete row of the row-major J block.
                    if (dma_weight_valid_i[engine_index] && dma_weight_ready_o[engine_index]) begin
                        engine_weight[engine_index][engine_load_slot[engine_index]]
                            [engine_weight_beat[engine_index]] <= dma_weight_data_i[engine_index];

                        if (engine_weight_beat[engine_index] == WEIGHT_BEAT_W'(WEIGHT_BEATS-1)) begin
                            engine_slot_valid[engine_index][engine_load_slot[engine_index]] <= 1'b1;
                            engine_load_active[engine_index] <= 1'b0;
                            engine_weight_beat[engine_index] <= '0;
                        end
                        else begin
                            engine_weight_beat[engine_index] <=
                                engine_weight_beat[engine_index] + 1'b1;
                        end
                    end

                    // Choose a completed buffer when an idle engine begins work.
                    if (engine_state[engine_index] == ENGINE_IDLE &&
                        engine_state_n[engine_index] == ENGINE_START) begin
                        if (engine_slot_valid[engine_index][0])
                            engine_active_slot[engine_index] <= 1'b0;
                        else
                            engine_active_slot[engine_index] <= 1'b1;
                    end

                    // Advance one four-beat packet at a time.
                    if (engine_state[engine_index] == ENGINE_SEND_A ||
                        engine_state[engine_index] == ENGINE_SEND_B) begin
                        if (engine_partial_grant[engine_index]) begin
                            if (engine_partial_beat[engine_index] == PARTIAL_BEAT_W'(PARTIAL_BEATS-1))
                                engine_partial_beat[engine_index] <= '0;
                            else
                                engine_partial_beat[engine_index] <=
                                    engine_partial_beat[engine_index] + 1'b1;
                        end
                    end
                    else begin
                        engine_partial_beat[engine_index] <= '0;
                    end

                    // After direction B drains, the active J-buffer slot is
                    // free and can immediately be reused by the dispatcher.
                    if (engine_state[engine_index] == ENGINE_SEND_B &&
                        engine_partial_grant[engine_index] &&
                        engine_partial_beat[engine_index] == PARTIAL_BEAT_W'(PARTIAL_BEATS-1)) begin
                        engine_slot_valid[engine_index][engine_active_slot[engine_index]] <= 1'b0;
                    end
                end
            end
        end
    endgenerate

    // H0-level control state.  The spin cores perform the final update and
    // remain in their WAIT_COMMIT state until the global barrier commits them.
    always_comb begin
        h0_state_n = h0_state;

        unique case (h0_state)
            H0_RESET: begin
                if (init_start)
                    h0_state_n = H0_INIT;
            end
            H0_INIT: begin
                if (init_done)
                    h0_state_n = H0_IDLE;
            end
            H0_IDLE: begin
                if (iter_start)
                    h0_state_n = H0_RUN;
            end
            H0_RUN: begin
                if (iter_done)
                    h0_state_n = H0_WAIT_COMMIT;
            end
            H0_WAIT_COMMIT: begin
                if (commit)
                    h0_state_n = H0_COMMIT;
            end
            H0_COMMIT: begin
                if (done)
                    h0_state_n = H0_DONE;
                else
                    h0_state_n = H0_IDLE;
            end
            H0_DONE: begin
                h0_state_n = H0_DONE;
            end
            default: h0_state_n = H0_RESET;
        endcase
    end

endmodule
