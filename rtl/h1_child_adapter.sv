import ising_pkg::*;

// H1 child adapter.
//
// Routes partial packets produced by the H1 hierarchy node, plus the single
// partial stream arriving from the parent level, to the H0 tile containing the
// addressed 32-spin block. Each H0 has one external-partial input, so a
// fixed-priority arbiter selects one source per child and locks that selection
// for the complete fixed-length partial packet.
//
// Source IDs 0..MVM_COUNT-1 identify H1-local engines. Source MVM_COUNT is the
// single parent stream. Parent packets have no explicit `last`, so this module
// derives their tail from the fixed four-flit partial length.
module h1_child_adapter #(
    parameter int CHILD_COUNT       = 16,
    parameter int BLOCKS_PER_CHILD  = 32,
    parameter int MVM_COUNT         = 16,
    parameter int GLOBAL_BLOCK_ID_W = 16,
    parameter int BASE_BLOCK_ID     = 0
) (
    input logic clk,
    input logic rst,

    input  logic [MVM_COUNT-1:0]                        node_partial_valid_i,
    output logic [MVM_COUNT-1:0]                        node_partial_ready_o,
    input  logic signed [MVM_COUNT-1:0][DATA_W-1:0]     node_partial_data_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] node_partial_block_id_i,
    input  logic [MVM_COUNT-1:0]                        node_partial_last_i,

    // Parent packets also contain SPIN_COUNT ACC_W-bit values. Packet framing
    // is implicit: every packet contains PARTIAL_BEATS accepted transfers.
    input  logic                                         parent_partial_valid_i,
    output logic                                         parent_partial_ready_o,
    input  logic signed [DATA_W-1:0]                     parent_partial_data_i,
    input  logic [GLOBAL_BLOCK_ID_W-1:0]                 parent_partial_block_id_i,

    output logic [CHILD_COUNT-1:0]                       child_partial_valid_o,
    input  logic [CHILD_COUNT-1:0]                       child_partial_ready_i,
    output logic signed [CHILD_COUNT-1:0][DATA_W-1:0]    child_partial_data_o,
    output logic [CHILD_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                         child_partial_block_id_o
);
    localparam int SOURCE_COUNT = MVM_COUNT + 1;
    localparam int SOURCE_ID_W = (SOURCE_COUNT > 1) ? $clog2(SOURCE_COUNT) : 1;
    localparam int ENGINE_ID_W = (MVM_COUNT > 1) ? $clog2(MVM_COUNT) : 1;
    localparam int PARTIAL_BEATS = SPIN_COUNT * ACC_W / DATA_W;
    localparam int PARENT_BEAT_W = (PARTIAL_BEATS > 1) ? $clog2(PARTIAL_BEATS) : 1;

    logic [CHILD_COUNT-1:0] output_locked;
    logic [CHILD_COUNT-1:0][SOURCE_ID_W-1:0] locked_source;
    logic [CHILD_COUNT-1:0][SOURCE_ID_W-1:0] selected_source;
    logic [CHILD_COUNT-1:0] selected_valid;
    logic [CHILD_COUNT-1:0] selected_last;
    logic [PARENT_BEAT_W-1:0] parent_partial_beat;

    function automatic logic block_belongs_to_child(
        input int child_index,
        input logic [GLOBAL_BLOCK_ID_W-1:0] block_id
    );
        int unsigned first_block;
        int unsigned final_block;
        int unsigned decoded_block;
        begin
            first_block = BASE_BLOCK_ID + child_index * BLOCKS_PER_CHILD;
            final_block = first_block + BLOCKS_PER_CHILD;
            decoded_block = int'(block_id);
            block_belongs_to_child =
                decoded_block >= first_block && decoded_block < final_block;
        end
    endfunction

    always_comb begin
        child_partial_valid_o = '0;
        child_partial_data_o = '0;
        child_partial_block_id_o = '0;
        selected_source = '0;
        selected_valid = '0;
        selected_last = '0;

        for (int child_index = 0; child_index < CHILD_COUNT; child_index++) begin
            logic found_source;
            found_source = 1'b0;

            if (output_locked[child_index]) begin
                selected_source[child_index] = locked_source[child_index];

                if (locked_source[child_index] == SOURCE_ID_W'(MVM_COUNT)) begin
                    selected_valid[child_index] = parent_partial_valid_i;
                    selected_last[child_index] =
                        parent_partial_beat == PARENT_BEAT_W'(PARTIAL_BEATS-1);
                    child_partial_valid_o[child_index] = parent_partial_valid_i;
                    child_partial_data_o[child_index] = parent_partial_data_i;
                    child_partial_block_id_o[child_index] = parent_partial_block_id_i;
                end
                else begin
                    selected_valid[child_index] =
                        node_partial_valid_i[locked_source[child_index][ENGINE_ID_W-1:0]];
                    selected_last[child_index] =
                        node_partial_last_i[locked_source[child_index][ENGINE_ID_W-1:0]];
                    child_partial_valid_o[child_index] = selected_valid[child_index];
                    child_partial_data_o[child_index] =
                        node_partial_data_i[locked_source[child_index][ENGINE_ID_W-1:0]];
                    child_partial_block_id_o[child_index] =
                        node_partial_block_id_i[locked_source[child_index][ENGINE_ID_W-1:0]];
                end
            end
            else begin
                // H1-local results have priority. This keeps the hierarchy
                // node draining; the lock prevents starvation within a packet.
                for (int engine_index = 0; engine_index < MVM_COUNT; engine_index++) begin
                    if (!found_source && node_partial_valid_i[engine_index] &&
                        block_belongs_to_child(child_index,
                            node_partial_block_id_i[engine_index])) begin
                        selected_source[child_index] = SOURCE_ID_W'(engine_index);
                        selected_valid[child_index] = 1'b1;
                        selected_last[child_index] = node_partial_last_i[engine_index];
                        child_partial_valid_o[child_index] = 1'b1;
                        child_partial_data_o[child_index] =
                            node_partial_data_i[engine_index];
                        child_partial_block_id_o[child_index] =
                            node_partial_block_id_i[engine_index];
                        found_source = 1'b1;
                    end
                end

                if (!found_source && parent_partial_valid_i &&
                    block_belongs_to_child(child_index, parent_partial_block_id_i)) begin
                    selected_source[child_index] = SOURCE_ID_W'(MVM_COUNT);
                    selected_valid[child_index] = 1'b1;
                    selected_last[child_index] =
                        parent_partial_beat == PARENT_BEAT_W'(PARTIAL_BEATS-1);
                    child_partial_valid_o[child_index] = 1'b1;
                    child_partial_data_o[child_index] = parent_partial_data_i;
                    child_partial_block_id_o[child_index] = parent_partial_block_id_i;
                end
            end
        end
    end

    // Ready propagation is kept separate from route selection. This avoids a
    // combinational ready-to-route-to-ready path when an H0 destination
    // decodes child_partial_block_id_o to generate its ready response.
    always_comb begin
        node_partial_ready_o = '0;
        parent_partial_ready_o = 1'b0;

        for (int child_index = 0; child_index < CHILD_COUNT; child_index++) begin
            if (selected_valid[child_index]) begin
                if (selected_source[child_index] == SOURCE_ID_W'(MVM_COUNT))
                    parent_partial_ready_o = child_partial_ready_i[child_index];
                else
                    node_partial_ready_o
                        [selected_source[child_index][ENGINE_ID_W-1:0]] =
                            child_partial_ready_i[child_index];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            output_locked <= '0;
            locked_source <= '0;
            parent_partial_beat <= '0;
        end
        else begin
            if (parent_partial_valid_i && parent_partial_ready_o) begin
                if (parent_partial_beat == PARENT_BEAT_W'(PARTIAL_BEATS-1))
                    parent_partial_beat <= '0;
                else
                    parent_partial_beat <= parent_partial_beat + 1'b1;
            end

            for (int child_index = 0; child_index < CHILD_COUNT; child_index++) begin
                if (!output_locked[child_index] && selected_valid[child_index] &&
                    child_partial_ready_i[child_index] &&
                    !selected_last[child_index]) begin
                    output_locked[child_index] <= 1'b1;
                    locked_source[child_index] <= selected_source[child_index];
                end
                else if (output_locked[child_index] &&
                         selected_valid[child_index] &&
                         child_partial_ready_i[child_index] &&
                         selected_last[child_index]) begin
                    output_locked[child_index] <= 1'b0;
                end
            end
        end
    end
endmodule
