import ising_pkg::*;

// H0 uplink (CIR with H0-anchored symmetric ownership).
//
// Every off-H0 block is evaluated once by the owning H0's symmetric engines.
// The partial addressed to a block outside this H0 leaves through this
// module instead of the local H0 adapter.  Cycle-level contract of
// model/cpp_exact/anchored.cpp (H0Uplink):
//
// Input side: round-robin, packet-locked selection among the engines whose
// current partial addresses a block outside [BASE_BLOCK_ID,
// BASE_BLOCK_ID+BLOCKS_PER_H0).  One beat per cycle.
//   - ACCUMULATE=1: the beat is summed into a per-remote-block accumulator
//     (SPIN_COUNT x ACC_W per block, one read-modify-write beat per cycle,
//     never stalls) and the block is marked touched.  When the H0 hierarchy
//     node is done, a flush walks the touched blocks in ascending block order
//     and emits one PARTIAL_BEATS-beat packet per block into the FIFOs, one
//     beat per cycle when the destination FIFO has room (room is evaluated
//     after this cycle's pop).
//   - ACCUMULATE=0: the beat enters the destination FIFO directly; the engine
//     is accepted only when that FIFO has room before this cycle's pop.
// Two FIFOs of FIFO_PACKETS packets each: queue 0 for sibling H0s of this H1
// (drained through the H1 child crossbar), queue 1 for other H1s (drained
// through the mesh injection arbiter).  One beat per cycle leaves: the output
// locks on the queue of the packet in flight and, between packets, moves to
// the other queue whenever the presented head is not accepted.
//
// The accumulator is a register file with combinational read so that the
// flush delivers one beat per cycle with no read latency, as the model
// assumes; a synchronous SRAM would need one pipeline stage here.
module h0_uplink #(
    parameter int MVM_COUNT         = 4,
    parameter int GLOBAL_BLOCK_ID_W = 16,
    parameter int TOTAL_BLOCK_COUNT = 64,
    parameter int BLOCKS_PER_H0     = 32,
    parameter int BASE_BLOCK_ID     = 0,
    parameter int BLOCKS_PER_H1     = 512,
    parameter int NODE_ID           = 0,
    parameter int MESH_X_COUNT      = 8,
    parameter int X_W               = 3,
    parameter int Y_W               = 3,
    parameter int SOURCE_ID_W       = 8,
    parameter int EPOCH_W           = 16,
    parameter int FIFO_PACKETS      = 4,
    parameter bit ACCUMULATE        = 1'b1
) (
    input logic clk,
    input logic rst,

    input  logic                                         iter_start_i,
    input  logic                                         node_done_i,
    input  logic [EPOCH_W-1:0]                           epoch_i,

    // Engine partial streams (shared with the H0 adapter; only remote
    // partials are consumed here).
    input  logic [MVM_COUNT-1:0]                         partial_valid_i,
    output logic [MVM_COUNT-1:0]                         partial_ready_o,
    input  logic signed [MVM_COUNT-1:0][DATA_W-1:0]      partial_data_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]  partial_block_id_i,
    input  logic [MVM_COUNT-1:0]                         partial_last_i,

    // Sibling-bound partial (one source of the H1 child crossbar).
    output logic                                         sib_valid_o,
    input  logic                                         sib_ready_i,
    output logic signed [DATA_W-1:0]                     sib_data_o,
    output logic [GLOBAL_BLOCK_ID_W-1:0]                 sib_block_id_o,
    output logic                                         sib_last_o,

    // Mesh-bound partial flit (one source of the uplink injection arbiter).
    output logic                                         mesh_valid_o,
    input  logic                                         mesh_ready_i,
    output logic [DATA_W-1:0]                            mesh_data_o,
    output logic [1:0]                                   mesh_type_o,
    output logic [X_W-1:0]                               mesh_dest_x_o,
    output logic [Y_W-1:0]                               mesh_dest_y_o,
    output logic [SOURCE_ID_W-1:0]                       mesh_source_id_o,
    output logic [EPOCH_W-1:0]                           mesh_epoch_o,
    output logic [GLOBAL_BLOCK_ID_W-1:0]                 mesh_block_id_o,
    output logic                                         mesh_last_o,

    // ACCUMULATE=1: the node is done, every touched block has been flushed and
    // both FIFOs are empty.  ACCUMULATE=0: the node is done and both FIFOs
    // are empty.
    output logic                                         flushed_o
);
    localparam int PARTIAL_BEATS = SPIN_COUNT * ACC_W / DATA_W;
    localparam int PARTIAL_BEAT_W = (PARTIAL_BEATS > 1) ? $clog2(PARTIAL_BEATS) : 1;
    localparam int PARTIAL_LANES = DATA_W / ACC_W;
    localparam int ENGINE_ID_W = (MVM_COUNT > 1) ? $clog2(MVM_COUNT) : 1;
    localparam int FIFO_DEPTH = FIFO_PACKETS * PARTIAL_BEATS;
    localparam int FIFO_PTR_W = (FIFO_DEPTH > 1) ? $clog2(FIFO_DEPTH) : 1;
    localparam int FIFO_COUNT_W = $clog2(FIFO_DEPTH + 1);
    localparam int CURSOR_W = $clog2(TOTAL_BLOCK_COUNT + 1);

    function automatic bit block_is_remote(input logic [GLOBAL_BLOCK_ID_W-1:0] block_id);
        int unsigned decoded;
        decoded = int'(block_id);
        return !(decoded >= BASE_BLOCK_ID && decoded < BASE_BLOCK_ID + BLOCKS_PER_H0);
    endfunction

    // Queue 0: destination block lives in this H1.  Queue 1: another H1.
    function automatic bit block_queue(input int unsigned block_id);
        return (block_id / BLOCKS_PER_H1) != NODE_ID;
    endfunction

    // ------------------------------------------------------------------
    // Input selection
    // ------------------------------------------------------------------
    logic input_locked;
    logic [ENGINE_ID_W-1:0] locked_engine;
    logic [ENGINE_ID_W-1:0] round_robin_start;
    logic [ENGINE_ID_W-1:0] selected_engine;
    logic selected_valid;
    logic selected_queue;
    logic selected_ready;
    logic input_accepted;
    logic [PARTIAL_BEAT_W-1:0] input_beat;
    logic packet_fresh;

    // ------------------------------------------------------------------
    // FIFOs: [0] sibling, [1] mesh
    // ------------------------------------------------------------------
    logic signed [DATA_W-1:0] fifo_data [0:1][0:FIFO_DEPTH-1];
    logic [GLOBAL_BLOCK_ID_W-1:0] fifo_block [0:1][0:FIFO_DEPTH-1];
    logic fifo_last [0:1][0:FIFO_DEPTH-1];
    logic [FIFO_PTR_W-1:0] fifo_rd [0:1];
    logic [FIFO_PTR_W-1:0] fifo_wr [0:1];
    logic [FIFO_COUNT_W-1:0] fifo_count [0:1];
    logic [1:0] fifo_empty;
    logic [1:0] fifo_pop;
    logic [1:0] fifo_push;
    logic signed [DATA_W-1:0] fifo_push_data;
    logic [GLOBAL_BLOCK_ID_W-1:0] fifo_push_block;
    logic fifo_push_last;

    // ------------------------------------------------------------------
    // Output selection
    // ------------------------------------------------------------------
    logic out_queue;
    logic out_locked;
    logic head_queue;
    logic head_valid;
    logic signed [DATA_W-1:0] head_data;
    logic [GLOBAL_BLOCK_ID_W-1:0] head_block;
    logic head_last;
    logic output_accepted;
    int unsigned head_destination;

    // ------------------------------------------------------------------
    // Accumulate mode: per-block accumulator, touched table, flush cursor
    // ------------------------------------------------------------------
    logic signed [ACC_W-1:0] accumulator [0:TOTAL_BLOCK_COUNT-1][0:SPIN_COUNT-1];
    logic [TOTAL_BLOCK_COUNT-1:0] touched;
    logic [CURSOR_W-1:0] flush_cursor;
    logic [PARTIAL_BEAT_W-1:0] flush_beat;
    logic flush_found;
    logic [CURSOR_W-1:0] flush_block;
    logic flush_queue;
    logic flush_push;
    logic signed [DATA_W-1:0] flush_data;

    // Engine selection: locked engine while its remote packet is in flight,
    // otherwise the first engine with a remote partial from the round-robin
    // pointer.
    always_comb begin
        logic found;
        int candidate;
        found = 1'b0;
        selected_engine = locked_engine;
        selected_valid = 1'b0;
        if (input_locked) begin
            selected_valid = partial_valid_i[locked_engine] &&
                block_is_remote(partial_block_id_i[locked_engine]);
        end
        else begin
            for (int offset = 0; offset < MVM_COUNT; offset++) begin
                candidate = (int'(round_robin_start) + offset) % MVM_COUNT;
                if (!found && partial_valid_i[candidate] &&
                    block_is_remote(partial_block_id_i[candidate])) begin
                    selected_engine = ENGINE_ID_W'(candidate);
                    selected_valid = 1'b1;
                    found = 1'b1;
                end
            end
        end
        selected_queue = block_queue(int'(partial_block_id_i[selected_engine]));
        // Accumulate: the read-modify-write port takes one beat per cycle.
        // FIFO mode: room is judged before this cycle's pop.
        selected_ready = ACCUMULATE ? 1'b1 :
            (fifo_count[selected_queue] < FIFO_COUNT_W'(FIFO_DEPTH));
        input_accepted = selected_valid && selected_ready;
        partial_ready_o = '0;
        if (selected_valid)
            partial_ready_o[selected_engine] = selected_ready;
    end

    // Output head: stay on the locked queue; otherwise fall through to the
    // other queue when the preferred one is empty.
    always_comb begin
        fifo_empty[0] = fifo_count[0] == '0;
        fifo_empty[1] = fifo_count[1] == '0;
        head_queue = out_queue;
        if (!out_locked && fifo_empty[out_queue])
            head_queue = ~out_queue;
        head_valid = !fifo_empty[head_queue];
        head_data = fifo_data[head_queue][fifo_rd[head_queue]];
        head_block = fifo_block[head_queue][fifo_rd[head_queue]];
        head_last = fifo_last[head_queue][fifo_rd[head_queue]];
        head_destination = int'(head_block) / BLOCKS_PER_H1;

        sib_valid_o = head_valid && head_queue == 1'b0;
        sib_data_o = head_data;
        sib_block_id_o = head_block;
        sib_last_o = head_last;

        mesh_valid_o = head_valid && head_queue == 1'b1;
        mesh_data_o = head_data;
        mesh_type_o = NOC_PARTIAL;
        mesh_dest_x_o = X_W'(head_destination % MESH_X_COUNT);
        mesh_dest_y_o = Y_W'(head_destination / MESH_X_COUNT);
        mesh_source_id_o = SOURCE_ID_W'(NODE_ID);
        mesh_epoch_o = epoch_i;
        mesh_block_id_o = head_block;
        mesh_last_o = head_last;

        output_accepted = head_valid &&
            (head_queue == 1'b0 ? sib_ready_i : mesh_ready_i);
        fifo_pop = '0;
        if (output_accepted)
            fifo_pop[head_queue] = 1'b1;
    end

    // Flush: next touched block at or above the cursor (ascending order).
    always_comb begin
        flush_found = 1'b0;
        flush_block = '0;
        for (int block = 0; block < TOTAL_BLOCK_COUNT; block++) begin
            if (!flush_found && touched[block] &&
                CURSOR_W'(block) >= flush_cursor) begin
                flush_found = 1'b1;
                flush_block = CURSOR_W'(block);
            end
        end
        flush_queue = block_queue(int'(flush_block));
        // Room is judged after this cycle's pop (model: pop, then flush push).
        flush_push = ACCUMULATE && node_done_i && flush_found &&
            ((fifo_count[flush_queue] - FIFO_COUNT_W'(fifo_pop[flush_queue])) <
             FIFO_COUNT_W'(FIFO_DEPTH));
        for (int lane = 0; lane < PARTIAL_LANES; lane++)
            flush_data[lane*ACC_W +: ACC_W] =
                accumulator[flush_block][int'(flush_beat)*PARTIAL_LANES + lane];
    end

    // FIFO write source: flush beat (accumulate) or engine beat (FIFO mode).
    always_comb begin
        fifo_push = '0;
        if (ACCUMULATE) begin
            fifo_push_data = flush_data;
            fifo_push_block = GLOBAL_BLOCK_ID_W'(flush_block);
            fifo_push_last = flush_beat == PARTIAL_BEAT_W'(PARTIAL_BEATS-1);
            if (flush_push)
                fifo_push[flush_queue] = 1'b1;
        end
        else begin
            fifo_push_data = partial_data_i[selected_engine];
            fifo_push_block = partial_block_id_i[selected_engine];
            fifo_push_last = partial_last_i[selected_engine];
            if (input_accepted)
                fifo_push[selected_queue] = 1'b1;
        end
    end

    assign flushed_o = node_done_i && fifo_empty[0] && fifo_empty[1] &&
                       (!ACCUMULATE || !flush_found);

    // ------------------------------------------------------------------
    // Sequential state
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            input_locked <= 1'b0;
            locked_engine <= '0;
            round_robin_start <= '0;
            input_beat <= '0;
            packet_fresh <= 1'b0;
            out_queue <= 1'b0;
            out_locked <= 1'b0;
            fifo_rd <= '{default: '0};
            fifo_wr <= '{default: '0};
            fifo_count <= '{default: '0};
            touched <= '0;
            flush_cursor <= '0;
            flush_beat <= '0;
        end
        else begin
            if (iter_start_i) begin
                touched <= '0;
                flush_cursor <= '0;
                flush_beat <= '0;
            end

            // Input acceptance and packet lock.
            if (input_accepted) begin
                if (partial_last_i[selected_engine])
                    input_beat <= '0;
                else
                    input_beat <= input_beat + 1'b1;
                if (input_locked) begin
                    if (partial_last_i[selected_engine]) begin
                        input_locked <= 1'b0;
                        round_robin_start <= ENGINE_ID_W'(
                            (int'(selected_engine) + 1) % MVM_COUNT);
                    end
                end
                else if (!partial_last_i[selected_engine]) begin
                    input_locked <= 1'b1;
                    locked_engine <= selected_engine;
                end
                else begin
                    round_robin_start <= ENGINE_ID_W'(
                        (int'(selected_engine) + 1) % MVM_COUNT);
                end
                if (ACCUMULATE) begin
                    logic fresh;
                    int unsigned block;
                    block = int'(partial_block_id_i[selected_engine]);
                    fresh = (input_beat == '0) ? !touched[block] : packet_fresh;
                    if (input_beat == '0)
                        packet_fresh <= !touched[block];
                    touched[block] <= 1'b1;
                    for (int lane = 0; lane < PARTIAL_LANES; lane++) begin
                        accumulator[block][int'(input_beat)*PARTIAL_LANES + lane] <=
                            (fresh ? ACC_W'(0) :
                                     accumulator[block][int'(input_beat)*PARTIAL_LANES + lane]) +
                            $signed(partial_data_i[selected_engine][lane*ACC_W +: ACC_W]);
                    end
                end
            end

            // Flush progress.
            if (flush_push) begin
                if (flush_beat == PARTIAL_BEAT_W'(PARTIAL_BEATS-1)) begin
                    flush_beat <= '0;
                    flush_cursor <= flush_block + 1'b1;
                end
                else begin
                    flush_beat <= flush_beat + 1'b1;
                end
            end

            // Output queue lock.
            if (head_valid) begin
                if (output_accepted) begin
                    if (head_last) begin
                        out_locked <= 1'b0;
                        out_queue <= ~head_queue;
                    end
                    else begin
                        out_locked <= 1'b1;
                        out_queue <= head_queue;
                    end
                end
                else if (!out_locked) begin
                    out_queue <= ~head_queue;
                end
            end

            // FIFO pointers.
            for (int queue = 0; queue < 2; queue++) begin
                if (fifo_push[queue]) begin
                    fifo_data[queue][fifo_wr[queue]] <= fifo_push_data;
                    fifo_block[queue][fifo_wr[queue]] <= fifo_push_block;
                    fifo_last[queue][fifo_wr[queue]] <= fifo_push_last;
                    fifo_wr[queue] <= (fifo_wr[queue] == FIFO_PTR_W'(FIFO_DEPTH-1)) ?
                        '0 : fifo_wr[queue] + 1'b1;
                end
                if (fifo_pop[queue])
                    fifo_rd[queue] <= (fifo_rd[queue] == FIFO_PTR_W'(FIFO_DEPTH-1)) ?
                        '0 : fifo_rd[queue] + 1'b1;
                fifo_count[queue] <= fifo_count[queue] +
                    FIFO_COUNT_W'(fifo_push[queue]) - FIFO_COUNT_W'(fifo_pop[queue]);
            end
        end
    end
endmodule
