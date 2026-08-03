import ising_pkg::*;

// H0 hierarchy router/compute node.
//
// This module does not contain the 32-spin cores.  A higher-level H0 tile
// connects the cores' frozen state vectors to state_current_i and connects the
// partial outputs below to each core's h0_partial ready/valid interface.
//
// The node evaluates only cross-core interactions.  A block command identifies
// two different 32-spin cores A and B.  The associated row-major J_ab block is
// fetched once and evaluated in both directions:
//
//     partial_a = J_ab * x_b
//     partial_b = transpose(J_ab) * x_a
//
// Diagonal J_aa blocks remain private to the spin cores and never pass through
// this node.
module h0_router #(
    parameter int CORE_COUNT = 128,
    parameter int MVM_COUNT  = 16
) (
    input logic clk,
    input logic rst,

    // Iteration control.  iter_start is a one-cycle pulse.  iter_done remains
    // high after completion and is cleared by the next iter_start pulse.
    input  logic                                         iter_start,
    output logic                                         iter_done,

    // Frozen 32-bit state vector from every spin core in this H0.  These must
    // remain stable for the complete iteration.
    input logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]         state_current_i,

    // The external scheduler/DMA asserts this after issuing the final J block
    // for the iteration.  The pulse is latched until every engine is drained.
    input logic                                           schedule_done_i,

    // One command interface per symmetric pair engine.  A command reserves
    // one of that engine's two J-buffer slots.
    input  logic [MVM_COUNT-1:0]                         dma_cmd_valid_i,
    output logic [MVM_COUNT-1:0]                         dma_cmd_ready_o,
    input  logic [MVM_COUNT-1:0][$clog2(CORE_COUNT)-1:0] dma_core_a_i,
    input  logic [MVM_COUNT-1:0][$clog2(CORE_COUNT)-1:0] dma_core_b_i,

    // Weight stream associated with the previously accepted command.  With a
    // 256-bit bus, one 32x32 int8 block contains exactly 32 accepted beats.
    input  logic [MVM_COUNT-1:0]                         dma_weight_valid_i,
    output logic [MVM_COUNT-1:0]                         dma_weight_ready_o,
    input  logic [MVM_COUNT-1:0][DATA_W-1:0]             dma_weight_data_i,

    // One ready/valid H0-partial stream per destination spin core.  Each
    // 32-entry int32 result is serialized into four 256-bit beats.
    output logic [CORE_COUNT-1:0]                        partial_valid_o,
    input  logic [CORE_COUNT-1:0]                        partial_ready_i,
    output logic signed [CORE_COUNT-1:0][DATA_W-1:0]     partial_data_o
);

    localparam int CORE_ID_W = (CORE_COUNT > 1) ? $clog2(CORE_COUNT) : 1;
    localparam int WEIGHT_BLOCK_BITS = SPIN_COUNT * SPIN_COUNT * WEIGHT_W;
    localparam int WEIGHT_BEATS = WEIGHT_BLOCK_BITS / DATA_W;
    localparam int WEIGHT_BEAT_W = (WEIGHT_BEATS > 1) ? $clog2(WEIGHT_BEATS) : 1;
    localparam int PARTIAL_BEATS = SPIN_COUNT * ACC_W / DATA_W;
    localparam int PARTIAL_BEAT_W = (PARTIAL_BEATS > 1) ? $clog2(PARTIAL_BEATS) : 1;
    localparam int PARTIAL_LANES = DATA_W / ACC_W;

    typedef enum logic [1:0] {
        H0_RESET,
        H0_IDLE,
        H0_RUN,
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

    logic schedule_done_pending;
    logic all_engines_done;

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
    // Symmetric pair MVM datapaths
    //
    // MVM_COUNT counts symmetric pair engines.  Each pair engine contains two
    // instances of the existing one-direction mvm module.
    // ---------------------------------------------------------------------
    logic [MVM_COUNT-1:0] engine_start;
    logic [MVM_COUNT-1:0] engine_done_a;
    logic [MVM_COUNT-1:0] engine_done_b;

    logic [SPIN_COUNT-1:0] engine_state_a [0:MVM_COUNT-1];
    logic [SPIN_COUNT-1:0] engine_state_b [0:MVM_COUNT-1];
    logic [WEIGHT_W*SPIN_COUNT-1:0]
        engine_active_weight [0:MVM_COUNT-1][0:SPIN_COUNT-1];
    logic [WEIGHT_W*SPIN_COUNT-1:0]
        engine_active_weight_transpose [0:MVM_COUNT-1][0:SPIN_COUNT-1];

    logic signed [ACC_W-1:0]
        engine_result_a [0:MVM_COUNT-1][0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0]
        engine_result_b [0:MVM_COUNT-1][0:SPIN_COUNT-1];

    // Result serializer and reduction-fabric request signals.
    logic [PARTIAL_BEAT_W-1:0] engine_partial_beat [0:MVM_COUNT-1];
    logic [MVM_COUNT-1:0] engine_partial_valid;
    // High for one cycle when this engine's current partial beat is selected
    // by the destination arbiter and accepted by the destination spin core.
    logic [MVM_COUNT-1:0] engine_partial_accepted;
    logic [MVM_COUNT-1:0][CORE_ID_W-1:0] engine_partial_core;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] engine_partial_data;

    assign iter_done = h0_state == H0_DONE;

    // ---------------------------------------------------------------------
    // MVM instances
    // ---------------------------------------------------------------------
    generate
        for (genvar engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin : gen_engines
            // Select the two frozen source vectors and the active J-buffer.
            // The transpose is wiring, not a second physical matrix copy.
            always_comb begin
                engine_state_a[engine_index] =
                    state_current_i
                        [engine_slot_core_a[engine_index][engine_active_slot[engine_index]]];
                engine_state_b[engine_index] =
                    state_current_i
                        [engine_slot_core_b[engine_index][engine_active_slot[engine_index]]];

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

            // J_ab*x_b contributes to core A.
            mvm mvm_to_a (
                .clk,
                .rst,
                .start(engine_start[engine_index]),
                .weight_mem(engine_active_weight[engine_index]),
                .result(engine_result_a[engine_index]),
                .state(engine_state_b[engine_index]),
                .done(engine_done_a[engine_index])
            );

            // transpose(J_ab)*x_a contributes to core B.
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

    // ---------------------------------------------------------------------
    // Result packet formation
    // ---------------------------------------------------------------------
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

    // ---------------------------------------------------------------------
    // Destination-banked result routing
    //
    // Every core is an independent destination bank.  Engines targeting
    // different cores can transfer in the same cycle.  Engines targeting the
    // same core are serialized using fixed engine-index priority.
    // ---------------------------------------------------------------------
    always_comb begin
        partial_valid_o = '0;
        partial_data_o = '0;
        engine_partial_accepted = '0;

        for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
            logic found_engine;
            found_engine = 1'b0;

            for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
                if (!found_engine && engine_partial_valid[engine_index] &&
                    engine_partial_core[engine_index] == CORE_ID_W'(core_index)) begin
                    partial_valid_o[core_index] = 1'b1;
                    partial_data_o[core_index] = engine_partial_data[engine_index];
                    engine_partial_accepted[engine_index] = partial_ready_i[core_index];
                    found_engine = 1'b1;
                end
            end
        end
    end

    // ---------------------------------------------------------------------
    // DMA interface
    // ---------------------------------------------------------------------
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

    // The iteration is locally complete only after the scheduler is finished,
    // every J-buffer slot is empty, and every result packet has drained.
    always_comb begin
        all_engines_done = schedule_done_pending;

        for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
            if (engine_load_active[engine_index] ||
                engine_slot_valid[engine_index] != 2'b00 ||
                engine_state[engine_index] != ENGINE_IDLE)
                all_engines_done = 1'b0;
        end
    end

    // ---------------------------------------------------------------------
    // Per-engine state machines
    // ---------------------------------------------------------------------
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
                    if (engine_partial_accepted[engine_index] &&
                        engine_partial_beat[engine_index] == PARTIAL_BEAT_W'(PARTIAL_BEATS-1))
                        engine_state_n[engine_index] = ENGINE_SEND_B;
                end
                ENGINE_SEND_B: begin
                    if (engine_partial_accepted[engine_index] &&
                        engine_partial_beat[engine_index] == PARTIAL_BEAT_W'(PARTIAL_BEATS-1))
                        engine_state_n[engine_index] = ENGINE_IDLE;
                end
                default: engine_state_n[engine_index] = ENGINE_IDLE;
            endcase
        end
    end

    // H0 control and completion-pulse latch.
    always_ff @(posedge clk) begin
        if (rst) begin
            h0_state <= H0_RESET;
            schedule_done_pending <= 1'b0;
        end
        else begin
            h0_state <= h0_state_n;

            if (iter_start)
                schedule_done_pending <= 1'b0;
            else if (schedule_done_i)
                schedule_done_pending <= 1'b1;
        end
    end

    // Sequential engine datapaths.  Each generated block corresponds to one
    // physical symmetric pair engine.
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

                    // Reserve a free J-buffer slot and remember its two core
                    // destinations before accepting any weight beats.
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

                    // Assemble one complete row-major 32x32 J block.
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

                    // Select a completed slot before issuing the MVM start
                    // pulse on the following cycle.
                    if (engine_state[engine_index] == ENGINE_IDLE &&
                        engine_state_n[engine_index] == ENGINE_START) begin
                        if (engine_slot_valid[engine_index][0])
                            engine_active_slot[engine_index] <= 1'b0;
                        else
                            engine_active_slot[engine_index] <= 1'b1;
                    end

                    // Advance the four-beat result serializer only on an
                    // accepted ready/valid transfer.
                    if (engine_state[engine_index] == ENGINE_SEND_A ||
                        engine_state[engine_index] == ENGINE_SEND_B) begin
                        if (engine_partial_accepted[engine_index]) begin
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

                    // Both directional results have now been accepted, so the
                    // active block-buffer slot can be reused by the DMA.
                    if (engine_state[engine_index] == ENGINE_SEND_B &&
                        engine_partial_accepted[engine_index] &&
                        engine_partial_beat[engine_index] == PARTIAL_BEAT_W'(PARTIAL_BEATS-1)) begin
                        engine_slot_valid[engine_index][engine_active_slot[engine_index]] <= 1'b0;
                    end
                end
            end
        end
    endgenerate

    // Router-level iteration state machine.
    always_comb begin
        h0_state_n = h0_state;

        unique case (h0_state)
            H0_RESET: begin
                h0_state_n = H0_IDLE;
            end
            H0_IDLE: begin
                if (iter_start)
                    h0_state_n = H0_RUN;
            end
            H0_RUN: begin
                if (all_engines_done)
                    h0_state_n = H0_DONE;
            end
            H0_DONE: begin
                if (iter_start)
                    h0_state_n = H0_RUN;
            end
            default: h0_state_n = H0_RESET;
        endcase
    end

endmodule
