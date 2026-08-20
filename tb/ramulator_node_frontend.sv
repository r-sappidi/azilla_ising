import ising_pkg::*;

// Simulation integration wrapper: synthesizable tagged streamer plus one
// simulation-only Ramulator bridge representing a node-local memory interface.
module ramulator_node_frontend #(
    parameter int SYSTEM_ID = 0,
    parameter int MVM_COUNT = 1,
    parameter int STATE_INDEX_W = 1,
    parameter int GLOBAL_BLOCK_ID_W = 1,
    parameter int TOTAL_BLOCK_COUNT = 1,
    parameter int MEM_LANES = 16,
    parameter int MAX_OUTSTANDING = 64
) (
    input logic clk,
    input logic rst,

    input  logic [MVM_COUNT-1:0] sched_cmd_valid_i,
    output logic [MVM_COUNT-1:0] sched_cmd_ready_o,
    input  logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0] sched_state_a_index_i,
    input  logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0] sched_state_b_index_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] sched_block_a_id_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] sched_block_b_id_i,

    output logic [MVM_COUNT-1:0] node_cmd_valid_o,
    input  logic [MVM_COUNT-1:0] node_cmd_ready_i,
    output logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0] node_state_a_index_o,
    output logic [MVM_COUNT-1:0][STATE_INDEX_W-1:0] node_state_b_index_o,
    output logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] node_block_a_id_o,
    output logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] node_block_b_id_o,
    output logic [MVM_COUNT-1:0] node_weight_valid_o,
    input  logic [MVM_COUNT-1:0] node_weight_ready_i,
    output logic [MVM_COUNT-1:0][DATA_W-1:0] node_weight_data_o,
    output logic idle_o
);
    localparam int TAG_W =
        ((MVM_COUNT > 1) ? $clog2(MVM_COUNT) : 1) + 1 + $clog2(SPIN_COUNT);

    logic [MEM_LANES-1:0] mem_req_valid, mem_req_ready;
    logic [MEM_LANES-1:0][63:0] mem_req_addr;
    logic [MEM_LANES-1:0][TAG_W-1:0] mem_req_tag;
    logic [MEM_LANES-1:0] mem_rsp_valid, mem_rsp_ready;
    logic [MEM_LANES-1:0][DATA_W-1:0] mem_rsp_data;
    logic [MEM_LANES-1:0][TAG_W-1:0] mem_rsp_tag;
    logic [$clog2(2*MVM_COUNT*SPIN_COUNT+1)-1:0] outstanding;

    dram_weight_streamer #(
        .MVM_COUNT(MVM_COUNT), .STATE_INDEX_W(STATE_INDEX_W),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .TOTAL_BLOCK_COUNT(TOTAL_BLOCK_COUNT),
        .MEM_REQ_LANES(MEM_LANES), .MEM_RSP_LANES(MEM_LANES),
        .MAX_OUTSTANDING(MAX_OUTSTANDING)
    ) streamer (
        .clk, .rst,
        .sched_cmd_valid_i, .sched_cmd_ready_o,
        .sched_state_a_index_i, .sched_state_b_index_i,
        .sched_block_a_id_i, .sched_block_b_id_i,
        .node_cmd_valid_o, .node_cmd_ready_i,
        .node_state_a_index_o, .node_state_b_index_o,
        .node_block_a_id_o, .node_block_b_id_o,
        .node_weight_valid_o, .node_weight_ready_i, .node_weight_data_o,
        .mem_req_valid_o(mem_req_valid), .mem_req_ready_i(mem_req_ready),
        .mem_req_addr_o(mem_req_addr), .mem_req_tag_o(mem_req_tag),
        .mem_rsp_valid_i(mem_rsp_valid), .mem_rsp_ready_o(mem_rsp_ready),
        .mem_rsp_data_i(mem_rsp_data), .mem_rsp_tag_i(mem_rsp_tag),
        .outstanding_o(outstanding), .idle_o
    );

    ramulator_dpi_bridge #(
        .SYSTEM_ID(SYSTEM_ID), .REQ_LANES(MEM_LANES),
        .RSP_LANES(MEM_LANES), .TAG_W(TAG_W)
    ) bridge (
        .clk, .rst,
        .req_valid_i(mem_req_valid), .req_ready_o(mem_req_ready),
        .req_addr_i(mem_req_addr), .req_tag_i(mem_req_tag),
        .rsp_valid_o(mem_rsp_valid), .rsp_ready_i(mem_rsp_ready),
        .rsp_data_o(mem_rsp_data), .rsp_tag_o(mem_rsp_tag)
    );
endmodule
