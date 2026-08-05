import ising_pkg::*;

// Complete H0 tile: 32 spin cores, one generic hierarchy compute node, and an
// H0-local star adapter. Higher-level partials enter through a separate stream
// and are delivered directly to the addressed core's external accumulator.
module h0_tile #(
    parameter int CORE_COUNT        = 32,
    parameter int MVM_COUNT         = 16,
    parameter int GLOBAL_BLOCK_ID_W = 16,
    parameter int BASE_BLOCK_ID     = 0
) (
    input logic clk,
    input logic rst,

    input  logic                                      init_start,
    output logic                                      init_done,
    input  logic [CORE_COUNT-1:0]                     core_weight_valid_i,
    output logic [CORE_COUNT-1:0]                     core_weight_ready_o,
    input  logic [CORE_COUNT-1:0][DATA_W-1:0]         core_weight_data_i,
    input  logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]     init_state_i,
    input  logic [CORE_COUNT-1:0][31:0]               noise_seed_i,
    input  logic signed [COEFF_W-1:0]                 coeff_a_i,
    input  logic signed [COEFF_W-1:0]                 coeff_b_i,
    input  logic signed [COEFF_W-1:0]                 coeff_c_i,
    input  logic signed [COEFF_W-1:0]                 noise_amplitude_i,

    input  logic                                      iter_start,
    output logic                                      iter_done,
    input  logic                                      commit,
    input  logic                                      done,
    input  logic [16:0]                               noise_decay_i,

    input  logic                                      schedule_done_i,
    input  logic [MVM_COUNT-1:0]                     dma_cmd_valid_i,
    output logic [MVM_COUNT-1:0]                     dma_cmd_ready_o,
    input  logic [MVM_COUNT-1:0][$clog2(CORE_COUNT)-1:0]
                                                       dma_state_a_index_i,
    input  logic [MVM_COUNT-1:0][$clog2(CORE_COUNT)-1:0]
                                                       dma_state_b_index_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                       dma_block_a_id_i,
    input  logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
                                                       dma_block_b_id_i,
    input  logic [MVM_COUNT-1:0]                     dma_weight_valid_i,
    output logic [MVM_COUNT-1:0]                     dma_weight_ready_o,
    input  logic [MVM_COUNT-1:0][DATA_W-1:0]         dma_weight_data_i,

    input  logic                                      ext_partial_valid_i,
    output logic                                      ext_partial_ready_o,
    input  logic [GLOBAL_BLOCK_ID_W-1:0]             ext_partial_block_id_i,
    input  logic signed [DATA_W-1:0]                 ext_partial_data_i,
    input  logic                                      ext_partials_done_i,

    output logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]    state_current_o,
    output logic [CORE_COUNT-1:0][SPIN_COUNT-1:0]    state_next_o
);
    localparam int CORE_ID_W = (CORE_COUNT > 1) ? $clog2(CORE_COUNT) : 1;

    typedef enum logic [2:0] {
        TILE_IDLE,
        TILE_LOAD_STATES,
        TILE_START_NODE,
        TILE_RUN,
        TILE_DONE
    } tile_state_t;

    tile_state_t tile_state, tile_state_n;
    logic [CORE_ID_W-1:0] state_load_index;
    logic node_iter_start;
    logic node_iter_done;
    logic node_state_valid;
    logic node_state_ready;
    logic core_iter_start;
    logic ext_partials_done_pending;
    logic partials_done_to_cores;

    logic [CORE_COUNT-1:0] core_init_done;
    logic [CORE_COUNT-1:0] core_iter_done;
    logic [CORE_COUNT-1:0] core_h0_partial_valid;
    logic [CORE_COUNT-1:0] core_h0_partial_ready;
    logic signed [CORE_COUNT-1:0][DATA_W-1:0] core_h0_partial_data;
    logic [CORE_COUNT-1:0] core_ext_partial_valid;
    logic [CORE_COUNT-1:0] core_ext_partial_ready;
    logic signed [CORE_COUNT-1:0][DATA_W-1:0] core_ext_partial_data;

    logic [MVM_COUNT-1:0] node_partial_valid;
    logic [MVM_COUNT-1:0] node_partial_ready;
    logic signed [MVM_COUNT-1:0][DATA_W-1:0] node_partial_data;
    logic [MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] node_partial_block_id;
    logic [MVM_COUNT-1:0] node_partial_last;

    assign init_done = &core_init_done;
    assign iter_done = &core_iter_done;
    assign core_iter_start = iter_start && tile_state == TILE_IDLE;
    assign node_iter_start = tile_state == TILE_START_NODE;
    assign node_state_valid = tile_state == TILE_LOAD_STATES;
    assign partials_done_to_cores = node_iter_done && ext_partials_done_pending;

    generate
        for (genvar core_index = 0; core_index < CORE_COUNT; core_index++) begin : gen_spin_cores
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
                .coeff_c(coeff_c_i),
                .noise_amplitude(noise_amplitude_i),
                .init_state(init_state_i[core_index]),
                .iter_start(core_iter_start),
                .partials_done(partials_done_to_cores),
                .commit,
                .noise_decay(noise_decay_i),
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

    hierarchy_node #(
        .STATE_ENTRY_COUNT(CORE_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W)
    ) hierarchy_compute (
        .clk,
        .rst,
        .iter_start(node_iter_start),
        .iter_done(node_iter_done),
        .state_valid_i(node_state_valid),
        .state_ready_o(node_state_ready),
        .state_index_i(state_load_index),
        .state_data_i(state_current_o[state_load_index]),
        .schedule_done_i,
        .dma_cmd_valid_i,
        .dma_cmd_ready_o,
        .dma_state_a_index_i,
        .dma_state_b_index_i,
        .dma_block_a_i(dma_block_a_id_i),
        .dma_block_b_i(dma_block_b_id_i),
        .dma_weight_valid_i,
        .dma_weight_ready_o,
        .dma_weight_data_i,
        .partial_valid_o(node_partial_valid),
        .partial_ready_i(node_partial_ready),
        .partial_data_o(node_partial_data),
        .partial_block_id_o(node_partial_block_id),
        .partial_last_o(node_partial_last)
    );

    h0_star_adapter #(
        .CORE_COUNT(CORE_COUNT),
        .MVM_COUNT(MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
        .BASE_BLOCK_ID(BASE_BLOCK_ID)
    ) local_star (
        .clk,
        .rst,
        .partial_valid_i(node_partial_valid),
        .partial_ready_o(node_partial_ready),
        .partial_data_i(node_partial_data),
        .partial_block_id_i(node_partial_block_id),
        .partial_last_i(node_partial_last),
        .core_partial_valid_o(core_h0_partial_valid),
        .core_partial_ready_i(core_h0_partial_ready),
        .core_partial_data_o(core_h0_partial_data)
    );

    // Higher-level partials already have a single input stream, so only a
    // global-block-to-local-core demultiplexer is required here.
    always_comb begin
        core_ext_partial_valid = '0;
        core_ext_partial_data = '0;
        ext_partial_ready_o = 1'b0;

        for (int core_index = 0; core_index < CORE_COUNT; core_index++) begin
            if (ext_partial_block_id_i ==
                GLOBAL_BLOCK_ID_W'(BASE_BLOCK_ID + core_index)) begin
                core_ext_partial_valid[core_index] = ext_partial_valid_i;
                core_ext_partial_data[core_index] = ext_partial_data_i;
                ext_partial_ready_o = core_ext_partial_ready[core_index];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            tile_state <= TILE_IDLE;
            state_load_index <= '0;
            ext_partials_done_pending <= 1'b0;
        end
        else begin
            tile_state <= tile_state_n;

            if (iter_start) begin
                state_load_index <= '0;
                ext_partials_done_pending <= 1'b0;
            end
            else begin
                if (tile_state == TILE_LOAD_STATES &&
                    node_state_valid && node_state_ready) begin
                    if (state_load_index != CORE_ID_W'(CORE_COUNT-1))
                        state_load_index <= state_load_index + 1'b1;
                end

                if (ext_partials_done_i)
                    ext_partials_done_pending <= 1'b1;
            end
        end
    end

    always_comb begin
        tile_state_n = tile_state;

        unique case (tile_state)
            TILE_IDLE: begin
                if (iter_start)
                    tile_state_n = TILE_LOAD_STATES;
            end
            TILE_LOAD_STATES: begin
                if (node_state_valid && node_state_ready &&
                    state_load_index == CORE_ID_W'(CORE_COUNT-1))
                    tile_state_n = TILE_START_NODE;
            end
            TILE_START_NODE: tile_state_n = TILE_RUN;
            TILE_RUN: begin
                if (iter_done)
                    tile_state_n = TILE_DONE;
            end
            TILE_DONE: begin
                if (commit)
                    tile_state_n = TILE_IDLE;
            end
            default: tile_state_n = TILE_IDLE;
        endcase
    end
endmodule
