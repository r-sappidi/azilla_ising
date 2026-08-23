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
    parameter int RAMULATOR_TCK_PS   = 250,
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
    localparam int CLK_PERIOD_PS = CLK_PERIOD_NS * 1000;
    localparam int SAFE_RAMULATOR_TCK_PS =
        (RAMULATOR_TCK_PS > 0) ? RAMULATOR_TCK_PS : 1;
    localparam int RAMULATOR_TICKS_PER_CYCLE =
        CLK_PERIOD_PS / SAFE_RAMULATOR_TCK_PS;

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

    typedef struct {
        int block_a;
        int block_b;
    } block_work_t;
    // Flatten the owner/engine dimensions before the queue dimension. Some
    // older simulator releases mishandle an unpacked dimension of size one followed
    // by a queue, silently retaining only one descriptor.
    block_work_t h0_work_queue [0:TOTAL_H0_COUNT*H0_MVM_COUNT-1][$];
    block_work_t h1_work_queue [0:NODE_COUNT*H1_MVM_COUNT-1][$];
    block_work_t cross_work_queue [0:NODE_COUNT*CROSS_MVM_COUNT-1][$];

    string dataset_name;
    string dataset_path;
    string schedule_path;
    string matlab_golden_path;
    int matlab_golden_file;
    int dataset_vertex_count;
    longint dataset_known_cut;
    longint dataset_edge_records;
    longint cycle_count;
    longint issued_h0_blocks;
    longint issued_h1_blocks;
    longint issued_cross_blocks;
    longint published_states;
    bit verbose_blocks;
    bit external_schedule;

    // Testbench-only NoC instrumentation. Physical-link counters observe each
    // directed router output once, so a flit crossing H hops contributes H
    // transfers. Local counters separately capture endpoint injection/ejection.
    longint unsigned noc_monitor_cycles;
    longint unsigned noc_link_offered [0:NODE_COUNT-1][0:3];
    longint unsigned noc_link_accepted [0:NODE_COUNT-1][0:3];
    longint unsigned noc_link_stalled [0:NODE_COUNT-1][0:3];
    longint unsigned noc_link_packets [0:NODE_COUNT-1][0:3];
    longint unsigned noc_link_type_flits [0:NODE_COUNT-1][0:3][0:3];
    longint unsigned noc_inject_offered [0:NODE_COUNT-1];
    longint unsigned noc_inject_accepted [0:NODE_COUNT-1];
    longint unsigned noc_inject_stalled [0:NODE_COUNT-1];
    longint unsigned noc_inject_packets [0:NODE_COUNT-1];
    longint unsigned noc_inject_type_flits [0:NODE_COUNT-1][0:3];
    longint unsigned noc_eject_offered [0:NODE_COUNT-1];
    longint unsigned noc_eject_accepted [0:NODE_COUNT-1];
    longint unsigned noc_eject_stalled [0:NODE_COUNT-1];
    longint unsigned noc_eject_packets [0:NODE_COUNT-1];
    longint unsigned noc_eject_type_flits [0:NODE_COUNT-1][0:3];
    logic noc_stats_active;
    logic [NODE_COUNT-1:0] noc_local_in_valid, noc_local_in_ready;
    logic [NODE_COUNT-1:0][1:0] noc_local_in_type;
    logic [NODE_COUNT-1:0] noc_local_in_last;
    logic [NODE_COUNT-1:0] noc_local_out_valid, noc_local_out_ready;
    logic [NODE_COUNT-1:0][1:0] noc_local_out_type;
    logic [NODE_COUNT-1:0] noc_local_out_last;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
        h0_cmd_accepted;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_cmd_accepted;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_cmd_accepted;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_MVM_COUNT-1:0]
        h0_cmd_pending;
    logic [NODE_COUNT-1:0][H1_MVM_COUNT-1:0] h1_cmd_pending;
    logic [NODE_COUNT-1:0][CROSS_MVM_COUNT-1:0] cross_cmd_pending;

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

    // Advance modeled DRAM time by exactly one accelerator clock period.
    always @(negedge clk)
        if (USE_RAMULATOR && !rst)
            az_dram_tick(RAMULATOR_TICKS_PER_CYCLE);

    always_ff @(posedge clk) begin
        if (rst)
            cycle_count <= 0;
        else begin
            cycle_count <= cycle_count + 1;
            if (cycle_count >= MAX_CYCLES)
                $fatal(1, "timeout after %0d cycles", cycle_count);
        end
    end

    // FlooNoC's local router port is port zero. Fixed generate indices make
    // these internal handshake signals available to the generic monitor below.
    for (genvar y = 0; y < MESH_Y_COUNT; y++) begin : noc_monitor_y
        for (genvar x = 0; x < MESH_X_COUNT; x++) begin : noc_monitor_x
            localparam int N = y*MESH_X_COUNT + x;
            assign noc_local_in_valid[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_in_valid[0];
            assign noc_local_in_ready[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_in_ready[0];
            assign noc_local_in_type[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_in_type[0];
            assign noc_local_in_last[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_in_last[0];
            assign noc_local_out_valid[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_valid[0];
            assign noc_local_out_ready[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_ready[0];
            assign noc_local_out_type[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_type[0];
            assign noc_local_out_last[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_last[0];
        end
    end

    function automatic bit is_physical_noc_link(input int node, input int dir);
        int x;
        int y;
        x = node % MESH_X_COUNT;
        y = node / MESH_X_COUNT;
        case (dir)
            0: return y > 0;                  // north
            1: return y+1 < MESH_Y_COUNT;     // south
            2: return x+1 < MESH_X_COUNT;     // east
            default: return x > 0;            // west
        endcase
    endfunction

    function automatic int noc_type_index(input logic [1:0] packet_type);
        case (packet_type)
            NOC_STATE: return 0;
            NOC_PARTIAL: return 1;
            NOC_EPOCH_DONE: return 2;
            default: return 3;
        endcase
    endfunction

    function automatic string noc_direction_name(input int dir);
        case (dir)
            0: return "north";
            1: return "south";
            2: return "east";
            default: return "west";
        endcase
    endfunction

    always @(posedge clk) begin : collect_noc_statistics
        int packet_type;
        if (rst) begin
            noc_stats_active = 1'b0;
            noc_monitor_cycles = 0;
            for (int node = 0; node < NODE_COUNT; node++) begin
                noc_inject_offered[node] = 0;
                noc_inject_accepted[node] = 0;
                noc_inject_stalled[node] = 0;
                noc_inject_packets[node] = 0;
                noc_eject_offered[node] = 0;
                noc_eject_accepted[node] = 0;
                noc_eject_stalled[node] = 0;
                noc_eject_packets[node] = 0;
                for (int packet = 0; packet < 4; packet++) begin
                    noc_inject_type_flits[node][packet] = 0;
                    noc_eject_type_flits[node][packet] = 0;
                end
                for (int dir = 0; dir < 4; dir++) begin
                    noc_link_offered[node][dir] = 0;
                    noc_link_accepted[node][dir] = 0;
                    noc_link_stalled[node][dir] = 0;
                    noc_link_packets[node][dir] = 0;
                    for (int packet = 0; packet < 4; packet++)
                        noc_link_type_flits[node][dir][packet] = 0;
                end
            end
        end
        else begin
            if (|iter_start_i)
                noc_stats_active = 1'b1;
            if (noc_stats_active) begin
                noc_monitor_cycles++;
                for (int node = 0; node < NODE_COUNT; node++) begin
                    if (noc_local_in_valid[node]) begin
                        noc_inject_offered[node]++;
                        if (noc_local_in_ready[node]) begin
                            noc_inject_accepted[node]++;
                            packet_type = noc_type_index(noc_local_in_type[node]);
                            noc_inject_type_flits[node][packet_type]++;
                            if (noc_local_in_last[node])
                                noc_inject_packets[node]++;
                        end
                        else
                            noc_inject_stalled[node]++;
                    end
                    if (noc_local_out_valid[node]) begin
                        noc_eject_offered[node]++;
                        if (noc_local_out_ready[node]) begin
                            noc_eject_accepted[node]++;
                            packet_type = noc_type_index(noc_local_out_type[node]);
                            noc_eject_type_flits[node][packet_type]++;
                            if (noc_local_out_last[node])
                                noc_eject_packets[node]++;
                        end
                        else
                            noc_eject_stalled[node]++;
                    end
                    for (int dir = 0; dir < 4; dir++) begin
                        if (is_physical_noc_link(node, dir) &&
                            dut.link_out_valid[node][dir]) begin
                            noc_link_offered[node][dir]++;
                            if (dut.link_out_ready[node][dir]) begin
                                noc_link_accepted[node][dir]++;
                                packet_type = noc_type_index(
                                    dut.link_out_type[node][dir]);
                                noc_link_type_flits[node][dir][packet_type]++;
                                if (dut.link_out_last[node][dir])
                                    noc_link_packets[node][dir]++;
                            end
                            else
                                noc_link_stalled[node][dir]++;
                        end
                    end
                end
            end
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
        if (external_schedule) begin
            for (int queue_index = 0;
                 queue_index < NODE_COUNT*CROSS_MVM_COUNT; queue_index++) begin
                owner = queue_index / CROSS_MVM_COUNT;
                for (int work_index = 0;
                     work_index < cross_work_queue[queue_index].size(); work_index++) begin
                    mark_publication(
                        cross_work_queue[queue_index][work_index].block_a, owner);
                    mark_publication(
                        cross_work_queue[queue_index][work_index].block_b, owner);
                end
            end
        end
        else if (SKIP_ZERO_BLOCKS) begin
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

    task automatic send_h0_block(input int block_a, input int block_b,
                                 input int engine);
        int global_h0;
        int node_id;
        int h0_id;
        global_h0 = block_a / CORES_PER_H0;
        node_id = global_h0 / H0_COUNT;
        h0_id = global_h0 % H0_COUNT;
        if (verbose_blocks)
            $display("issue H0 block (%0d,%0d) node=%0d h0=%0d engine=%0d cycle=%0d",
                     block_a, block_b, node_id, h0_id, engine, cycle_count);
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
    endtask

    task automatic send_h1_block(input int block_a, input int block_b,
                                 input int engine);
        int node_id;
        node_id = block_a / BLOCKS_PER_H1;
        if (verbose_blocks)
            $display("issue H1 block (%0d,%0d) node=%0d engine=%0d cycle=%0d",
                     block_a, block_b, node_id, engine, cycle_count);
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
    endtask

    task automatic send_cross_block(input int block_a, input int block_b,
                                    input int owner, input int engine);
        int h1_a, h1_b;
        h1_a = block_a / BLOCKS_PER_H1;
        h1_b = block_b / BLOCKS_PER_H1;
        if (verbose_blocks)
            $display("issue cross block (%0d,%0d) owner=%0d engine=%0d cycle=%0d",
                     block_a, block_b, owner, engine, cycle_count);
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
    endtask

    task automatic enqueue_mapped_pair(input int block_a, input int block_b,
                                       input int requested_owner,
                                       input int requested_engine);
        int h0_a, h0_b, h1_a, h1_b, engine, owner;
        block_work_t work;
        work.block_a = block_a;
        work.block_b = block_b;
        if (block_a < 0 || block_b < 0 ||
            block_a >= TOTAL_BLOCK_COUNT || block_b >= TOTAL_BLOCK_COUNT ||
            block_a >= block_b)
            $fatal(1, "invalid scheduled block pair (%0d,%0d)",
                   block_a, block_b);
        h0_a = block_a / CORES_PER_H0;
        h0_b = block_b / CORES_PER_H0;
        h1_a = block_a / BLOCKS_PER_H1;
        h1_b = block_b / BLOCKS_PER_H1;
        if (h0_a == h0_b) begin
            engine = requested_engine >= 0 ? requested_engine : h0_next_engine[h0_a];
            if (engine >= H0_MVM_COUNT)
                $fatal(1, "H0 engine %0d out of range", engine);
            h0_next_engine[h0_a] = (engine + 1) % H0_MVM_COUNT;
            h0_work_queue[h0_a*H0_MVM_COUNT+engine].push_back(work);
            issued_h0_blocks++;
        end
        else if (h1_a == h1_b) begin
            engine = requested_engine >= 0 ? requested_engine : h1_next_engine[h1_a];
            if (engine >= H1_MVM_COUNT)
                $fatal(1, "H1 engine %0d out of range", engine);
            h1_next_engine[h1_a] = (engine + 1) % H1_MVM_COUNT;
            h1_work_queue[h1_a*H1_MVM_COUNT+engine].push_back(work);
            issued_h1_blocks++;
        end
        else begin
            owner = requested_owner >= 0 ? requested_owner :
                    pair_owner[h1_a*NODE_COUNT + h1_b];
            if (owner >= NODE_COUNT)
                $fatal(1, "cross owner %0d out of range", owner);
            engine = requested_engine >= 0 ? requested_engine : cross_next_engine[owner];
            if (engine >= CROSS_MVM_COUNT)
                $fatal(1, "cross engine %0d out of range", engine);
            cross_next_engine[owner] = (engine + 1) % CROSS_MVM_COUNT;
            cross_work_queue[owner*CROSS_MVM_COUNT+engine].push_back(work);
            issued_cross_blocks++;
        end
    endtask

    task automatic enqueue_block_pair(input int block_a, input int block_b);
        enqueue_mapped_pair(block_a, block_b, -1, -1);
    endtask

    task automatic compile_schedule;
        longint unsigned key;
        int block_a, block_b;
        int schedule_file;
        int dump_file;
        int owner, engine, scan_result;
        string dump_path;
        for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++)
            for (int engine = 0; engine < H0_MVM_COUNT; engine++)
                h0_work_queue[h0*H0_MVM_COUNT+engine].delete();
        for (int node = 0; node < NODE_COUNT; node++) begin
            for (int engine = 0; engine < H1_MVM_COUNT; engine++)
                h1_work_queue[node*H1_MVM_COUNT+engine].delete();
            for (int engine = 0; engine < CROSS_MVM_COUNT; engine++)
                cross_work_queue[node*CROSS_MVM_COUNT+engine].delete();
        end
        external_schedule = $value$plusargs("SCHEDULE=%s", schedule_path);
        if (external_schedule) begin
            schedule_file = $fopen(schedule_path, "r");
            if (schedule_file == 0)
                $fatal(1, "cannot open schedule file %s", schedule_path);
            while (!$feof(schedule_file)) begin
                scan_result = $fscanf(schedule_file, "%d %d %d %d\n",
                                      block_a, block_b, owner, engine);
                if (scan_result == 4)
                    enqueue_mapped_pair(block_a, block_b, owner, engine);
                else if (scan_result != -1)
                    $fatal(1, "invalid schedule record in %s", schedule_path);
            end
            $fclose(schedule_file);
            $display("loaded runtime schedule %s", schedule_path);
        end
        else if (SKIP_ZERO_BLOCKS) begin
            foreach (active_block_pair[key]) begin
                block_a = int'(key >> 32);
                block_b = int'(key[31:0]);
                enqueue_block_pair(block_a, block_b);
            end
        end
        else begin
            for (block_a = 0; block_a < TOTAL_BLOCK_COUNT; block_a++)
                for (block_b = block_a + 1; block_b < TOTAL_BLOCK_COUNT; block_b++)
                    enqueue_block_pair(block_a, block_b);
        end
        if ($value$plusargs("DUMP_SCHEDULE=%s", dump_path)) begin
            dump_file = $fopen(dump_path, "w");
            if (dump_file == 0)
                $fatal(1, "cannot create schedule file %s", dump_path);
            for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++)
                for (int e = 0; e < H0_MVM_COUNT; e++)
                    for (int w = 0;
                         w < h0_work_queue[h0*H0_MVM_COUNT+e].size(); w++)
                        $fdisplay(dump_file, "%0d %0d -1 %0d",
                            h0_work_queue[h0*H0_MVM_COUNT+e][w].block_a,
                            h0_work_queue[h0*H0_MVM_COUNT+e][w].block_b, e);
            for (int node = 0; node < NODE_COUNT; node++) begin
                for (int e = 0; e < H1_MVM_COUNT; e++)
                    for (int w = 0;
                         w < h1_work_queue[node*H1_MVM_COUNT+e].size(); w++)
                        $fdisplay(dump_file, "%0d %0d -1 %0d",
                            h1_work_queue[node*H1_MVM_COUNT+e][w].block_a,
                            h1_work_queue[node*H1_MVM_COUNT+e][w].block_b, e);
                for (int e = 0; e < CROSS_MVM_COUNT; e++)
                    for (int w = 0;
                         w < cross_work_queue[node*CROSS_MVM_COUNT+e].size(); w++)
                        $fdisplay(dump_file, "%0d %0d %0d %0d",
                            cross_work_queue[node*CROSS_MVM_COUNT+e][w].block_a,
                            cross_work_queue[node*CROSS_MVM_COUNT+e][w].block_b,
                            node, e);
            end
            $fclose(dump_file);
            $display("wrote runtime schedule %s", dump_path);
        end
    endtask

    task automatic dispatch_h0_engine(input int global_h0, input int engine);
        block_work_t work;
        while (h0_work_queue[global_h0*H0_MVM_COUNT+engine].size() != 0) begin
            work = h0_work_queue[global_h0*H0_MVM_COUNT+engine].pop_front();
            send_h0_block(work.block_a, work.block_b, engine);
        end
    endtask

    task automatic dispatch_h1_engine(input int node, input int engine);
        block_work_t work;
        while (h1_work_queue[node*H1_MVM_COUNT+engine].size() != 0) begin
            work = h1_work_queue[node*H1_MVM_COUNT+engine].pop_front();
            send_h1_block(work.block_a, work.block_b, engine);
        end
    endtask

    task automatic dispatch_cross_engine(input int node, input int engine);
        block_work_t work;
        while (cross_work_queue[node*CROSS_MVM_COUNT+engine].size() != 0) begin
            work = cross_work_queue[node*CROSS_MVM_COUNT+engine].pop_front();
            send_cross_block(work.block_a, work.block_b, node, engine);
        end
    endtask

    // Ramulator-mode commands carry no inline weight stream, so every engine
    // port can be serviced independently from one cycle-driven dispatcher.
    // A blocked port retains valid and metadata while unrelated ports advance.
    task automatic dispatch_ramulator_schedule;
        longint remaining;
        longint dispatch_cycles;
        longint remaining_h0;
        longint remaining_h1;
        longint remaining_cross;
        block_work_t work;
        remaining = issued_h0_blocks + issued_h1_blocks + issued_cross_blocks;
        dispatch_cycles = 0;
        remaining_h0 = issued_h0_blocks;
        remaining_h1 = issued_h1_blocks;
        remaining_cross = issued_cross_blocks;
        h0_cmd_accepted = '0;
        h1_cmd_accepted = '0;
        cross_cmd_accepted = '0;
        h0_cmd_pending = '0;
        h1_cmd_pending = '0;
        cross_cmd_pending = '0;
        $display("compiled concurrent schedule h0=%0d h1=%0d cross=%0d total=%0d",
                 issued_h0_blocks, issued_h1_blocks, issued_cross_blocks,
                 remaining);
        while (remaining != 0) begin
            @(negedge clk);
            dispatch_cycles++;
            for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++) begin
                int node;
                int local_h0;
                node = h0 / H0_COUNT;
                local_h0 = h0 % H0_COUNT;
                for (int engine = 0; engine < H0_MVM_COUNT; engine++) begin
                    if (h0_cmd_accepted[node][local_h0][engine]) begin
                        h0_src_cmd_valid[node][local_h0][engine] = 1'b0;
                        h0_cmd_pending[node][local_h0][engine] = 1'b0;
                        remaining--;
                        remaining_h0--;
                    end
                    else if (!h0_cmd_pending[node][local_h0][engine] &&
                        h0_work_queue[h0*H0_MVM_COUNT+engine].size() != 0) begin
                        work = h0_work_queue[h0*H0_MVM_COUNT+engine].pop_front();
                        h0_src_state_a[node][local_h0][engine] =
                            H0_STATE_INDEX_W'(work.block_a % CORES_PER_H0);
                        h0_src_state_b[node][local_h0][engine] =
                            H0_STATE_INDEX_W'(work.block_b % CORES_PER_H0);
                        h0_src_block_a[node][local_h0][engine] =
                            GLOBAL_BLOCK_ID_W'(work.block_a);
                        h0_src_block_b[node][local_h0][engine] =
                            GLOBAL_BLOCK_ID_W'(work.block_b);
                        h0_src_cmd_valid[node][local_h0][engine] = 1'b1;
                        h0_cmd_pending[node][local_h0][engine] = 1'b1;
                    end
                end
            end
            for (int node = 0; node < NODE_COUNT; node++) begin
                for (int engine = 0; engine < H1_MVM_COUNT; engine++) begin
                    if (h1_cmd_accepted[node][engine]) begin
                        h1_src_cmd_valid[node][engine] = 1'b0;
                        h1_cmd_pending[node][engine] = 1'b0;
                        remaining--;
                        remaining_h1--;
                    end
                    else if (!h1_cmd_pending[node][engine] &&
                        h1_work_queue[node*H1_MVM_COUNT+engine].size() != 0) begin
                        work = h1_work_queue[node*H1_MVM_COUNT+engine].pop_front();
                        h1_src_state_a[node][engine] =
                            H1_STATE_INDEX_W'(work.block_a % BLOCKS_PER_H1);
                        h1_src_state_b[node][engine] =
                            H1_STATE_INDEX_W'(work.block_b % BLOCKS_PER_H1);
                        h1_src_block_a[node][engine] =
                            GLOBAL_BLOCK_ID_W'(work.block_a);
                        h1_src_block_b[node][engine] =
                            GLOBAL_BLOCK_ID_W'(work.block_b);
                        h1_src_cmd_valid[node][engine] = 1'b1;
                        h1_cmd_pending[node][engine] = 1'b1;
                    end
                end
                for (int engine = 0; engine < CROSS_MVM_COUNT; engine++) begin
                    if (cross_cmd_accepted[node][engine]) begin
                        cross_src_cmd_valid[node][engine] = 1'b0;
                        cross_cmd_pending[node][engine] = 1'b0;
                        remaining--;
                        remaining_cross--;
                    end
                    else if (!cross_cmd_pending[node][engine] &&
                        cross_work_queue[node*CROSS_MVM_COUNT+engine].size() != 0) begin
                        work = cross_work_queue[node*CROSS_MVM_COUNT+engine].pop_front();
                        cross_src_state_a[node][engine] =
                            TOP_STATE_INDEX_W'(work.block_a);
                        cross_src_state_b[node][engine] =
                            TOP_STATE_INDEX_W'(work.block_b);
                        cross_src_block_a[node][engine] =
                            GLOBAL_BLOCK_ID_W'(work.block_a);
                        cross_src_block_b[node][engine] =
                            GLOBAL_BLOCK_ID_W'(work.block_b);
                        cross_src_cmd_valid[node][engine] = 1'b1;
                        cross_cmd_pending[node][engine] = 1'b1;
                    end
                end
            end
            if ((dispatch_cycles % 100_000) == 0) begin
                $display("concurrent dispatch cycle=%0d remaining=%0d h0=%0d h1=%0d cross=%0d",
                         dispatch_cycles, remaining, remaining_h0,
                         remaining_h1, remaining_cross);
            end
            if (remaining < 0)
                $fatal(1, "concurrent dispatcher accepted more commands than queued");

            // Valid and ready are now stable for the upcoming rising edge.
            // Retire that transfer on the next pass, after the edge occurred.
            h0_cmd_accepted = h0_src_cmd_valid & h0_src_cmd_ready;
            h1_cmd_accepted = h1_src_cmd_valid & h1_src_cmd_ready;
            cross_cmd_accepted = cross_src_cmd_valid & cross_src_cmd_ready;
        end
        @(negedge clk);
        h0_src_cmd_valid = '0;
        h1_src_cmd_valid = '0;
        cross_src_cmd_valid = '0;
    endtask

    task automatic dispatch_direct_schedule;
        for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++)
            for (int engine = 0; engine < H0_MVM_COUNT; engine++)
                dispatch_h0_engine(h0, engine);
        for (int node = 0; node < NODE_COUNT; node++) begin
            for (int engine = 0; engine < H1_MVM_COUNT; engine++)
                dispatch_h1_engine(node, engine);
            for (int engine = 0; engine < CROSS_MVM_COUNT; engine++)
                dispatch_cross_engine(node, engine);
        end
    endtask

    task automatic drive_schedule;
        if (USE_RAMULATOR)
            dispatch_ramulator_schedule();
        else
            dispatch_direct_schedule();
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

    task automatic report_noc_statistics;
        string stats_path;
        int stats_file;
        longint unsigned total_injected;
        longint unsigned total_injected_packets;
        longint unsigned total_ejected;
        longint unsigned total_link_flits;
        longint unsigned total_link_stalls;
        longint unsigned total_type_flits [0:3];
        longint unsigned hottest_flits;
        longint unsigned most_stalls;
        int hottest_node;
        int hottest_dir;
        int stalled_node;
        int stalled_dir;
        real utilization;
        real pressure;
        real average_hops;

        total_injected = 0;
        total_injected_packets = 0;
        total_ejected = 0;
        total_link_flits = 0;
        total_link_stalls = 0;
        hottest_flits = 0;
        most_stalls = 0;
        hottest_node = 0;
        hottest_dir = 0;
        stalled_node = 0;
        stalled_dir = 0;
        for (int packet = 0; packet < 4; packet++)
            total_type_flits[packet] = 0;

        if (!$value$plusargs("NOC_STATS_FILE=%s", stats_path))
            stats_path = "noc_stats.csv";
        stats_file = $fopen(stats_path, "w");
        if (stats_file == 0)
            $fatal(1, "could not open NoC statistics file %s", stats_path);
        $fdisplay(stats_file,
            "scope,node,x,y,direction,offered_cycles,accepted_flits,stall_cycles,packets,state_flits,partial_flits,epoch_done_flits,other_flits,window_utilization,backpressure_fraction");

        for (int node = 0; node < NODE_COUNT; node++) begin
            total_injected += noc_inject_accepted[node];
            total_injected_packets += noc_inject_packets[node];
            total_ejected += noc_eject_accepted[node];
            for (int packet = 0; packet < 4; packet++)
                total_type_flits[packet] += noc_inject_type_flits[node][packet];
            utilization = noc_monitor_cycles == 0 ? 0.0 :
                real'(noc_inject_accepted[node]) / real'(noc_monitor_cycles);
            pressure = noc_inject_offered[node] == 0 ? 0.0 :
                real'(noc_inject_stalled[node]) / real'(noc_inject_offered[node]);
            $fdisplay(stats_file,
                "inject,%0d,%0d,%0d,local,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0.6f,%0.6f",
                node, node % MESH_X_COUNT, node / MESH_X_COUNT,
                noc_inject_offered[node], noc_inject_accepted[node],
                noc_inject_stalled[node], noc_inject_packets[node],
                noc_inject_type_flits[node][0], noc_inject_type_flits[node][1],
                noc_inject_type_flits[node][2], noc_inject_type_flits[node][3],
                utilization, pressure);

            utilization = noc_monitor_cycles == 0 ? 0.0 :
                real'(noc_eject_accepted[node]) / real'(noc_monitor_cycles);
            pressure = noc_eject_offered[node] == 0 ? 0.0 :
                real'(noc_eject_stalled[node]) / real'(noc_eject_offered[node]);
            $fdisplay(stats_file,
                "eject,%0d,%0d,%0d,local,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0.6f,%0.6f",
                node, node % MESH_X_COUNT, node / MESH_X_COUNT,
                noc_eject_offered[node], noc_eject_accepted[node],
                noc_eject_stalled[node], noc_eject_packets[node],
                noc_eject_type_flits[node][0], noc_eject_type_flits[node][1],
                noc_eject_type_flits[node][2], noc_eject_type_flits[node][3],
                utilization, pressure);

            for (int dir = 0; dir < 4; dir++) begin
                if (is_physical_noc_link(node, dir)) begin
                    total_link_flits += noc_link_accepted[node][dir];
                    total_link_stalls += noc_link_stalled[node][dir];
                    if (noc_link_accepted[node][dir] > hottest_flits) begin
                        hottest_flits = noc_link_accepted[node][dir];
                        hottest_node = node;
                        hottest_dir = dir;
                    end
                    if (noc_link_stalled[node][dir] > most_stalls) begin
                        most_stalls = noc_link_stalled[node][dir];
                        stalled_node = node;
                        stalled_dir = dir;
                    end
                    utilization = noc_monitor_cycles == 0 ? 0.0 :
                        real'(noc_link_accepted[node][dir]) /
                        real'(noc_monitor_cycles);
                    pressure = noc_link_offered[node][dir] == 0 ? 0.0 :
                        real'(noc_link_stalled[node][dir]) /
                        real'(noc_link_offered[node][dir]);
                    $fdisplay(stats_file,
                        "link,%0d,%0d,%0d,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0.6f,%0.6f",
                        node, node % MESH_X_COUNT, node / MESH_X_COUNT,
                        noc_direction_name(dir), noc_link_offered[node][dir],
                        noc_link_accepted[node][dir], noc_link_stalled[node][dir],
                        noc_link_packets[node][dir],
                        noc_link_type_flits[node][dir][0],
                        noc_link_type_flits[node][dir][1],
                        noc_link_type_flits[node][dir][2],
                        noc_link_type_flits[node][dir][3], utilization, pressure);
                end
            end
        end
        $fclose(stats_file);

        average_hops = total_injected == 0 ? 0.0 :
            real'(total_link_flits) / real'(total_injected);
        $display("NOC summary window_cycles=%0d injected_flits=%0d ejected_flits=%0d physical_link_flits=%0d average_hops=%0.3f link_stall_cycles=%0d",
                 noc_monitor_cycles, total_injected, total_ejected,
                 total_link_flits, average_hops, total_link_stalls);
        $display("NOC injected types state=%0d partial=%0d epoch_done=%0d other=%0d packets=%0d",
                 total_type_flits[0], total_type_flits[1],
                 total_type_flits[2], total_type_flits[3],
                 total_injected_packets);
        $display("NOC busiest link node=%0d (%0d,%0d) dir=%s accepted_flits=%0d utilization=%0.3f%%",
                 hottest_node, hottest_node % MESH_X_COUNT,
                 hottest_node / MESH_X_COUNT, noc_direction_name(hottest_dir),
                 hottest_flits,
                 noc_monitor_cycles == 0 ? 0.0 :
                     100.0*real'(hottest_flits)/real'(noc_monitor_cycles));
        $display("NOC most backpressured link node=%0d (%0d,%0d) dir=%s stall_cycles=%0d",
                 stalled_node, stalled_node % MESH_X_COUNT,
                 stalled_node / MESH_X_COUNT, noc_direction_name(stalled_dir),
                 most_stalls);
        $display("NOC detailed CSV: %s", stats_path);
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
        int matlab_expected;
        int matlab_scan_result;
        int block_id, node_id, local_block, h0_id, core_id, spin_id;
`ifdef AZILLA_TIMING_ONLY
        $display("iteration=%0d timing-only: functional state comparison skipped",
                 iteration);
        return;
`endif
        errors = 0;
        for (int spin = 0; spin < TOTAL_SPIN_COUNT; spin++) begin
            block_id = spin / SPIN_COUNT;
            spin_id = spin % SPIN_COUNT;
            node_id = block_id / BLOCKS_PER_H1;
            local_block = block_id % BLOCKS_PER_H1;
            h0_id = local_block / CORES_PER_H0;
            core_id = local_block % CORES_PER_H0;
            if (matlab_golden_file != 0) begin
                matlab_scan_result = $fscanf(matlab_golden_file, "%d", matlab_expected);
                if (matlab_scan_result != 1 ||
                    (matlab_expected != 0 && matlab_expected != 1))
                    $fatal(1, "invalid MATLAB golden value at iteration %0d spin %0d",
                           iteration, spin);
                if (golden_next[spin] !== bit'(matlab_expected))
                    $fatal(1, "MATLAB/internal golden mismatch at iteration %0d spin %0d: MATLAB=%0d internal=%0b",
                           iteration, spin, matlab_expected, golden_next[spin]);
            end
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
        int matlab_spin_count;
        int matlab_iteration_count;
        int matlab_header_result;

        if (RAMULATOR_TCK_PS <= 0)
            $fatal(1, "RAMULATOR_TCK_PS must be positive");
        if ((CLK_PERIOD_PS % SAFE_RAMULATOR_TCK_PS) != 0)
            $fatal(1,
                   "CLK_PERIOD_NS (%0d ns) must be an integer multiple of RAMULATOR_TCK_PS (%0d ps)",
                   CLK_PERIOD_NS, RAMULATOR_TCK_PS);
        validate_configuration();
        initialize_inputs();
        verbose_blocks = $test$plusargs("VERBOSE_BLOCKS");
        rst = 1'b1;
        load_dataset();
        matlab_golden_file = 0;
        if ($value$plusargs("MATLAB_GOLDEN=%s", matlab_golden_path)) begin
            matlab_golden_file = $fopen(matlab_golden_path, "r");
            if (matlab_golden_file == 0)
                $fatal(1, "cannot open MATLAB golden file %s", matlab_golden_path);
            matlab_header_result = $fscanf(matlab_golden_file, "%d %d",
                                            matlab_spin_count,
                                            matlab_iteration_count);
            if (matlab_header_result != 2 ||
                matlab_spin_count != TOTAL_SPIN_COUNT ||
                matlab_iteration_count != ITERATION_COUNT)
                $fatal(1, "MATLAB golden header mismatch: got spins=%0d iterations=%0d, expected spins=%0d iterations=%0d",
                       matlab_spin_count, matlab_iteration_count,
                       TOTAL_SPIN_COUNT, ITERATION_COUNT);
            $display("using MATLAB golden states from %s", matlab_golden_path);
        end
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

            compile_schedule();
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

            // Cores perform state_current <= state_next while in CORE_COMMIT,
            // on the following rising edge. Do not compute the next golden
            // iteration or publish state until that architectural commit has
            // taken effect everywhere.
            @(negedge clk);
        end

        $display("PASS: %0d iteration(s), skip_zero_blocks=%0b, total_cycles=%0d",
                 ITERATION_COUNT, SKIP_ZERO_BLOCKS, cycle_count);
        report_noc_statistics();
        if (USE_RAMULATOR) begin
            az_dram_report();
            az_dram_finalize();
        end
        if (matlab_golden_file != 0)
            $fclose(matlab_golden_file);
        $finish;
    end
endmodule
