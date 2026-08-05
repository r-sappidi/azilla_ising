import ising_pkg::*;

// Generic hierarchy compute node.
//
// This module is independent of hierarchy depth and does not contain spin
// cores. A controller preloads the state blocks used by this node and supplies
// commands containing state-table indices and global destination block IDs.
//
// The node evaluates interactions between two different 32-spin blocks A and
// B. The associated row-major J_ab block is
// fetched once and evaluated in both directions:
//
//     partial_a = J_ab * x_b
//     partial_b = transpose(J_ab) * x_a
//
// Diagonal J_aa blocks normally remain private to the spin cores.
module hierarchy_node #(
    parameter int STATE_ENTRY_COUNT = 32,
    parameter int MVM_COUNT         = 16,
    parameter int GLOBAL_BLOCK_ID_W = 16
) (
    input logic clk,
    input logic rst,

    // Iteration control.  iter_start is a one-cycle pulse.  iter_done remains
    // high after completion and is cleared by the next iter_start pulse.
    input  logic                                         iter_start,
    output logic                                         iter_done,

    // State-table load port. The controller loads every state block referenced
    // by this node's schedule before iter_start. State entries remain frozen
    // throughout the iteration.
    input  logic                                          state_valid_i,
    output logic                                          state_ready_o,
    input  logic [((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                         state_index_i,
    input  logic [SPIN_COUNT-1:0]                        state_data_i,

    // The external scheduler/DMA asserts this after issuing the final J block
    // for the iteration.  The pulse is latched until every engine is drained.
    input logic                                           schedule_done_i,

    // One command interface per symmetric pair engine.  A command reserves
    // one of that engine's two J-buffer slots.
    input  logic [MVM_COUNT-1:0]                         dma_cmd_valid_i,
    output logic [MVM_COUNT-1:0]                         dma_cmd_ready_o,
    input  logic [MVM_COUNT-1:0][((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                         dma_state_a_index_i,
    input  logic [MVM_COUNT-1:0][((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                                                         dma_state_b_index_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] dma_block_a_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] dma_block_b_i,

    // Weight stream associated with the previously accepted command.  With a
    // 256-bit bus, one 32x32 int8 block contains exactly 32 accepted beats.
    input  logic [MVM_COUNT-1:0]                         dma_weight_valid_i,
    output logic [MVM_COUNT-1:0]                         dma_weight_ready_o,
    input  logic [MVM_COUNT-1:0][DATA_W-1:0]             dma_weight_data_i,

    // One topology-neutral partial stream per symmetric engine. A tree or
    // mesh adapter maps each global destination block to a physical route.
    output logic [MVM_COUNT-1:0]                        partial_valid_o,
    input  logic [MVM_COUNT-1:0]                        partial_ready_i,
    output logic signed [MVM_COUNT-1:0][DATA_W-1:0]     partial_data_o,
    output logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] partial_block_id_o,
    output logic [MVM_COUNT-1:0]                        partial_last_o
);

    localparam int STATE_INDEX_W =
        (STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1;
    localparam int WEIGHT_BLOCK_BITS = SPIN_COUNT * SPIN_COUNT * WEIGHT_W;
    localparam int WEIGHT_BEATS = WEIGHT_BLOCK_BITS / DATA_W;
    localparam int WEIGHT_BEAT_W = (WEIGHT_BEATS > 1) ? $clog2(WEIGHT_BEATS) : 1;
    localparam int PARTIAL_BEATS = SPIN_COUNT * ACC_W / DATA_W;
    localparam int PARTIAL_BEAT_W = (PARTIAL_BEATS > 1) ? $clog2(PARTIAL_BEATS) : 1;
    localparam int PARTIAL_LANES = DATA_W / ACC_W;

    typedef enum logic [1:0] {
        NODE_RESET,
        NODE_IDLE,
        NODE_RUN,
        NODE_DONE
    } node_state_t;

    typedef enum logic [2:0] {
        ENGINE_IDLE,
        ENGINE_START,
        ENGINE_COMPUTE,
        ENGINE_SEND_A,
        ENGINE_SEND_B
    } engine_state_t;

    node_state_t node_state, node_state_n;
    engine_state_t engine_state [0:MVM_COUNT-1];
    engine_state_t engine_state_n [0:MVM_COUNT-1];

    logic schedule_done_pending;
    logic [MVM_COUNT-1:0] engine_work_done;
    logic all_engine_work_done;
    logic node_work_done;

    // ---------------------------------------------------------------------
    // Per-engine double-buffered J storage and command metadata
    // ---------------------------------------------------------------------
    logic [1:0] engine_slot_valid [0:MVM_COUNT-1];
    logic [STATE_INDEX_W-1:0] engine_slot_state_a_index [0:MVM_COUNT-1][0:1];
    logic [STATE_INDEX_W-1:0] engine_slot_state_b_index [0:MVM_COUNT-1][0:1];
    logic [GLOBAL_BLOCK_ID_W-1:0] engine_slot_block_a [0:MVM_COUNT-1][0:1];
    logic [GLOBAL_BLOCK_ID_W-1:0] engine_slot_block_b [0:MVM_COUNT-1][0:1];

    // Frozen state table populated before each iteration.
    logic [SPIN_COUNT-1:0] state_mem [0:STATE_ENTRY_COUNT-1];

    logic engine_load_active [0:MVM_COUNT-1];
    logic engine_load_slot [0:MVM_COUNT-1];
    logic [WEIGHT_BEAT_W-1:0] engine_weight_beat [0:MVM_COUNT-1];
    logic engine_active_slot [0:MVM_COUNT-1];

    // ---------------------------------------------------------------------
    // Fused symmetric MVM datapaths. Each engine reads one J row per cycle and
    // uses it for both J*x_b and transpose(J)*x_a.
    // ---------------------------------------------------------------------
    logic [MVM_COUNT-1:0] engine_start;
    logic [MVM_COUNT-1:0] engine_done;

    logic [SPIN_COUNT-1:0] engine_state_a [0:MVM_COUNT-1];
    logic [SPIN_COUNT-1:0] engine_state_b [0:MVM_COUNT-1];
    logic [WEIGHT_BEAT_W-1:0] engine_read_row [0:MVM_COUNT-1];
    logic [WEIGHT_W*SPIN_COUNT-1:0] engine_read_data [0:MVM_COUNT-1];
    logic [MVM_COUNT-1:0] engine_weight_write_enable;

    logic signed [ACC_W-1:0]
        engine_result_a [0:MVM_COUNT-1][0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0]
        engine_result_b [0:MVM_COUNT-1][0:SPIN_COUNT-1];

    // Result serializer and reduction-fabric request signals.
    logic [PARTIAL_BEAT_W-1:0] engine_partial_beat [0:MVM_COUNT-1];
    logic [MVM_COUNT-1:0] engine_partial_valid;
    // High for one cycle when this engine's current partial beat is selected
    // by the output arbiter and accepted downstream.
    logic [MVM_COUNT-1:0] engine_partial_accepted;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] engine_partial_block;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] engine_partial_data;

    assign iter_done = node_state == NODE_DONE;
    assign state_ready_o = node_state != NODE_RUN;

    // ---------------------------------------------------------------------
    // MVM instances
    // ---------------------------------------------------------------------
    generate
        for (genvar engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin : gen_engines
            // Select the two frozen source vectors. The J storage below has a
            // synchronous row read and never stores a transpose.
            always_comb begin
                engine_state_a[engine_index] =
                    state_mem
                        [engine_slot_state_a_index[engine_index][engine_active_slot[engine_index]]];
                engine_state_b[engine_index] =
                    state_mem
                        [engine_slot_state_b_index[engine_index][engine_active_slot[engine_index]]];

            end

            j_block_sram #(
                .ROW_COUNT(SPIN_COUNT),
                .ROW_W(SPIN_COUNT*WEIGHT_W)
            ) weight_sram (
                .clk,
                .write_enable_i(engine_weight_write_enable[engine_index]),
                .write_slot_i(engine_load_slot[engine_index]),
                .write_row_i(engine_weight_beat[engine_index]),
                .write_data_i(dma_weight_data_i[engine_index]),
                .read_slot_i(engine_active_slot[engine_index]),
                .read_row_i(engine_read_row[engine_index]),
                .read_data_o(engine_read_data[engine_index])
            );

            symmetric_mvm pair_mvm (
                .clk,
                .rst,
                .start(engine_start[engine_index]),
                .state_a(engine_state_a[engine_index]),
                .state_b(engine_state_b[engine_index]),
                .weight_row_o(engine_read_row[engine_index]),
                .weight_data_i(engine_read_data[engine_index]),
                .result_a(engine_result_a[engine_index]),
                .result_b(engine_result_b[engine_index]),
                .done(engine_done[engine_index])
            );
        end
    endgenerate

    // ---------------------------------------------------------------------
    // Result packet formation
    // ---------------------------------------------------------------------
    always_comb begin
        engine_partial_valid = '0;
        engine_partial_block = '0;
        engine_partial_data = '0;

        for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
            if (engine_state[engine_index] == ENGINE_SEND_A) begin
                engine_partial_valid[engine_index] = 1'b1;
                engine_partial_block[engine_index] =
                    engine_slot_block_a[engine_index][engine_active_slot[engine_index]];

                for (int lane = 0; lane < PARTIAL_LANES; lane++) begin
                    engine_partial_data[engine_index][lane*ACC_W +: ACC_W] =
                        engine_result_a[engine_index]
                            [engine_partial_beat[engine_index]*PARTIAL_LANES + lane];
                end
            end
            else if (engine_state[engine_index] == ENGINE_SEND_B) begin
                engine_partial_valid[engine_index] = 1'b1;
                engine_partial_block[engine_index] =
                    engine_slot_block_b[engine_index][engine_active_slot[engine_index]];

                for (int lane = 0; lane < PARTIAL_LANES; lane++) begin
                    engine_partial_data[engine_index][lane*ACC_W +: ACC_W] =
                        engine_result_b[engine_index]
                            [engine_partial_beat[engine_index]*PARTIAL_LANES + lane];
                end
            end
        end
    end

    // ---------------------------------------------------------------------
    // Topology-neutral engine outputs. Each engine owns a stream, so engines
    // targeting different destinations can transfer concurrently. Arbitration
    // for a shared child or mesh link belongs in the external NoC adapter.
    // ---------------------------------------------------------------------
    always_comb begin
        partial_valid_o = engine_partial_valid;
        partial_data_o = engine_partial_data;
        partial_block_id_o = engine_partial_block;
        partial_last_o = '0;
        engine_partial_accepted = '0;

        for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
            partial_last_o[engine_index] =
                engine_partial_beat[engine_index] == PARTIAL_BEAT_W'(PARTIAL_BEATS-1);
            engine_partial_accepted[engine_index] =
                engine_partial_valid[engine_index] && partial_ready_i[engine_index];
        end
    end

    // ---------------------------------------------------------------------
    // DMA interface
    // ---------------------------------------------------------------------
    always_comb begin
        dma_cmd_ready_o = '0;
        dma_weight_ready_o = '0;
        engine_weight_write_enable = '0;

        for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
            dma_cmd_ready_o[engine_index] =
                node_state == NODE_RUN &&
                !engine_load_active[engine_index] &&
                engine_slot_valid[engine_index] != 2'b11;

            dma_weight_ready_o[engine_index] =
                node_state == NODE_RUN && engine_load_active[engine_index];

            engine_weight_write_enable[engine_index] =
                dma_weight_valid_i[engine_index] &&
                dma_weight_ready_o[engine_index];
        end
    end

    // An engine's work is done only when it is idle, has no block still loading,
    // and has no completed J block waiting in either buffer slot.
    always_comb begin
        for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
            engine_work_done[engine_index] =
                engine_state[engine_index] == ENGINE_IDLE &&
                engine_slot_valid[engine_index] == 2'b00 &&
                !engine_load_active[engine_index];
        end
    end

    assign all_engine_work_done = &engine_work_done;
    assign node_work_done = schedule_done_pending && all_engine_work_done;

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
                    if (engine_done[engine_index])
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

    // Node control, state-table load, and completion-pulse latch.
    always_ff @(posedge clk) begin
        if (rst) begin
            node_state <= NODE_RESET;
            schedule_done_pending <= 1'b0;
        end
        else begin
            node_state <= node_state_n;

            if (state_valid_i && state_ready_o)
                state_mem[state_index_i] <= state_data_i;

            if (iter_start) begin
                schedule_done_pending <= 1'b0;
            end
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
                    engine_slot_state_a_index[engine_index] <= '{default: '0};
                    engine_slot_state_b_index[engine_index] <= '{default: '0};
                    engine_slot_block_a[engine_index] <= '{default: '0};
                    engine_slot_block_b[engine_index] <= '{default: '0};
                end
                else begin
                    engine_state[engine_index] <= engine_state_n[engine_index];

                    // Reserve a free J-buffer slot and remember its two core
                    // destinations before accepting any weight beats.
                    if (dma_cmd_valid_i[engine_index] && dma_cmd_ready_o[engine_index]) begin
                        if (!engine_slot_valid[engine_index][0]) begin
                            engine_load_slot[engine_index] <= 1'b0;
                            engine_slot_state_a_index[engine_index][0] <=
                                dma_state_a_index_i[engine_index];
                            engine_slot_state_b_index[engine_index][0] <=
                                dma_state_b_index_i[engine_index];
                            engine_slot_block_a[engine_index][0] <= dma_block_a_i[engine_index];
                            engine_slot_block_b[engine_index][0] <= dma_block_b_i[engine_index];
                        end
                        else begin
                            engine_load_slot[engine_index] <= 1'b1;
                            engine_slot_state_a_index[engine_index][1] <=
                                dma_state_a_index_i[engine_index];
                            engine_slot_state_b_index[engine_index][1] <=
                                dma_state_b_index_i[engine_index];
                            engine_slot_block_a[engine_index][1] <= dma_block_a_i[engine_index];
                            engine_slot_block_b[engine_index][1] <= dma_block_b_i[engine_index];
                        end

                        engine_load_active[engine_index] <= 1'b1;
                        engine_weight_beat[engine_index] <= '0;
                    end

                    // Track assembly of one complete row-major 32x32 J block.
                    // The accepted beat itself is written by j_block_sram.
                    if (dma_weight_valid_i[engine_index] && dma_weight_ready_o[engine_index]) begin
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

    // Hierarchy-node iteration state machine.
    always_comb begin
        node_state_n = node_state;

        unique case (node_state)
            NODE_RESET: begin
                node_state_n = NODE_IDLE;
            end
            NODE_IDLE: begin
                if (iter_start)
                    node_state_n = NODE_RUN;
            end
            NODE_RUN: begin
                if (node_work_done)
                    node_state_n = NODE_DONE;
            end
            NODE_DONE: begin
                if (iter_start)
                    node_state_n = NODE_RUN;
            end
            default: node_state_n = NODE_RESET;
        endcase
    end

endmodule
