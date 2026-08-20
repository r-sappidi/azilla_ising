import ising_pkg::*;

// Converts scheduled 32x32 J-block commands into tagged native DRAM reads.
//
// Each MVM engine owns two streamer-side buffers.  The streamer may receive
// responses in any order; {engine, buffer, beat} in the returned tag selects
// the exact storage row.  A completed block is then presented, in schedule
// order, to the existing hierarchy-node command and weight-stream ports.
//
// Per-engine flow:
//
//   schedule command -> tail buffer -> 32 tagged reads
//                    -> out-of-order reassembly -> head buffer
//                    -> node command -> 32 ordered weight beats
//
// `engine_head` preserves schedule order toward the hierarchy node, while
// `engine_tail` permits allocation of the following block. Memory responses
// need not be ordered because their tags select the exact engine/buffer/beat.
module dram_weight_streamer #(
    parameter int MVM_COUNT          = 16,
    parameter int STATE_INDEX_W      = 5,
    parameter int GLOBAL_BLOCK_ID_W  = 16,
    parameter int TOTAL_BLOCK_COUNT  = 32768,
    parameter int MEM_REQ_LANES      = 16,
    parameter int MEM_RSP_LANES      = 16,
    parameter int MEM_ADDR_W         = 64,
    parameter int MAX_OUTSTANDING    = 64
) (
    input logic clk,
    input logic rst,

    // Testbench/compiler schedule input.  The selected array element is the
    // runtime engine ID; the streamer chooses that engine's free buffer.
    input  logic [MVM_COUNT-1:0]                         sched_cmd_valid_i,
    output logic [MVM_COUNT-1:0]                         sched_cmd_ready_o,
    input  logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0]      sched_state_a_index_i,
    input  logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0]      sched_state_b_index_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] sched_block_a_id_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] sched_block_b_id_i,

    // Existing hierarchy-node DMA-side command and weight streams.
    output logic [MVM_COUNT-1:0]                         node_cmd_valid_o,
    input  logic [MVM_COUNT-1:0]                         node_cmd_ready_i,
    output logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0]      node_state_a_index_o,
    output logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0]      node_state_b_index_o,
    output logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] node_block_a_id_o,
    output logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] node_block_b_id_o,
    output logic [MVM_COUNT-1:0]                         node_weight_valid_o,
    input  logic [MVM_COUNT-1:0]                         node_weight_ready_i,
    output logic [MVM_COUNT-1:0][DATA_W-1:0]             node_weight_data_o,

    // Native memory transactions.  For the Ramulator GDDR6 organization one
    // transaction is 32 bytes, exactly one 256-bit J row.
    output logic [MEM_REQ_LANES-1:0]                     mem_req_valid_o,
    input  logic [MEM_REQ_LANES-1:0]                     mem_req_ready_i,
    output logic [MEM_REQ_LANES-1:0][MEM_ADDR_W-1:0]     mem_req_addr_o,
    output logic [MEM_REQ_LANES-1:0]
                 [((MVM_COUNT > 1) ? $clog2(MVM_COUNT) : 1)+1+
                  $clog2(SPIN_COUNT)-1:0]                mem_req_tag_o,

    input  logic [MEM_RSP_LANES-1:0]                     mem_rsp_valid_i,
    output logic [MEM_RSP_LANES-1:0]                     mem_rsp_ready_o,
    input  logic [MEM_RSP_LANES-1:0][DATA_W-1:0]         mem_rsp_data_i,
    input  logic [MEM_RSP_LANES-1:0]
                 [((MVM_COUNT > 1) ? $clog2(MVM_COUNT) : 1)+1+
                  $clog2(SPIN_COUNT)-1:0]                mem_rsp_tag_i,

    output logic [$clog2(2*MVM_COUNT*SPIN_COUNT+1)-1:0] outstanding_o,
    output logic idle_o
);
    localparam int ENGINE_ID_W = (MVM_COUNT > 1) ? $clog2(MVM_COUNT) : 1;
    localparam int BEAT_ID_W = $clog2(SPIN_COUNT);
    localparam int BLOCK_BYTES = SPIN_COUNT * SPIN_COUNT * WEIGHT_W / 8;
    localparam int TX_BYTES = DATA_W / 8;
    localparam int OUTSTANDING_W = $clog2(2*MVM_COUNT*SPIN_COUNT+1);

    // Two reassembly slots per engine. State is stored as a two-bit vector so
    // each slot can progress independently through request, response, and
    // hierarchy-node delivery phases.
    logic [1:0] slot_allocated [0:MVM_COUNT-1];
    logic [1:0] slot_requests_done [0:MVM_COUNT-1];
    logic [1:0] slot_command_sent [0:MVM_COUNT-1];
    logic engine_head [0:MVM_COUNT-1];
    logic engine_tail [0:MVM_COUNT-1];

    logic [STATE_INDEX_W-1:0] slot_state_a [0:MVM_COUNT-1][0:1];
    logic [STATE_INDEX_W-1:0] slot_state_b [0:MVM_COUNT-1][0:1];
    logic [GLOBAL_BLOCK_ID_W-1:0] slot_block_a [0:MVM_COUNT-1][0:1];
    logic [GLOBAL_BLOCK_ID_W-1:0] slot_block_b [0:MVM_COUNT-1][0:1];
    logic [BEAT_ID_W:0] slot_request_count [0:MVM_COUNT-1][0:1];
    logic [SPIN_COUNT-1:0] slot_response_valid [0:MVM_COUNT-1][0:1];
    logic [BEAT_ID_W-1:0] slot_send_beat [0:MVM_COUNT-1][0:1];
    logic [DATA_W-1:0] slot_data [0:MVM_COUNT-1][0:1][0:SPIN_COUNT-1];

    logic [MEM_REQ_LANES-1:0][ENGINE_ID_W-1:0] selected_engine;
    logic [MEM_REQ_LANES-1:0] selected_buffer;
    logic [MEM_REQ_LANES-1:0][BEAT_ID_W-1:0] selected_beat;

    function automatic logic [MEM_ADDR_W-1:0] block_address(
        input logic [GLOBAL_BLOCK_ID_W-1:0] block_a,
        input logic [GLOBAL_BLOCK_ID_W-1:0] block_b,
        input logic [BEAT_ID_W-1:0] beat
    );
        longint unsigned low_block;
        longint unsigned high_block;
        longint unsigned block_number;
        low_block = (block_a < block_b) ? longint'(block_a) : longint'(block_b);
        high_block = (block_a < block_b) ? longint'(block_b) : longint'(block_a);
        // A simple rectangular global layout is used for simulation.  Only the
        // upper-triangular addresses are generated, so symmetric blocks are
        // still fetched exactly once.
        block_number = low_block * TOTAL_BLOCK_COUNT + high_block;
        return MEM_ADDR_W'(block_number * BLOCK_BYTES + beat * TX_BYTES);
    endfunction

    // Allocate one of the two buffers associated with the scheduler-selected
    // engine. Widths are static, while engine/buffer values are runtime state.
    always_comb begin
        sched_cmd_ready_o = '0;
        for (int engine = 0; engine < MVM_COUNT; engine++)
            sched_cmd_ready_o[engine] =
                !slot_allocated[engine][engine_tail[engine]];
    end

    // Select at most one new native transaction from each buffer per cycle.
    // Multiple lanes allow independent engines/buffers to feed a wide memory
    // interface concurrently.
    always_comb begin
        logic [2*MVM_COUNT-1:0] chosen;
        int lane;
        chosen = '0;
        lane = 0;
        mem_req_valid_o = '0;
        mem_req_addr_o = '0;
        mem_req_tag_o = '0;
        selected_engine = '0;
        selected_buffer = '0;
        selected_beat = '0;

        for (int engine = 0; engine < MVM_COUNT; engine++) begin
            for (int buffer = 0; buffer < 2; buffer++) begin
                if (lane < MEM_REQ_LANES &&
                    lane < (MAX_OUTSTANDING - int'(outstanding_o)) &&
                    slot_allocated[engine][buffer] &&
                    !slot_requests_done[engine][buffer] &&
                    !chosen[engine*2+buffer]) begin
                    mem_req_valid_o[lane] = 1'b1;
                    mem_req_addr_o[lane] = block_address(
                        slot_block_a[engine][buffer],
                        slot_block_b[engine][buffer],
                        BEAT_ID_W'(slot_request_count[engine][buffer]));
                    mem_req_tag_o[lane] = {
                        ENGINE_ID_W'(engine), 1'(buffer),
                        BEAT_ID_W'(slot_request_count[engine][buffer])};
                    selected_engine[lane] = ENGINE_ID_W'(engine);
                    selected_buffer[lane] = 1'(buffer);
                    selected_beat[lane] =
                        BEAT_ID_W'(slot_request_count[engine][buffer]);
                    chosen[engine*2+buffer] = 1'b1;
                    lane++;
                end
            end
        end
    end

    // The response tag always identifies writable storage, so each response
    // lane can be accepted independently.  Assertions below catch stale tags.
    assign mem_rsp_ready_o = '1;

    // Present only the per-engine head buffer to the hierarchy node.  Memory
    // may fill the following buffer first, but block execution remains ordered.
    always_comb begin
        node_cmd_valid_o = '0;
        node_state_a_index_o = '0;
        node_state_b_index_o = '0;
        node_block_a_id_o = '0;
        node_block_b_id_o = '0;
        node_weight_valid_o = '0;
        node_weight_data_o = '0;

        for (int engine = 0; engine < MVM_COUNT; engine++) begin
            logic buffer;
            buffer = engine_head[engine];
            node_state_a_index_o[engine] = slot_state_a[engine][buffer];
            node_state_b_index_o[engine] = slot_state_b[engine][buffer];
            node_block_a_id_o[engine] = slot_block_a[engine][buffer];
            node_block_b_id_o[engine] = slot_block_b[engine][buffer];

            if (slot_allocated[engine][buffer] &&
                &slot_response_valid[engine][buffer]) begin
                if (!slot_command_sent[engine][buffer])
                    node_cmd_valid_o[engine] = 1'b1;
                else begin
                    node_weight_valid_o[engine] = 1'b1;
                    node_weight_data_o[engine] =
                        slot_data[engine][buffer][slot_send_beat[engine][buffer]];
                end
            end
        end
    end

    // Outstanding is derived from accepted requests minus unique responses.
    always_comb begin
        outstanding_o = '0;
        for (int engine = 0; engine < MVM_COUNT; engine++)
            for (int buffer = 0; buffer < 2; buffer++)
                outstanding_o += OUTSTANDING_W'(
                    int'(slot_request_count[engine][buffer]) -
                    $countones(slot_response_valid[engine][buffer]));
    end

    // Idle means no allocated slot remains anywhere in the streamer. The
    // testbench uses this stronger condition before announcing schedule done.
    always_comb begin
        idle_o = 1'b1;
        for (int engine = 0; engine < MVM_COUNT; engine++)
            if (slot_allocated[engine] != 2'b00)
                idle_o = 1'b0;
    end

    // Allocation, native-request accounting, tagged response reassembly, and
    // ordered node delivery share one sequential process to make slot lifetime
    // explicit and to prevent conflicting ownership updates.
    always_ff @(posedge clk) begin
        if (rst) begin
            slot_allocated <= '{default: '0};
            slot_requests_done <= '{default: '0};
            slot_command_sent <= '{default: '0};
            engine_head <= '{default: '0};
            engine_tail <= '{default: '0};
            for (int engine = 0; engine < MVM_COUNT; engine++) begin
                for (int buffer = 0; buffer < 2; buffer++) begin
                    slot_request_count[engine][buffer] <= '0;
                    slot_response_valid[engine][buffer] <= '0;
                    slot_send_beat[engine][buffer] <= '0;
                end
            end
        end
        else begin
            for (int engine = 0; engine < MVM_COUNT; engine++) begin
                if (sched_cmd_valid_i[engine] && sched_cmd_ready_o[engine]) begin
                    logic buffer;
                    buffer = engine_tail[engine];
                    slot_allocated[engine][buffer] <= 1'b1;
                    slot_requests_done[engine][buffer] <= 1'b0;
                    slot_command_sent[engine][buffer] <= 1'b0;
                    slot_request_count[engine][buffer] <= '0;
                    slot_response_valid[engine][buffer] <= '0;
                    slot_send_beat[engine][buffer] <= '0;
                    slot_state_a[engine][buffer] <= sched_state_a_index_i[engine];
                    slot_state_b[engine][buffer] <= sched_state_b_index_i[engine];
                    slot_block_a[engine][buffer] <= sched_block_a_id_i[engine];
                    slot_block_b[engine][buffer] <= sched_block_b_id_i[engine];
                    engine_tail[engine] <= !engine_tail[engine];
                end
            end

            for (int lane = 0; lane < MEM_REQ_LANES; lane++) begin
                if (mem_req_valid_o[lane] && mem_req_ready_i[lane]) begin
                    int engine;
                    int buffer;
                    engine = int'(selected_engine[lane]);
                    buffer = int'(selected_buffer[lane]);
                    if (selected_beat[lane] == BEAT_ID_W'(SPIN_COUNT-1)) begin
                        slot_requests_done[engine][buffer] <= 1'b1;
                        slot_request_count[engine][buffer] <=
                            (BEAT_ID_W+1)'(SPIN_COUNT);
                    end
                    else
                        slot_request_count[engine][buffer] <=
                            slot_request_count[engine][buffer] + 1'b1;
                end
            end

            for (int lane = 0; lane < MEM_RSP_LANES; lane++) begin
                if (mem_rsp_valid_i[lane] && mem_rsp_ready_o[lane]) begin
                    int engine;
                    int buffer;
                    int beat;
                    engine = int'(mem_rsp_tag_i[lane]
                        [BEAT_ID_W+1 +: ENGINE_ID_W]);
                    buffer = int'(mem_rsp_tag_i[lane][BEAT_ID_W]);
                    beat = int'(mem_rsp_tag_i[lane][BEAT_ID_W-1:0]);
                    assert (engine < MVM_COUNT && buffer < 2 && beat < SPIN_COUNT)
                        else $fatal(1, "invalid DRAM response tag 0x%0h",
                                    mem_rsp_tag_i[lane]);
                    assert (slot_allocated[engine][buffer])
                        else $fatal(1, "stale DRAM response tag 0x%0h",
                                    mem_rsp_tag_i[lane]);
                    assert (!slot_response_valid[engine][buffer][beat])
                        else $fatal(1, "duplicate DRAM response tag 0x%0h",
                                    mem_rsp_tag_i[lane]);
                    slot_data[engine][buffer][beat] <= mem_rsp_data_i[lane];
                    slot_response_valid[engine][buffer][beat] <= 1'b1;
                end
            end

            for (int engine = 0; engine < MVM_COUNT; engine++) begin
                logic buffer;
                buffer = engine_head[engine];
                if (node_cmd_valid_o[engine] && node_cmd_ready_i[engine])
                    slot_command_sent[engine][buffer] <= 1'b1;

                if (node_weight_valid_o[engine] && node_weight_ready_i[engine]) begin
                    if (slot_send_beat[engine][buffer] ==
                        BEAT_ID_W'(SPIN_COUNT-1)) begin
                        slot_allocated[engine][buffer] <= 1'b0;
                        slot_requests_done[engine][buffer] <= 1'b0;
                        slot_command_sent[engine][buffer] <= 1'b0;
                        slot_request_count[engine][buffer] <= '0;
                        slot_response_valid[engine][buffer] <= '0;
                        slot_send_beat[engine][buffer] <= '0;
                        engine_head[engine] <= !engine_head[engine];
                    end
                    else
                        slot_send_beat[engine][buffer] <=
                            slot_send_beat[engine][buffer] + 1'b1;
                end
            end
        end
    end

    initial begin
        if (DATA_W != 256 || WEIGHT_W != 8 || SPIN_COUNT != 32)
            $fatal(1, "dram_weight_streamer currently requires 256-bit beats, int8 J, and 32-spin blocks");
        if (TOTAL_BLOCK_COUNT <= 0 || MEM_REQ_LANES <= 0 || MEM_RSP_LANES <= 0 ||
            MAX_OUTSTANDING <= 0 ||
            MAX_OUTSTANDING > 2*MVM_COUNT*SPIN_COUNT)
            $fatal(1, "invalid DRAM streamer geometry");
    end
endmodule
