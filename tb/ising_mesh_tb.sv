`timescale 1ns/1ps

import ising_pkg::*;

// Parameterized full-system testbench.
//
// Dataset format (stored under tb/datasets):
//   <vertex-count> <known-best-cut>
//   <one-based-source> <one-based-destination> <signed-int8-weight>
//
// Select the file at runtime with +DATASET=<filename>. Structural parameters
// are compile-time parameters and can be overridden with Verilator -G options.
module ising_mesh_tb #(
    parameter int MESH_X_COUNT       = 2,
    parameter int MESH_Y_COUNT       = 1,
    parameter int H0_COUNT           = 2,
    parameter int CORES_PER_H0       = 2,
    parameter int H0_MVM_COUNT       = 1,
    parameter int H1_MVM_COUNT       = 1,
    parameter int CROSS_MVM_COUNT    = 1,
    parameter int FIFO_DEPTH         = 4,
    parameter bit SKIP_ZERO_BLOCKS   = 1'b0,
    parameter bit USE_RAMULATOR      = 1'b0,
    parameter int MEM_PIN_COUNT      = 128,
    parameter int MEM_PIN_GBPS       = 32,
    parameter int MEM_LANES          = 16,
    parameter int ITERATION_COUNT    = 1,
    parameter int CLK_PERIOD_NS      = 10,
    parameter int COEFF_A_VALUE      = 0,
    parameter int COEFF_B_VALUE      = 1,
    parameter int COEFF_C_VALUE      = 0,
    parameter int NOISE_AMPLITUDE    = 0,
    parameter int NOISE_DECAY_VALUE  = 0,
    parameter longint MAX_CYCLES     = 64'd1_000_000_000_000
);
    localparam int NODE_COUNT = MESH_X_COUNT * MESH_Y_COUNT;
    localparam int BLOCKS_PER_H0 = CORES_PER_H0;
    localparam int BLOCKS_PER_H1 = H0_COUNT * CORES_PER_H0;
    localparam int TOTAL_H0_COUNT = NODE_COUNT * H0_COUNT;
    localparam int TOTAL_BLOCK_COUNT = NODE_COUNT * BLOCKS_PER_H1;
    localparam int TOTAL_SPIN_COUNT = TOTAL_BLOCK_COUNT * SPIN_COUNT;
    localparam int TOP_STATE_ENTRY_COUNT = TOTAL_BLOCK_COUNT;
    localparam int GLOBAL_BLOCK_ID_W =
        (TOTAL_BLOCK_COUNT > 1) ? $clog2(TOTAL_BLOCK_COUNT) : 1;
    localparam int X_W = (MESH_X_COUNT > 1) ? $clog2(MESH_X_COUNT) : 1;
    localparam int Y_W = (MESH_Y_COUNT > 1) ? $clog2(MESH_Y_COUNT) : 1;
    localparam int SOURCE_ID_W = (NODE_COUNT > 1) ? $clog2(NODE_COUNT) : 1;
    localparam int EPOCH_W = (ITERATION_COUNT > 1) ? $clog2(ITERATION_COUNT + 1) : 1;
    localparam int H0_STATE_INDEX_W =
        (CORES_PER_H0 > 1) ? $clog2(CORES_PER_H0) : 1;
    localparam int H1_STATE_INDEX_W =
        (BLOCKS_PER_H1 > 1) ? $clog2(BLOCKS_PER_H1) : 1;
    localparam int TOP_STATE_INDEX_W =
        (TOP_STATE_ENTRY_COUNT > 1) ? $clog2(TOP_STATE_ENTRY_COUNT) : 1;
    localparam int WEIGHT_BEATS = SPIN_COUNT * SPIN_COUNT * WEIGHT_W / DATA_W;
    localparam int DRAM_SYSTEM_COUNT = TOTAL_H0_COUNT + 2*NODE_COUNT;

    logic clk;
    logic rst;

    logic [NODE_COUNT-1:0] init_start_i, init_done_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0]
        core_weight_valid_i, core_weight_ready_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][DATA_W-1:0]
        core_weight_data_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
        init_state_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][31:0]
        noise_seed_i;
    logic signed [NODE_COUNT-1:0][COEFF_W-1:0]
        coeff_a_i, coeff_b_i, coeff_c_i, noise_amplitude_i;

    logic [NODE_COUNT-1:0] iter_start_i, iter_done_o, commit_i, done_i;
    logic [NODE_COUNT-1:0][16:0] noise_decay_i;
    logic [NODE_COUNT-1:0][EPOCH_W-1:0] epoch_i;

    logic [NODE_COUNT-1:0][H0_COUNT-1:0] h0_schedule_done_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
        h0_dma_cmd_valid_i, h0_dma_cmd_ready_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
          [H0_STATE_INDEX_W-1:0]
        h0_dma_state_a_index_i, h0_dma_state_b_index_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
          [GLOBAL_BLOCK_ID_W-1:0]
        h0_dma_block_a_id_i, h0_dma_block_b_id_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
        h0_dma_weight_valid_i, h0_dma_weight_ready_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0][DATA_W-1:0]
        h0_dma_weight_data_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
        h0_src_cmd_valid, h0_src_cmd_ready;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0][H0_STATE_INDEX_W-1:0]
        h0_src_state_a, h0_src_state_b;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
        h0_src_block_a, h0_src_block_b;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
        h0_src_weight_valid, h0_src_weight_ready;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0][DATA_W-1:0]
        h0_src_weight_data;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0] h0_streamer_idle;

    logic [NODE_COUNT-1:0] h1_schedule_done_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0]
        h1_dma_cmd_valid_i, h1_dma_cmd_ready_o;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][H1_STATE_INDEX_W-1:0]
        h1_dma_state_a_index_i, h1_dma_state_b_index_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
        h1_dma_block_a_id_i, h1_dma_block_b_id_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0]
        h1_dma_weight_valid_i, h1_dma_weight_ready_o;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][DATA_W-1:0]
        h1_dma_weight_data_i;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0]
        h1_src_cmd_valid, h1_src_cmd_ready;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][H1_STATE_INDEX_W-1:0]
        h1_src_state_a, h1_src_state_b;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
        h1_src_block_a, h1_src_block_b;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0]
        h1_src_weight_valid, h1_src_weight_ready;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0][DATA_W-1:0]
        h1_src_weight_data;
    logic [NODE_COUNT-1:0] h1_streamer_idle;

    logic [NODE_COUNT-1:0] state_publish_valid_i, state_publish_ready_o;
    logic [NODE_COUNT-1:0][H1_STATE_INDEX_W-1:0] state_publish_local_index_i;
    logic [NODE_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] state_publish_block_id_i;
    logic [NODE_COUNT-1:0][X_W-1:0] state_publish_dest_x_i;
    logic [NODE_COUNT-1:0][Y_W-1:0] state_publish_dest_y_i;
    logic [NODE_COUNT-1:0] done_publish_valid_i, done_publish_ready_o;
    logic [NODE_COUNT-1:0][X_W-1:0] done_publish_dest_x_i;
    logic [NODE_COUNT-1:0][Y_W-1:0] done_publish_dest_y_i;

    logic [NODE_COUNT-1:0] cross_iter_start_i, cross_iter_done_o;
    logic [NODE_COUNT-1:0] cross_schedule_done_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0]
        cross_dma_cmd_valid_i, cross_dma_cmd_ready_o;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][TOP_STATE_INDEX_W-1:0]
        cross_dma_state_a_index_i, cross_dma_state_b_index_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
        cross_dma_block_a_id_i, cross_dma_block_b_id_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0]
        cross_dma_weight_valid_i, cross_dma_weight_ready_o;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][DATA_W-1:0]
        cross_dma_weight_data_i;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0]
        cross_src_cmd_valid, cross_src_cmd_ready;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][TOP_STATE_INDEX_W-1:0]
        cross_src_state_a, cross_src_state_b;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0]
        cross_src_block_a, cross_src_block_b;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0]
        cross_src_weight_valid, cross_src_weight_ready;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0][DATA_W-1:0]
        cross_src_weight_data;
    logic [NODE_COUNT-1:0] cross_streamer_idle;

    logic [NODE_COUNT-1:0][H0_COUNT-1:0][CORES_PER_H0-1:0][SPIN_COUNT-1:0]
        state_current_o, state_next_o;

    // Sparse input representation. Missing keys are zero. active_block_pair
    // contains unordered off-diagonal block pairs that contain at least one
    // edge and is used only when SKIP_ZERO_BLOCKS is enabled.
    byte signed graph_weight [longint unsigned];
    bit active_block_pair [longint unsigned];
    bit publication_needed [longint unsigned];
    bit golden_next [0:TOTAL_SPIN_COUNT-1];
    longint signed golden_sum [0:TOTAL_SPIN_COUNT-1];
    logic [31:0] golden_lfsr [0:TOTAL_BLOCK_COUNT-1];

    int pair_owner [0:NODE_COUNT*NODE_COUNT-1];
    bit pair_assigned [0:NODE_COUNT*NODE_COUNT-1];
    int owner_capacity [0:NODE_COUNT-1];
    int owner_load [0:NODE_COUNT-1];
    int h0_next_engine [0:TOTAL_H0_COUNT-1];
    int h1_next_engine [0:NODE_COUNT-1];
    int cross_next_engine [0:NODE_COUNT-1];

    string dataset_name;
    string dataset_path;
    int dataset_vertex_count;
    longint dataset_known_cut;
    longint dataset_edge_records;
    longint cycle_count;
    longint issued_h0_blocks;
    longint issued_h1_blocks;
    longint issued_cross_blocks;
    longint published_states;
    bit verbose_blocks;

    import "DPI-C" function void az_dram_init(
        input string config_path,
        input string dataset_path,
        input int system_count,
        input int block_count
    );
    import "DPI-C" function void az_dram_tick(input int tick_count);
    import "DPI-C" function void az_dram_report();
    import "DPI-C" function void az_dram_finalize();

    ising_mesh #(
        .MESH_X_COUNT(MESH_X_COUNT), .MESH_Y_COUNT(MESH_Y_COUNT),
        .H0_COUNT(H0_COUNT), .CORES_PER_H0(CORES_PER_H0),
        .H0_MVM_COUNT(H0_MVM_COUNT), .H1_MVM_COUNT(H1_MVM_COUNT),
        .CROSS_MVM_COUNT(CROSS_MVM_COUNT),
        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W), .X_W(X_W), .Y_W(Y_W),
        .SOURCE_ID_W(SOURCE_ID_W), .EPOCH_W(EPOCH_W),
        .FIFO_DEPTH(FIFO_DEPTH), .NODE_COUNT(NODE_COUNT),
        .BLOCKS_PER_H1(BLOCKS_PER_H1),
        .TOP_STATE_ENTRY_COUNT(TOP_STATE_ENTRY_COUNT)
    ) dut (.*);

    generate
        if (!USE_RAMULATOR) begin : gen_direct_weights
            assign h0_dma_cmd_valid_i = h0_src_cmd_valid;
            assign h0_src_cmd_ready = h0_dma_cmd_ready_o;
            assign h0_dma_state_a_index_i = h0_src_state_a;
            assign h0_dma_state_b_index_i = h0_src_state_b;
            assign h0_dma_block_a_id_i = h0_src_block_a;
            assign h0_dma_block_b_id_i = h0_src_block_b;
            assign h0_dma_weight_valid_i = h0_src_weight_valid;
            assign h0_src_weight_ready = h0_dma_weight_ready_o;
            assign h0_dma_weight_data_i = h0_src_weight_data;
            assign h0_streamer_idle = '1;

            assign h1_dma_cmd_valid_i = h1_src_cmd_valid;
            assign h1_src_cmd_ready = h1_dma_cmd_ready_o;
            assign h1_dma_state_a_index_i = h1_src_state_a;
            assign h1_dma_state_b_index_i = h1_src_state_b;
            assign h1_dma_block_a_id_i = h1_src_block_a;
            assign h1_dma_block_b_id_i = h1_src_block_b;
            assign h1_dma_weight_valid_i = h1_src_weight_valid;
            assign h1_src_weight_ready = h1_dma_weight_ready_o;
            assign h1_dma_weight_data_i = h1_src_weight_data;
            assign h1_streamer_idle = '1;

            assign cross_dma_cmd_valid_i = cross_src_cmd_valid;
            assign cross_src_cmd_ready = cross_dma_cmd_ready_o;
            assign cross_dma_state_a_index_i = cross_src_state_a;
            assign cross_dma_state_b_index_i = cross_src_state_b;
            assign cross_dma_block_a_id_i = cross_src_block_a;
            assign cross_dma_block_b_id_i = cross_src_block_b;
            assign cross_dma_weight_valid_i = cross_src_weight_valid;
            assign cross_src_weight_ready = cross_dma_weight_ready_o;
            assign cross_dma_weight_data_i = cross_src_weight_data;
            assign cross_streamer_idle = '1;
        end
        else begin : gen_ramulator_weights
            assign h0_src_weight_ready = '1;
            assign h1_src_weight_ready = '1;
            assign cross_src_weight_ready = '1;

            for (genvar node = 0; node < NODE_COUNT; node++) begin : gen_node_memory
                for (genvar h0 = 0; h0 < H0_COUNT; h0++) begin : gen_h0_memory
                    ramulator_node_frontend #(
                        .SYSTEM_ID(node*H0_COUNT+h0),
                        .MVM_COUNT(H0_MVM_COUNT),
                        .STATE_INDEX_W(H0_STATE_INDEX_W),
                        .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
                        .TOTAL_BLOCK_COUNT(TOTAL_BLOCK_COUNT),
                        .MEM_LANES(MEM_LANES)
                    ) frontend (
                        .clk, .rst,
                        .sched_cmd_valid_i(h0_src_cmd_valid[node][h0]),
                        .sched_cmd_ready_o(h0_src_cmd_ready[node][h0]),
                        .sched_state_a_index_i(h0_src_state_a[node][h0]),
                        .sched_state_b_index_i(h0_src_state_b[node][h0]),
                        .sched_block_a_id_i(h0_src_block_a[node][h0]),
                        .sched_block_b_id_i(h0_src_block_b[node][h0]),
                        .node_cmd_valid_o(h0_dma_cmd_valid_i[node][h0]),
                        .node_cmd_ready_i(h0_dma_cmd_ready_o[node][h0]),
                        .node_state_a_index_o(h0_dma_state_a_index_i[node][h0]),
                        .node_state_b_index_o(h0_dma_state_b_index_i[node][h0]),
                        .node_block_a_id_o(h0_dma_block_a_id_i[node][h0]),
                        .node_block_b_id_o(h0_dma_block_b_id_i[node][h0]),
                        .node_weight_valid_o(h0_dma_weight_valid_i[node][h0]),
                        .node_weight_ready_i(h0_dma_weight_ready_o[node][h0]),
                        .node_weight_data_o(h0_dma_weight_data_i[node][h0]),
                        .idle_o(h0_streamer_idle[node][h0])
                    );
                end

                ramulator_node_frontend #(
                    .SYSTEM_ID(TOTAL_H0_COUNT+node),
                    .MVM_COUNT(H1_MVM_COUNT),
                    .STATE_INDEX_W(H1_STATE_INDEX_W),
                    .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
                    .TOTAL_BLOCK_COUNT(TOTAL_BLOCK_COUNT),
                    .MEM_LANES(MEM_LANES)
                ) h1_frontend (
                    .clk, .rst,
                    .sched_cmd_valid_i(h1_src_cmd_valid[node]),
                    .sched_cmd_ready_o(h1_src_cmd_ready[node]),
                    .sched_state_a_index_i(h1_src_state_a[node]),
                    .sched_state_b_index_i(h1_src_state_b[node]),
                    .sched_block_a_id_i(h1_src_block_a[node]),
                    .sched_block_b_id_i(h1_src_block_b[node]),
                    .node_cmd_valid_o(h1_dma_cmd_valid_i[node]),
                    .node_cmd_ready_i(h1_dma_cmd_ready_o[node]),
                    .node_state_a_index_o(h1_dma_state_a_index_i[node]),
                    .node_state_b_index_o(h1_dma_state_b_index_i[node]),
                    .node_block_a_id_o(h1_dma_block_a_id_i[node]),
                    .node_block_b_id_o(h1_dma_block_b_id_i[node]),
                    .node_weight_valid_o(h1_dma_weight_valid_i[node]),
                    .node_weight_ready_i(h1_dma_weight_ready_o[node]),
                    .node_weight_data_o(h1_dma_weight_data_i[node]),
                    .idle_o(h1_streamer_idle[node])
                );

                ramulator_node_frontend #(
                    .SYSTEM_ID(TOTAL_H0_COUNT+NODE_COUNT+node),
                    .MVM_COUNT(CROSS_MVM_COUNT),
                    .STATE_INDEX_W(TOP_STATE_INDEX_W),
                    .GLOBAL_BLOCK_ID_W(GLOBAL_BLOCK_ID_W),
                    .TOTAL_BLOCK_COUNT(TOTAL_BLOCK_COUNT),
                    .MEM_LANES(MEM_LANES)
                ) cross_frontend (
                    .clk, .rst,
                    .sched_cmd_valid_i(cross_src_cmd_valid[node]),
                    .sched_cmd_ready_o(cross_src_cmd_ready[node]),
                    .sched_state_a_index_i(cross_src_state_a[node]),
                    .sched_state_b_index_i(cross_src_state_b[node]),
                    .sched_block_a_id_i(cross_src_block_a[node]),
                    .sched_block_b_id_i(cross_src_block_b[node]),
                    .node_cmd_valid_o(cross_dma_cmd_valid_i[node]),
                    .node_cmd_ready_i(cross_dma_cmd_ready_o[node]),
                    .node_state_a_index_o(cross_dma_state_a_index_i[node]),
                    .node_state_b_index_o(cross_dma_state_b_index_i[node]),
                    .node_block_a_id_o(cross_dma_block_a_id_i[node]),
                    .node_block_b_id_o(cross_dma_block_b_id_i[node]),
                    .node_weight_valid_o(cross_dma_weight_valid_i[node]),
                    .node_weight_ready_i(cross_dma_weight_ready_o[node]),
                    .node_weight_data_o(cross_dma_weight_data_i[node]),
                    .idle_o(cross_streamer_idle[node])
                );
            end
        end
    endgenerate

    initial clk = 1'b0;
    always #(CLK_PERIOD_NS/2) clk = ~clk;

    // The projected 32-Gb/s GDDR configuration has a 250-ps command clock.
    // Four Ramulator ticks therefore elapse per 1-ns accelerator cycle.
    always @(negedge clk)
        if (USE_RAMULATOR && !rst)
            az_dram_tick(4);

    always_ff @(posedge clk) begin
        if (rst)
            cycle_count <= 0;
        else begin
            cycle_count <= cycle_count + 1;
            if (cycle_count >= MAX_CYCLES)
                $fatal(1, "timeout after %0d cycles", cycle_count);
        end
    end

    function automatic bit is_power_of_two(input int value);
        return value > 0 && (value & (value - 1)) == 0;
    endfunction

    function automatic longint unsigned edge_key(input int row, input int column);
        return (longint'(row) << 32) | longint'(column);
    endfunction

    function automatic longint unsigned block_pair_key(input int block_a,
                                                         input int block_b);
        int low_block;
        int high_block;
        low_block = (block_a < block_b) ? block_a : block_b;
        high_block = (block_a < block_b) ? block_b : block_a;
        return edge_key(low_block, high_block);
    endfunction

    function automatic int mesh_distance(input int node_a, input int node_b);
        int ax, ay, bx, by;
        ax = node_a % MESH_X_COUNT;
        ay = node_a / MESH_X_COUNT;
        bx = node_b % MESH_X_COUNT;
        by = node_b / MESH_X_COUNT;
        return ((ax > bx) ? ax-bx : bx-ax) + ((ay > by) ? ay-by : by-ay);
    endfunction

    function automatic logic [SPIN_COUNT-1:0] initial_state_for_block(
        input int block_id
    );
        logic [31:0] value;
        value = 32'h9e37_79b9 ^ (32'(block_id) * 32'h85eb_ca6b);
        value ^= value << 13;
        value ^= value >> 17;
        value ^= value << 5;
        return value;
    endfunction

    function automatic logic [31:0] noise_seed_for_block(input int block_id);
        logic [31:0] value;
        value = 32'hd1b5_4a35 ^ (32'(block_id) * 32'h27d4_eb2d);
        return (value == 0) ? 32'h1 : value;
    endfunction

    function automatic bit current_spin(input int spin_index);
        int block_id, node_id, local_block, h0_id, core_id, spin_id;
        block_id = spin_index / SPIN_COUNT;
        spin_id = spin_index % SPIN_COUNT;
        node_id = block_id / BLOCKS_PER_H1;
        local_block = block_id % BLOCKS_PER_H1;
        h0_id = local_block / CORES_PER_H0;
        core_id = local_block % CORES_PER_H0;
        return state_current_o[node_id][h0_id][core_id][spin_id];
    endfunction

    function automatic byte signed get_weight(input int row, input int column);
        longint unsigned key;
        key = edge_key(row, column);
        return graph_weight.exists(key) ? graph_weight[key] : 0;
    endfunction

    function automatic logic [DATA_W-1:0] weight_beat(
        input int block_a,
        input int block_b,
        input int beat_index
    );
        logic [DATA_W-1:0] beat;
        int entries_per_beat;
        int linear_entry;
        int row;
        int column;
        beat = '0;
        entries_per_beat = DATA_W / WEIGHT_W;
        for (int lane = 0; lane < entries_per_beat; lane++) begin
            linear_entry = beat_index * entries_per_beat + lane;
            row = linear_entry / SPIN_COUNT;
            column = linear_entry % SPIN_COUNT;
            beat[lane*WEIGHT_W +: WEIGHT_W] =
                get_weight(block_a*SPIN_COUNT + row,
                           block_b*SPIN_COUNT + column);
        end
        return beat;
    endfunction

    task automatic validate_configuration;
        if (!is_power_of_two(MESH_X_COUNT) || !is_power_of_two(MESH_Y_COUNT))
            $fatal(1, "mesh dimensions must each be powers of two");
        if (!is_power_of_two(H0_COUNT))
            $fatal(1, "H0_COUNT must be a power of two");
        if (!is_power_of_two(CORES_PER_H0))
            $fatal(1, "CORES_PER_H0 must be a power of two");
        if (!is_power_of_two(H0_MVM_COUNT) || H0_MVM_COUNT > CORES_PER_H0)
            $fatal(1, "H0_MVM_COUNT must be a power of two no larger than CORES_PER_H0");
        if (!is_power_of_two(H1_MVM_COUNT) || H1_MVM_COUNT > BLOCKS_PER_H1)
            $fatal(1, "H1_MVM_COUNT must be a power of two no larger than blocks/H1");
        if (!is_power_of_two(CROSS_MVM_COUNT) ||
            CROSS_MVM_COUNT > TOTAL_BLOCK_COUNT)
            $fatal(1, "CROSS_MVM_COUNT must be a legal power of two");
        if (!is_power_of_two(FIFO_DEPTH))
            $fatal(1, "FIFO_DEPTH must be a power of two");
        if (DATA_W % WEIGHT_W != 0 || DATA_W % ACC_W != 0 ||
            SPIN_COUNT*SPIN_COUNT*WEIGHT_W % DATA_W != 0)
            $fatal(1, "package widths do not form complete weight/partial beats");
        if (COEFF_A_VALUE < -(1 << (COEFF_W-1)) ||
            COEFF_A_VALUE >= (1 << (COEFF_W-1)) ||
            COEFF_B_VALUE < -(1 << (COEFF_W-1)) ||
            COEFF_B_VALUE >= (1 << (COEFF_W-1)))
            $fatal(1, "coefficients do not fit COEFF_W");
    endtask

    task automatic initialize_inputs;
        init_start_i = '0; core_weight_valid_i = '0; core_weight_data_i = '0;
        init_state_i = '0; noise_seed_i = '0;
        coeff_a_i = '0; coeff_b_i = '0; coeff_c_i = '0;
        noise_amplitude_i = '0; iter_start_i = '0; commit_i = '0; done_i = '0;
        noise_decay_i = '0; epoch_i = '0;
        h0_schedule_done_i = '0; h0_src_cmd_valid = '0;
        h0_src_state_a = '0; h0_src_state_b = '0;
        h0_src_block_a = '0; h0_src_block_b = '0;
        h0_src_weight_valid = '0; h0_src_weight_data = '0;
        h1_schedule_done_i = '0; h1_src_cmd_valid = '0;
        h1_src_state_a = '0; h1_src_state_b = '0;
        h1_src_block_a = '0; h1_src_block_b = '0;
        h1_src_weight_valid = '0; h1_src_weight_data = '0;
        state_publish_valid_i = '0; state_publish_local_index_i = '0;
        state_publish_block_id_i = '0; state_publish_dest_x_i = '0;
        state_publish_dest_y_i = '0; done_publish_valid_i = '0;
        done_publish_dest_x_i = '0; done_publish_dest_y_i = '0;
        cross_iter_start_i = '0; cross_schedule_done_i = '0;
        cross_src_cmd_valid = '0; cross_src_state_a = '0;
        cross_src_state_b = '0; cross_src_block_a = '0;
        cross_src_block_b = '0; cross_src_weight_valid = '0;
        cross_src_weight_data = '0;
    endtask

    task automatic load_dataset;
        int file_handle;
        int scan_result;
        int source_vertex;
        int destination_vertex;
        int weight_value;
        int block_a;
        int block_b;
        longint unsigned key;

        if (!$value$plusargs("DATASET=%s", dataset_name))
            dataset_name = "g256_smoke.txt";
        dataset_path = $sformatf("tb/datasets/%s", dataset_name);
        file_handle = $fopen(dataset_path, "r");
        if (file_handle == 0)
            $fatal(1, "cannot open dataset %s", dataset_path);
        scan_result = $fscanf(file_handle, "%d %d", dataset_vertex_count,
                              dataset_known_cut);
        if (scan_result != 2)
            $fatal(1, "invalid dataset header in %s", dataset_path);
        if (dataset_vertex_count != TOTAL_SPIN_COUNT)
            $fatal(1, "dataset has %0d spins, configuration implements %0d",
                   dataset_vertex_count, TOTAL_SPIN_COUNT);

        dataset_edge_records = 0;
        while (!$feof(file_handle)) begin
            scan_result = $fscanf(file_handle, "%d %d %d", source_vertex,
                                  destination_vertex, weight_value);
            if (scan_result == 3) begin
                source_vertex--;
                destination_vertex--;
                if (source_vertex < 0 || source_vertex >= TOTAL_SPIN_COUNT ||
                    destination_vertex < 0 ||
                    destination_vertex >= TOTAL_SPIN_COUNT)
                    $fatal(1, "dataset vertex outside configured range");
                if (weight_value < -128 || weight_value > 127)
                    $fatal(1, "dataset weight %0d does not fit int8", weight_value);
                key = edge_key(source_vertex, destination_vertex);
                graph_weight[key] = byte'(weight_value);
                block_a = source_vertex / SPIN_COUNT;
                block_b = destination_vertex / SPIN_COUNT;
                if (block_a != block_b)
                    active_block_pair[block_pair_key(block_a, block_b)] = 1'b1;
                dataset_edge_records++;
            end
            else if (!$feof(file_handle))
                $fatal(1, "malformed edge record in %s", dataset_path);
        end
        $fclose(file_handle);
        $display("dataset=%s spins=%0d edge_records=%0d known_cut=%0d",
                 dataset_name, dataset_vertex_count, dataset_edge_records,
                 dataset_known_cut);
    endtask

    // Reproduce the validated distance-balanced allocator used by the GVSOC
    // model. Every unordered H1 pair receives exactly one top-node owner.
    task automatic allocate_h1_pairs;
        int pair_count;
        int base_capacity;
        int extra_capacity;
        bit extra_selected [0:NODE_COUNT-1];
        int best_node;
        int best_total_distance;
        int total_distance;
        int best_a, best_b, best_pair_distance;
        int pair_distance;
        int selected_owner;
        int candidate_max_distance, candidate_sum_distance;
        int best_max_distance, best_sum_distance;

        pair_count = NODE_COUNT * (NODE_COUNT - 1) / 2;
        base_capacity = (NODE_COUNT == 0) ? 0 : pair_count / NODE_COUNT;
        extra_capacity = (NODE_COUNT == 0) ? 0 : pair_count % NODE_COUNT;
        pair_owner = '{default: -1};
        pair_assigned = '{default: 1'b0};
        owner_capacity = '{default: base_capacity};
        owner_load = '{default: 0};
        extra_selected = '{default: 1'b0};

        for (int extra = 0; extra < extra_capacity; extra++) begin
            best_node = -1;
            best_total_distance = 32'h7fff_ffff;
            for (int node = 0; node < NODE_COUNT; node++) begin
                total_distance = 0;
                for (int other = 0; other < NODE_COUNT; other++)
                    total_distance += mesh_distance(node, other);
                if (!extra_selected[node] &&
                    (total_distance < best_total_distance ||
                     (total_distance == best_total_distance && node < best_node))) begin
                    best_node = node;
                    best_total_distance = total_distance;
                end
            end
            extra_selected[best_node] = 1'b1;
            owner_capacity[best_node]++;
        end

        for (int step = 0; step < pair_count; step++) begin
            best_a = -1;
            best_b = -1;
            best_pair_distance = -1;
            for (int endpoint_a = 0; endpoint_a < NODE_COUNT; endpoint_a++) begin
                for (int endpoint_b = endpoint_a + 1;
                     endpoint_b < NODE_COUNT; endpoint_b++) begin
                    pair_distance = mesh_distance(endpoint_a, endpoint_b);
                    if (!pair_assigned[endpoint_a*NODE_COUNT + endpoint_b] &&
                        pair_distance > best_pair_distance) begin
                        best_a = endpoint_a;
                        best_b = endpoint_b;
                        best_pair_distance = pair_distance;
                    end
                end
            end

            selected_owner = -1;
            best_max_distance = 32'h7fff_ffff;
            best_sum_distance = 32'h7fff_ffff;
            for (int owner = 0; owner < NODE_COUNT; owner++) begin
                if (owner_load[owner] < owner_capacity[owner]) begin
                    candidate_max_distance =
                        (mesh_distance(owner, best_a) > mesh_distance(owner, best_b)) ?
                         mesh_distance(owner, best_a) : mesh_distance(owner, best_b);
                    candidate_sum_distance = mesh_distance(owner, best_a) +
                                             mesh_distance(owner, best_b);
                    if (selected_owner < 0 ||
                        candidate_max_distance < best_max_distance ||
                        (candidate_max_distance == best_max_distance &&
                         candidate_sum_distance < best_sum_distance) ||
                        (candidate_max_distance == best_max_distance &&
                         candidate_sum_distance == best_sum_distance &&
                         owner_load[owner]*owner_capacity[selected_owner] <
                         owner_load[selected_owner]*owner_capacity[owner]) ||
                        (candidate_max_distance == best_max_distance &&
                         candidate_sum_distance == best_sum_distance &&
                         owner_load[owner]*owner_capacity[selected_owner] ==
                         owner_load[selected_owner]*owner_capacity[owner] &&
                         owner < selected_owner)) begin
                        selected_owner = owner;
                        best_max_distance = candidate_max_distance;
                        best_sum_distance = candidate_sum_distance;
                    end
                end
            end
            pair_assigned[best_a*NODE_COUNT + best_b] = 1'b1;
            pair_owner[best_a*NODE_COUNT + best_b] = selected_owner;
            pair_owner[best_b*NODE_COUNT + best_a] = selected_owner;
            owner_load[selected_owner]++;
        end
    endtask

    task automatic load_diagonal_block(input int block_id);
        int node_id;
        int local_block;
        int h0_id;
        int core_id;
        node_id = block_id / BLOCKS_PER_H1;
        local_block = block_id % BLOCKS_PER_H1;
        h0_id = local_block / CORES_PER_H0;
        core_id = local_block % CORES_PER_H0;
        for (int beat = 0; beat < WEIGHT_BEATS; beat++) begin
            @(negedge clk);
            core_weight_data_i[node_id][h0_id][core_id] =
                weight_beat(block_id, block_id, beat);
            core_weight_valid_i[node_id][h0_id][core_id] = 1'b1;
            while (!core_weight_ready_o[node_id][h0_id][core_id])
                @(negedge clk);
        end
        @(negedge clk);
        core_weight_valid_i[node_id][h0_id][core_id] = 1'b0;
    endtask

    task automatic publish_state(input int block_id, input int destination_node);
        int source_node;
        int local_block;
        source_node = block_id / BLOCKS_PER_H1;
        local_block = block_id % BLOCKS_PER_H1;
        @(negedge clk);
        state_publish_local_index_i[source_node] = H1_STATE_INDEX_W'(local_block);
        state_publish_block_id_i[source_node] = GLOBAL_BLOCK_ID_W'(block_id);
        state_publish_dest_x_i[source_node] = X_W'(destination_node % MESH_X_COUNT);
        state_publish_dest_y_i[source_node] = Y_W'(destination_node / MESH_X_COUNT);
        state_publish_valid_i[source_node] = 1'b1;
        while (!state_publish_ready_o[source_node]) @(negedge clk);
        @(negedge clk);
        state_publish_valid_i[source_node] = 1'b0;
        published_states++;
    endtask

    task automatic mark_publication(input int block_id, input int owner);
        publication_needed[edge_key(block_id, owner)] = 1'b1;
    endtask

    task automatic prepare_publications;
        longint unsigned key;
        int block_a, block_b;
        int h1_a, h1_b, owner;
        publication_needed.delete();
        if (SKIP_ZERO_BLOCKS) begin
            foreach (active_block_pair[key]) begin
                block_a = int'(key >> 32);
                block_b = int'(key[31:0]);
                h1_a = block_a / BLOCKS_PER_H1;
                h1_b = block_b / BLOCKS_PER_H1;
                if (h1_a != h1_b) begin
                    owner = pair_owner[h1_a*NODE_COUNT + h1_b];
                    mark_publication(block_a, owner);
                    mark_publication(block_b, owner);
                end
            end
        end
        else begin
            for (int h1_a_index = 0; h1_a_index < NODE_COUNT; h1_a_index++) begin
                for (int h1_b_index = h1_a_index + 1;
                     h1_b_index < NODE_COUNT; h1_b_index++) begin
                    owner = pair_owner[h1_a_index*NODE_COUNT + h1_b_index];
                    for (int local_block = 0; local_block < BLOCKS_PER_H1;
                         local_block++) begin
                        mark_publication(h1_a_index*BLOCKS_PER_H1 + local_block,
                                         owner);
                        mark_publication(h1_b_index*BLOCKS_PER_H1 + local_block,
                                         owner);
                    end
                end
            end
        end
    endtask

    task automatic send_h0_block(input int block_a, input int block_b);
        int global_h0;
        int node_id;
        int h0_id;
        int engine;
        global_h0 = block_a / CORES_PER_H0;
        node_id = global_h0 / H0_COUNT;
        h0_id = global_h0 % H0_COUNT;
        engine = h0_next_engine[global_h0];
        if (verbose_blocks)
            $display("issue H0 block (%0d,%0d) node=%0d h0=%0d engine=%0d cycle=%0d",
                     block_a, block_b, node_id, h0_id, engine, cycle_count);
        h0_next_engine[global_h0] = (engine + 1) % H0_MVM_COUNT;
        @(negedge clk);
        h0_src_state_a[node_id][h0_id][engine] =
            H0_STATE_INDEX_W'(block_a % CORES_PER_H0);
        h0_src_state_b[node_id][h0_id][engine] =
            H0_STATE_INDEX_W'(block_b % CORES_PER_H0);
        h0_src_block_a[node_id][h0_id][engine] = GLOBAL_BLOCK_ID_W'(block_a);
        h0_src_block_b[node_id][h0_id][engine] = GLOBAL_BLOCK_ID_W'(block_b);
        h0_src_cmd_valid[node_id][h0_id][engine] = 1'b1;
        while (!h0_src_cmd_ready[node_id][h0_id][engine]) @(negedge clk);
        @(negedge clk);
        h0_src_cmd_valid[node_id][h0_id][engine] = 1'b0;
        if (!USE_RAMULATOR) begin
            for (int beat = 0; beat < WEIGHT_BEATS; beat++) begin
                h0_src_weight_data[node_id][h0_id][engine] =
                    weight_beat(block_a, block_b, beat);
                h0_src_weight_valid[node_id][h0_id][engine] = 1'b1;
                while (!h0_src_weight_ready[node_id][h0_id][engine]) @(negedge clk);
                @(negedge clk);
            end
            h0_src_weight_valid[node_id][h0_id][engine] = 1'b0;
        end
        issued_h0_blocks++;
    endtask

    task automatic send_h1_block(input int block_a, input int block_b);
        int node_id;
        int engine;
        node_id = block_a / BLOCKS_PER_H1;
        engine = h1_next_engine[node_id];
        if (verbose_blocks)
            $display("issue H1 block (%0d,%0d) node=%0d engine=%0d cycle=%0d",
                     block_a, block_b, node_id, engine, cycle_count);
        h1_next_engine[node_id] = (engine + 1) % H1_MVM_COUNT;
        @(negedge clk);
        h1_src_state_a[node_id][engine] =
            H1_STATE_INDEX_W'(block_a % BLOCKS_PER_H1);
        h1_src_state_b[node_id][engine] =
            H1_STATE_INDEX_W'(block_b % BLOCKS_PER_H1);
        h1_src_block_a[node_id][engine] = GLOBAL_BLOCK_ID_W'(block_a);
        h1_src_block_b[node_id][engine] = GLOBAL_BLOCK_ID_W'(block_b);
        h1_src_cmd_valid[node_id][engine] = 1'b1;
        while (!h1_src_cmd_ready[node_id][engine]) @(negedge clk);
        @(negedge clk);
        h1_src_cmd_valid[node_id][engine] = 1'b0;
        if (!USE_RAMULATOR) begin
            for (int beat = 0; beat < WEIGHT_BEATS; beat++) begin
                h1_src_weight_data[node_id][engine] = weight_beat(block_a, block_b, beat);
                h1_src_weight_valid[node_id][engine] = 1'b1;
                while (!h1_src_weight_ready[node_id][engine]) @(negedge clk);
                @(negedge clk);
            end
            h1_src_weight_valid[node_id][engine] = 1'b0;
        end
        issued_h1_blocks++;
    endtask

    task automatic send_cross_block(input int block_a, input int block_b);
        int h1_a, h1_b, owner, engine;
        h1_a = block_a / BLOCKS_PER_H1;
        h1_b = block_b / BLOCKS_PER_H1;
        owner = pair_owner[h1_a*NODE_COUNT + h1_b];
        engine = cross_next_engine[owner];
        if (verbose_blocks)
            $display("issue cross block (%0d,%0d) owner=%0d engine=%0d cycle=%0d",
                     block_a, block_b, owner, engine, cycle_count);
        cross_next_engine[owner] = (engine + 1) % CROSS_MVM_COUNT;
        @(negedge clk);
        cross_src_state_a[owner][engine] = TOP_STATE_INDEX_W'(block_a);
        cross_src_state_b[owner][engine] = TOP_STATE_INDEX_W'(block_b);
        cross_src_block_a[owner][engine] = GLOBAL_BLOCK_ID_W'(block_a);
        cross_src_block_b[owner][engine] = GLOBAL_BLOCK_ID_W'(block_b);
        cross_src_cmd_valid[owner][engine] = 1'b1;
        while (!cross_src_cmd_ready[owner][engine]) @(negedge clk);
        @(negedge clk);
        cross_src_cmd_valid[owner][engine] = 1'b0;
        if (!USE_RAMULATOR) begin
            for (int beat = 0; beat < WEIGHT_BEATS; beat++) begin
                cross_src_weight_data[owner][engine] = weight_beat(block_a, block_b, beat);
                cross_src_weight_valid[owner][engine] = 1'b1;
                while (!cross_src_weight_ready[owner][engine]) @(negedge clk);
                @(negedge clk);
            end
            cross_src_weight_valid[owner][engine] = 1'b0;
        end
        issued_cross_blocks++;
    endtask

    task automatic send_block_pair(input int block_a, input int block_b);
        int h0_a, h0_b, h1_a, h1_b;
        h0_a = block_a / CORES_PER_H0;
        h0_b = block_b / CORES_PER_H0;
        h1_a = block_a / BLOCKS_PER_H1;
        h1_b = block_b / BLOCKS_PER_H1;
        if (h0_a == h0_b)
            send_h0_block(block_a, block_b);
        else if (h1_a == h1_b)
            send_h1_block(block_a, block_b);
        else
            send_cross_block(block_a, block_b);
    endtask

    task automatic drive_schedule;
        longint unsigned key;
        int block_a, block_b;
        if (SKIP_ZERO_BLOCKS) begin
            foreach (active_block_pair[key]) begin
                block_a = int'(key >> 32);
                block_b = int'(key[31:0]);
                send_block_pair(block_a, block_b);
            end
        end
        else begin
            for (block_a = 0; block_a < TOTAL_BLOCK_COUNT; block_a++)
                for (block_b = block_a + 1; block_b < TOTAL_BLOCK_COUNT; block_b++)
                    send_block_pair(block_a, block_b);
        end
    endtask

    task automatic pulse_schedule_done;
        @(negedge clk);
        h0_schedule_done_i = '1;
        h1_schedule_done_i = '1;
        cross_schedule_done_i = '1;
        @(negedge clk);
        h0_schedule_done_i = '0;
        h1_schedule_done_i = '0;
        cross_schedule_done_i = '0;
    endtask

    task automatic wait_for_streamers;
        if (USE_RAMULATOR)
            wait (&h0_streamer_idle && &h1_streamer_idle &&
                  &cross_streamer_idle);
    endtask

    // A packet buffered anywhere in a Floo router produces an output request.
    // Four consecutive globally quiet cycles therefore provide a testbench
    // drain barrier without adding debug ports to the synthesizable RTL.
    task automatic wait_for_network_drain;
        int quiet_cycles;
        quiet_cycles = 0;
        while (quiet_cycles < 4) begin
            @(posedge clk);
            if (router_activity == '0)
                quiet_cycles++;
            else
                quiet_cycles = 0;
        end
    endtask

    logic [NODE_COUNT-1:0] router_activity;
    for (genvar y = 0; y < MESH_Y_COUNT; y++) begin : monitor_y
        for (genvar x = 0; x < MESH_X_COUNT; x++) begin : monitor_x
            localparam int N = y*MESH_X_COUNT + x;
            assign router_activity[N] =
                |dut.gen_y[y].gen_x[x].tile.top.router.floo_valid_o;
        end
    end

    task automatic publish_completion;
        logic [NODE_COUNT-1:0] pending;
        pending = '1;
        @(negedge clk);
        for (int node = 0; node < NODE_COUNT; node++) begin
            done_publish_dest_x_i[node] = X_W'(node % MESH_X_COUNT);
            done_publish_dest_y_i[node] = Y_W'(node / MESH_X_COUNT);
        end
        done_publish_valid_i = pending;
        while (pending != '0) begin
            @(negedge clk);
            for (int node = 0; node < NODE_COUNT; node++)
                if (pending[node] && done_publish_ready_o[node])
                    pending[node] = 1'b0;
            done_publish_valid_i = pending;
        end
        wait_for_network_drain();
    endtask

    function automatic logic [31:0] advance_lfsr(input logic [31:0] value);
        logic feedback;
        feedback = value[31] ^ value[21] ^ value[1] ^ value[0];
        return {value[30:0], feedback};
    endfunction

    task automatic compute_golden;
        longint unsigned key;
        int row, column;
        longint signed field;
        longint signed noise;
        int block_id;
        int local_spin;
        golden_sum = '{default: 0};
        foreach (graph_weight[key]) begin
            row = int'(key >> 32);
            column = int'(key[31:0]);
            golden_sum[row] += $signed(graph_weight[key]) *
                               (current_spin(column) ? 1 : -1);
        end
        for (int block = 0; block < TOTAL_BLOCK_COUNT; block++)
            golden_lfsr[block] = advance_lfsr(golden_lfsr[block]);
        for (int spin = 0; spin < TOTAL_SPIN_COUNT; spin++) begin
            block_id = spin / SPIN_COUNT;
            local_spin = spin % SPIN_COUNT;
            noise = golden_lfsr[block_id][local_spin] ?
                    NOISE_AMPLITUDE : -NOISE_AMPLITUDE;
            field = (current_spin(spin) ? COEFF_A_VALUE : -COEFF_A_VALUE) +
                    COEFF_B_VALUE * golden_sum[spin] + noise;
            golden_next[spin] = field >= 0;
        end
    endtask

    task automatic check_next_state(input int iteration);
        int errors;
        int block_id, node_id, local_block, h0_id, core_id, spin_id;
        errors = 0;
        for (int spin = 0; spin < TOTAL_SPIN_COUNT; spin++) begin
            block_id = spin / SPIN_COUNT;
            spin_id = spin % SPIN_COUNT;
            node_id = block_id / BLOCKS_PER_H1;
            local_block = block_id % BLOCKS_PER_H1;
            h0_id = local_block / CORES_PER_H0;
            core_id = local_block % CORES_PER_H0;
            if (state_next_o[node_id][h0_id][core_id][spin_id] !==
                golden_next[spin]) begin
                if (errors < 16)
                    $error("iteration %0d spin %0d got %0b expected %0b",
                           iteration, spin,
                           state_next_o[node_id][h0_id][core_id][spin_id],
                           golden_next[spin]);
                errors++;
            end
        end
        if (errors != 0)
            $fatal(1, "iteration %0d failed with %0d state mismatches",
                   iteration, errors);
        $display("iteration=%0d PASS cycles=%0d h0_blocks=%0d h1_blocks=%0d cross_blocks=%0d noc_states=%0d",
                 iteration, cycle_count, issued_h0_blocks, issued_h1_blocks,
                 issued_cross_blocks, published_states);
    endtask

    initial begin : test_sequence
        longint unsigned publication_key;
        int publication_block;
        int publication_owner;

        validate_configuration();
        initialize_inputs();
        verbose_blocks = $test$plusargs("VERBOSE_BLOCKS");
        rst = 1'b1;
        load_dataset();
        if (USE_RAMULATOR)
            az_dram_init("tb/ramulator_128x32.yaml", dataset_path,
                         DRAM_SYSTEM_COUNT, TOTAL_BLOCK_COUNT);
        allocate_h1_pairs();

        for (int block = 0; block < TOTAL_BLOCK_COUNT; block++) begin
            int node_id;
            int local_block;
            int h0_id;
            int core_id;
            node_id = block / BLOCKS_PER_H1;
            local_block = block % BLOCKS_PER_H1;
            h0_id = local_block / CORES_PER_H0;
            core_id = local_block % CORES_PER_H0;
            init_state_i[node_id][h0_id][core_id] = initial_state_for_block(block);
            noise_seed_i[node_id][h0_id][core_id] = noise_seed_for_block(block);
            golden_lfsr[block] = noise_seed_for_block(block);
        end
        coeff_a_i = '{default: COEFF_W'(COEFF_A_VALUE)};
        coeff_b_i = '{default: COEFF_W'(COEFF_B_VALUE)};
        coeff_c_i = '{default: COEFF_W'(COEFF_C_VALUE)};
        noise_amplitude_i = '{default: COEFF_W'(NOISE_AMPLITUDE)};
        noise_decay_i = '{default: 17'(NOISE_DECAY_VALUE)};

        repeat (5) @(negedge clk);
        rst = 1'b0;
        @(negedge clk);
        init_start_i = '1;
        @(negedge clk);
        init_start_i = '0;
        for (int block = 0; block < TOTAL_BLOCK_COUNT; block++)
            load_diagonal_block(block);
        wait (&init_done_o);
        $display("initialization complete at cycle %0d", cycle_count);

        for (int iteration = 0; iteration < ITERATION_COUNT; iteration++) begin
            issued_h0_blocks = 0;
            issued_h1_blocks = 0;
            issued_cross_blocks = 0;
            published_states = 0;
            h0_next_engine = '{default: 0};
            h1_next_engine = '{default: 0};
            cross_next_engine = '{default: 0};
            epoch_i = '{default: EPOCH_W'(iteration)};
            compute_golden();

            @(negedge clk);
            iter_start_i = '1;
            @(negedge clk);
            iter_start_i = '0;

            prepare_publications();
            $display("iteration %0d: publishing states", iteration);
            foreach (publication_needed[publication_key]) begin
                publication_block = int'(publication_key >> 32);
                publication_owner = int'(publication_key[31:0]);
                publish_state(publication_block, publication_owner);
            end
            wait_for_network_drain();
            $display("iteration %0d: state publication drained at cycle %0d",
                     iteration, cycle_count);

            @(negedge clk);
            cross_iter_start_i = '1;
            @(negedge clk);
            cross_iter_start_i = '0;

            drive_schedule();
            $display("iteration %0d: schedule issued at cycle %0d", iteration,
                     cycle_count);
            wait_for_streamers();
            pulse_schedule_done();
            wait (&cross_iter_done_o);
            $display("iteration %0d: cross compute done at cycle %0d", iteration,
                     cycle_count);
            wait_for_network_drain();
            publish_completion();
            wait (&iter_done_o);
            check_next_state(iteration);

            @(negedge clk);
            if (iteration == ITERATION_COUNT-1)
                done_i = '1;
            commit_i = '1;
            @(negedge clk);
            commit_i = '0;
        end

        $display("PASS: %0d iteration(s), skip_zero_blocks=%0b, total_cycles=%0d",
                 ITERATION_COUNT, SKIP_ZERO_BLOCKS, cycle_count);
        if (USE_RAMULATOR) begin
            az_dram_report();
            az_dram_finalize();
        end
        $finish;
    end
endmodule
