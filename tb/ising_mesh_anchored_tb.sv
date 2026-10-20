`timescale 1ns/1ps

import ising_pkg::*;

// Full-system testbench for CIR with H0-anchored symmetric ownership
// (rtl/experimental/anchored).  Derived from tb/ising_mesh_tb.sv; the base
// testbench is unchanged.
//
// Dataset format (stored under tb/datasets):
//   <vertex-count> <known-best-cut>
//   <one-based-source> <one-based-destination> <signed-int8-weight>
//
// The owner assignment is a software decision made by the C++ exact-event
// model (compile_anchored_schedule).  It is consumed, never recomputed, from
// the file written by `azilla_exact ... --execution-mode cir-h0-anchored
// --dump-anchored-schedule PATH` (+ANCHORED_SCHEDULE=PATH, required):
//   geometry <mesh_x> <mesh_y> <h0_per_h1> <cores_per_h0> <h0_mvms> <accumulate> <uplink_packets>
//   job <owner_h0> <block_a> <block_b>     (per owner H0, dispatch order)
//   pub <block> <h1>                        (explicit state publications)
//   end
// Each H0 hierarchy node is filled with every distinct block its jobs
// reference (local and remote), one block per cycle from iter_start, from
// the frozen global state (the model's fill rule: distinct sources + 2).
// Ramulator mode is required (the model always has live DRAM timing).
module ising_mesh_anchored_tb #(
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
    parameter int UPLINK_PACKETS     = 4,
    parameter bit ACCUMULATE         = 1'b1,
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
    // Anchored: H0 state tables are indexed by global block.
    localparam int H0_STATE_INDEX_W =
        (TOTAL_BLOCK_COUNT > 1) ? $clog2(TOTAL_BLOCK_COUNT) : 1;
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
    logic [NODE_COUNT-1:0][H0_COUNT-1:0] h0_state_fill_valid_i, h0_state_fill_ready_o;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][H0_STATE_INDEX_W-1:0] h0_state_fill_index_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0][SPIN_COUNT-1:0] h0_state_fill_data_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0] h0_state_fill_done_i;
    logic [NODE_COUNT-1:0][H0_COUNT-1:0] h0_node_done_o, h0_uplink_flushed_o;

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
    bit golden_next [0:TOTAL_SPIN_COUNT-1];
    longint signed golden_sum [0:TOTAL_SPIN_COUNT-1];
    logic [31:0] golden_lfsr [0:TOTAL_BLOCK_COUNT-1];

    int h0_next_engine [0:TOTAL_H0_COUNT-1];
    int h1_next_engine [0:NODE_COUNT-1];
    int cross_next_engine [0:NODE_COUNT-1];

    typedef struct {
        int block_a;
        int block_b;
    } block_work_t;
    typedef struct {
        int block;
        int h1;
    } publication_t;
    // Ramulator-mode descriptors wait at the physical compute node until its
    // ready-aware round-robin dispatcher selects an available engine.  Only
    // H0 queues carry work in the anchored mode; the H1 and cross queues stay
    // empty but keep the dispatcher structure of the base testbench.
    block_work_t h0_node_work_queue [0:TOTAL_H0_COUNT-1][$];
    block_work_t h1_node_work_queue [0:NODE_COUNT-1][$];
    block_work_t cross_node_work_queue [0:NODE_COUNT-1][$];
    // Explicit publications in file order and, per H0, the ascending list of
    // distinct blocks its jobs reference (state fill).
    publication_t publication_list [$];
    int h0_fill_blocks [0:TOTAL_H0_COUNT-1][$];
    int h0_fill_count [0:TOTAL_H0_COUNT-1];
    int max_fill_count;
    longint fill_writes;

    string dataset_name;
    string dataset_path;
    string schedule_path;
    int schedule_jobs;
    string matlab_golden_path;
    string state_dump_path;
    string solver_schedule_path;
    int runtime_iteration_count;
    int solver_coeff_a [0:ITERATION_COUNT-1];
    int solver_coeff_b [0:ITERATION_COUNT-1];
    int solver_noise [0:ITERATION_COUNT-1];
    int matlab_golden_file;
    int state_dump_file;
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
    logic [NODE_COUNT-1:0][X_W-1:0] noc_local_in_dest_x;
    logic [NODE_COUNT-1:0][Y_W-1:0] noc_local_in_dest_y;
    logic [NODE_COUNT-1:0][SOURCE_ID_W-1:0] noc_local_in_source_id;
    logic [NODE_COUNT-1:0][EPOCH_W-1:0] noc_local_in_epoch;
    logic [NODE_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] noc_local_in_block_id;
    logic [NODE_COUNT-1:0] noc_local_out_valid, noc_local_out_ready;
    logic [NODE_COUNT-1:0][1:0] noc_local_out_type;
    logic [NODE_COUNT-1:0] noc_local_out_last;
    logic [NODE_COUNT-1:0][X_W-1:0] noc_local_out_dest_x;
    logic [NODE_COUNT-1:0][Y_W-1:0] noc_local_out_dest_y;
    logic [NODE_COUNT-1:0][SOURCE_ID_W-1:0] noc_local_out_source_id;
    logic [NODE_COUNT-1:0][EPOCH_W-1:0] noc_local_out_epoch;
    logic [NODE_COUNT-1:0][GLOBAL_BLOCK_ID_W-1:0] noc_local_out_block_id;

    // Optional temporal instrumentation. Cumulative aggregate counters above
    // remain the source of truth; snapshots subtract these saved baselines.
    int noc_timeline_file;
    int noc_event_file;
    int noc_stats_interval;
    longint noc_trace_start;
    longint noc_trace_end;
    int noc_trace_node;
    longint signed noc_inflight_flits;
    longint unsigned noc_peak_inflight_flits;
    longint unsigned noc_interval_start_cycle;
    longint unsigned noc_prev_link_offered [0:NODE_COUNT-1][0:3];
    longint unsigned noc_prev_link_accepted [0:NODE_COUNT-1][0:3];
    longint unsigned noc_prev_link_stalled [0:NODE_COUNT-1][0:3];
    longint unsigned noc_prev_link_packets [0:NODE_COUNT-1][0:3];
    longint unsigned noc_prev_link_types [0:NODE_COUNT-1][0:3][0:3];
    longint unsigned noc_prev_inject_offered [0:NODE_COUNT-1];
    longint unsigned noc_prev_inject_accepted [0:NODE_COUNT-1];
    longint unsigned noc_prev_inject_stalled [0:NODE_COUNT-1];
    longint unsigned noc_prev_inject_packets [0:NODE_COUNT-1];
    longint unsigned noc_prev_inject_types [0:NODE_COUNT-1][0:3];
    longint unsigned noc_prev_eject_offered [0:NODE_COUNT-1];
    longint unsigned noc_prev_eject_accepted [0:NODE_COUNT-1];
    longint unsigned noc_prev_eject_stalled [0:NODE_COUNT-1];
    longint unsigned noc_prev_eject_packets [0:NODE_COUNT-1];
    longint unsigned noc_prev_eject_types [0:NODE_COUNT-1][0:3];
    bit noc_inject_stall_active [0:NODE_COUNT-1];
    bit noc_eject_stall_active [0:NODE_COUNT-1];
    bit noc_link_stall_active [0:NODE_COUNT-1][0:3];

    // Internal hierarchy instrumentation. Endpoint indices [0,H0_COUNT)
    // identify H0 tiles, H0_COUNT identifies the H1-local compute node, and
    // H0_COUNT+1 identifies the cross-H1 compute node. Transfer indices are
    // command, weight, state-table load, and partial-result respectively.
    localparam int INTERNAL_ENDPOINT_COUNT = H0_COUNT + 2;
    localparam int INTERNAL_TRANSFER_COUNT = 4;
    localparam int INTERNAL_CMD = 0;
    localparam int INTERNAL_WEIGHT = 1;
    localparam int INTERNAL_STATE = 2;
    localparam int INTERNAL_PARTIAL = 3;
    longint unsigned internal_offered
        [0:NODE_COUNT-1][0:INTERNAL_ENDPOINT_COUNT-1][0:INTERNAL_TRANSFER_COUNT-1];
    longint unsigned internal_accepted
        [0:NODE_COUNT-1][0:INTERNAL_ENDPOINT_COUNT-1][0:INTERNAL_TRANSFER_COUNT-1];
    longint unsigned internal_stalled
        [0:NODE_COUNT-1][0:INTERNAL_ENDPOINT_COUNT-1][0:INTERNAL_TRANSFER_COUNT-1];
    longint unsigned internal_first_accept
        [0:NODE_COUNT-1][0:INTERNAL_ENDPOINT_COUNT-1][0:INTERNAL_TRANSFER_COUNT-1];
    longint unsigned internal_last_accept
        [0:NODE_COUNT-1][0:INTERNAL_ENDPOINT_COUNT-1][0:INTERNAL_TRANSFER_COUNT-1];
    int internal_event_file;
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
        .TOP_STATE_ENTRY_COUNT(TOP_STATE_ENTRY_COUNT),
        .UPLINK_PACKETS(UPLINK_PACKETS), .ACCUMULATE(ACCUMULATE)
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
                        .STATE_INDEX_W(H0_STATE_INDEX_W),  // global block index
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
    longint diagnostic_dram_ticks = 0;
    always @(negedge clk) begin
        if (USE_RAMULATOR && !rst) begin
            az_dram_tick(RAMULATOR_TICKS_PER_CYCLE);
            diagnostic_dram_ticks += RAMULATOR_TICKS_PER_CYCLE;
            if ($test$plusargs("MEM_TRACE_CLOCK") &&
                (cycle_count < 3 || (cycle_count >= 17289 && cycle_count <= 17292)))
                $display("MEM_CLOCK cycle=%0d ticks=%0d time=%0t",
                    cycle_count, diagnostic_dram_ticks, $time);
        end
    end

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
            assign noc_local_in_dest_x[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_in_dest_x[0];
            assign noc_local_in_dest_y[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_in_dest_y[0];
            assign noc_local_in_source_id[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_in_source_id[0];
            assign noc_local_in_epoch[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_in_epoch[0];
            assign noc_local_in_block_id[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_in_block_id[0];
            assign noc_local_out_valid[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_valid[0];
            assign noc_local_out_ready[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_ready[0];
            assign noc_local_out_type[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_type[0];
            assign noc_local_out_last[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_last[0];
            assign noc_local_out_dest_x[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_dest_x[0];
            assign noc_local_out_dest_y[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_dest_y[0];
            assign noc_local_out_source_id[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_source_id[0];
            assign noc_local_out_epoch[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_epoch[0];
            assign noc_local_out_block_id[N] =
                dut.gen_y[y].gen_x[x].tile.top.router_out_block_id[0];
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

    function automatic string noc_stall_event_name(input bit stalled);
        if (stalled)
            return "stall_begin";
        return "stall_end";
    endfunction

    function automatic bit noc_trace_enabled_for(input int node);
        return cycle_count >= noc_trace_start && cycle_count <= noc_trace_end &&
               (noc_trace_node < 0 || noc_trace_node == node);
    endfunction

    task automatic write_noc_timeline_snapshot(input longint unsigned end_cycle);
        longint unsigned offered, accepted, stalled, packets;
        longint unsigned types [0:3];
        longint unsigned window_cycles;
        real utilization;
        real pressure;
        if (noc_timeline_file == 0 || end_cycle <= noc_interval_start_cycle)
            return;
        window_cycles = end_cycle - noc_interval_start_cycle;
        for (int node = 0; node < NODE_COUNT; node++) begin
            offered = noc_inject_offered[node] - noc_prev_inject_offered[node];
            accepted = noc_inject_accepted[node] - noc_prev_inject_accepted[node];
            stalled = noc_inject_stalled[node] - noc_prev_inject_stalled[node];
            packets = noc_inject_packets[node] - noc_prev_inject_packets[node];
            for (int packet = 0; packet < 4; packet++)
                types[packet] = noc_inject_type_flits[node][packet] -
                                noc_prev_inject_types[node][packet];
            utilization = real'(accepted) / real'(window_cycles);
            pressure = offered == 0 ? 0.0 : real'(stalled) / real'(offered);
            $fdisplay(noc_timeline_file,
                "%0d,%0d,inject,%0d,%0d,%0d,local,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0.6f,%0.6f,%0d,%0d",
                noc_interval_start_cycle, end_cycle, node,
                node % MESH_X_COUNT, node / MESH_X_COUNT,
                offered, accepted, stalled, packets, types[0], types[1],
                types[2], types[3], utilization, pressure,
                noc_inflight_flits, noc_peak_inflight_flits);

            offered = noc_eject_offered[node] - noc_prev_eject_offered[node];
            accepted = noc_eject_accepted[node] - noc_prev_eject_accepted[node];
            stalled = noc_eject_stalled[node] - noc_prev_eject_stalled[node];
            packets = noc_eject_packets[node] - noc_prev_eject_packets[node];
            for (int packet = 0; packet < 4; packet++)
                types[packet] = noc_eject_type_flits[node][packet] -
                                noc_prev_eject_types[node][packet];
            utilization = real'(accepted) / real'(window_cycles);
            pressure = offered == 0 ? 0.0 : real'(stalled) / real'(offered);
            $fdisplay(noc_timeline_file,
                "%0d,%0d,eject,%0d,%0d,%0d,local,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0.6f,%0.6f,%0d,%0d",
                noc_interval_start_cycle, end_cycle, node,
                node % MESH_X_COUNT, node / MESH_X_COUNT,
                offered, accepted, stalled, packets, types[0], types[1],
                types[2], types[3], utilization, pressure,
                noc_inflight_flits, noc_peak_inflight_flits);

            for (int dir = 0; dir < 4; dir++) begin
                if (is_physical_noc_link(node, dir)) begin
                    offered = noc_link_offered[node][dir] - noc_prev_link_offered[node][dir];
                    accepted = noc_link_accepted[node][dir] - noc_prev_link_accepted[node][dir];
                    stalled = noc_link_stalled[node][dir] - noc_prev_link_stalled[node][dir];
                    packets = noc_link_packets[node][dir] - noc_prev_link_packets[node][dir];
                    for (int packet = 0; packet < 4; packet++)
                        types[packet] = noc_link_type_flits[node][dir][packet] -
                            noc_prev_link_types[node][dir][packet];
                    utilization = real'(accepted) / real'(window_cycles);
                    pressure = offered == 0 ? 0.0 : real'(stalled) / real'(offered);
                    $fdisplay(noc_timeline_file,
                        "%0d,%0d,link,%0d,%0d,%0d,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0.6f,%0.6f,%0d,%0d",
                        noc_interval_start_cycle, end_cycle, node,
                        node % MESH_X_COUNT, node / MESH_X_COUNT,
                        noc_direction_name(dir), offered, accepted, stalled,
                        packets, types[0], types[1], types[2], types[3],
                        utilization, pressure, noc_inflight_flits,
                        noc_peak_inflight_flits);
                end
            end

            noc_prev_inject_offered[node] = noc_inject_offered[node];
            noc_prev_inject_accepted[node] = noc_inject_accepted[node];
            noc_prev_inject_stalled[node] = noc_inject_stalled[node];
            noc_prev_inject_packets[node] = noc_inject_packets[node];
            noc_prev_eject_offered[node] = noc_eject_offered[node];
            noc_prev_eject_accepted[node] = noc_eject_accepted[node];
            noc_prev_eject_stalled[node] = noc_eject_stalled[node];
            noc_prev_eject_packets[node] = noc_eject_packets[node];
            for (int packet = 0; packet < 4; packet++) begin
                noc_prev_inject_types[node][packet] = noc_inject_type_flits[node][packet];
                noc_prev_eject_types[node][packet] = noc_eject_type_flits[node][packet];
            end
            for (int dir = 0; dir < 4; dir++) begin
                noc_prev_link_offered[node][dir] = noc_link_offered[node][dir];
                noc_prev_link_accepted[node][dir] = noc_link_accepted[node][dir];
                noc_prev_link_stalled[node][dir] = noc_link_stalled[node][dir];
                noc_prev_link_packets[node][dir] = noc_link_packets[node][dir];
                for (int packet = 0; packet < 4; packet++)
                    noc_prev_link_types[node][dir][packet] =
                        noc_link_type_flits[node][dir][packet];
            end
        end
        noc_interval_start_cycle = end_cycle;
        $fflush(noc_timeline_file);
    endtask

    initial begin : configure_noc_temporal_trace
        string timeline_path;
        string event_path;
        string internal_event_path;
        noc_timeline_file = 0;
        noc_event_file = 0;
        internal_event_file = 0;
        noc_stats_interval = 100;
        noc_trace_start = 0;
        noc_trace_end = 64'h7fff_ffff_ffff_ffff;
        noc_trace_node = -1;
        void'($value$plusargs("NOC_STATS_INTERVAL=%d", noc_stats_interval));
        void'($value$plusargs("NOC_TRACE_START=%d", noc_trace_start));
        void'($value$plusargs("NOC_TRACE_END=%d", noc_trace_end));
        void'($value$plusargs("NOC_TRACE_NODE=%d", noc_trace_node));
        if (noc_stats_interval <= 0)
            $fatal(1, "NOC_STATS_INTERVAL must be positive");
        if ($value$plusargs("NOC_TIMELINE_FILE=%s", timeline_path)) begin
            noc_timeline_file = $fopen(timeline_path, "w");
            if (noc_timeline_file == 0)
                $fatal(1, "could not open NoC timeline file %s", timeline_path);
            $fdisplay(noc_timeline_file,
                "interval_start,interval_end,scope,node,x,y,direction,offered,accepted,stall_cycles,packets,state_flits,partial_flits,epoch_done_flits,other_flits,utilization,backpressure,inflight_end,peak_inflight");
        end
        if ($value$plusargs("NOC_EVENT_FILE=%s", event_path)) begin
            noc_event_file = $fopen(event_path, "w");
            if (noc_event_file == 0)
                $fatal(1, "could not open NoC event file %s", event_path);
            $fdisplay(noc_event_file,
                "cycle,event,scope,node,x,y,direction,packet_type,source_id,epoch,block_id,dest_x,dest_y,last,valid,ready,inflight");
        end
        if ($value$plusargs("INTERNAL_EVENT_FILE=%s", internal_event_path)) begin
            internal_event_file = $fopen(internal_event_path, "w");
            if (internal_event_file == 0)
                $fatal(1, "could not open internal event file %s",
                       internal_event_path);
            $fdisplay(internal_event_file,
                "cycle,event,node,endpoint_class,endpoint_id,transfer,accepted_lanes");
        end
    end

    always @(posedge clk) begin : collect_noc_statistics
        int packet_type;
        int accepted_injections;
        int accepted_ejections;
        bit stalled_now;
        if (rst) begin
            noc_stats_active = 1'b0;
            noc_monitor_cycles = 0;
            noc_inflight_flits = 0;
            noc_peak_inflight_flits = 0;
            noc_interval_start_cycle = 0;
            for (int node = 0; node < NODE_COUNT; node++) begin
                noc_inject_offered[node] = 0;
                noc_inject_accepted[node] = 0;
                noc_inject_stalled[node] = 0;
                noc_inject_packets[node] = 0;
                noc_eject_offered[node] = 0;
                noc_eject_accepted[node] = 0;
                noc_eject_stalled[node] = 0;
                noc_eject_packets[node] = 0;
                noc_inject_stall_active[node] = 0;
                noc_eject_stall_active[node] = 0;
                noc_prev_inject_offered[node] = 0;
                noc_prev_inject_accepted[node] = 0;
                noc_prev_inject_stalled[node] = 0;
                noc_prev_inject_packets[node] = 0;
                noc_prev_eject_offered[node] = 0;
                noc_prev_eject_accepted[node] = 0;
                noc_prev_eject_stalled[node] = 0;
                noc_prev_eject_packets[node] = 0;
                for (int packet = 0; packet < 4; packet++) begin
                    noc_inject_type_flits[node][packet] = 0;
                    noc_eject_type_flits[node][packet] = 0;
                    noc_prev_inject_types[node][packet] = 0;
                    noc_prev_eject_types[node][packet] = 0;
                end
                for (int dir = 0; dir < 4; dir++) begin
                    noc_link_offered[node][dir] = 0;
                    noc_link_accepted[node][dir] = 0;
                    noc_link_stalled[node][dir] = 0;
                    noc_link_packets[node][dir] = 0;
                    noc_link_stall_active[node][dir] = 0;
                    noc_prev_link_offered[node][dir] = 0;
                    noc_prev_link_accepted[node][dir] = 0;
                    noc_prev_link_stalled[node][dir] = 0;
                    noc_prev_link_packets[node][dir] = 0;
                    for (int packet = 0; packet < 4; packet++) begin
                        noc_link_type_flits[node][dir][packet] = 0;
                        noc_prev_link_types[node][dir][packet] = 0;
                    end
                end
            end
        end
        else begin
            if (|iter_start_i && !noc_stats_active) begin
                noc_stats_active = 1'b1;
                noc_interval_start_cycle = cycle_count;
            end
            if (noc_stats_active) begin
                accepted_injections = 0;
                accepted_ejections = 0;
                noc_monitor_cycles++;
                for (int node = 0; node < NODE_COUNT; node++) begin
                    if (noc_local_in_valid[node]) begin
                        noc_inject_offered[node]++;
                        if (noc_local_in_ready[node]) begin
                            accepted_injections++;
                            noc_inject_accepted[node]++;
                            packet_type = noc_type_index(noc_local_in_type[node]);
                            noc_inject_type_flits[node][packet_type]++;
                            if (noc_local_in_last[node])
                                noc_inject_packets[node]++;
                            if (noc_event_file != 0 && noc_trace_enabled_for(node))
                                $fdisplay(noc_event_file,
                                    "%0d,accept,inject,%0d,%0d,%0d,local,%0d,%0d,%0d,%0d,%0d,%0d,%0d,1,1,%0d",
                                    cycle_count, node, node % MESH_X_COUNT,
                                    node / MESH_X_COUNT, noc_local_in_type[node],
                                    noc_local_in_source_id[node], noc_local_in_epoch[node],
                                    noc_local_in_block_id[node], noc_local_in_dest_x[node],
                                    noc_local_in_dest_y[node], noc_local_in_last[node],
                                    noc_inflight_flits);
                        end
                        else
                            noc_inject_stalled[node]++;
                    end
                    stalled_now = noc_local_in_valid[node] && !noc_local_in_ready[node];
                    if (noc_event_file != 0 && noc_trace_enabled_for(node) &&
                        stalled_now != noc_inject_stall_active[node])
                        $fdisplay(noc_event_file,
                            "%0d,%s,inject,%0d,%0d,%0d,local,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
                            cycle_count, noc_stall_event_name(stalled_now), node,
                            node % MESH_X_COUNT, node / MESH_X_COUNT,
                            noc_local_in_type[node], noc_local_in_source_id[node],
                            noc_local_in_epoch[node], noc_local_in_block_id[node],
                            noc_local_in_dest_x[node], noc_local_in_dest_y[node],
                            noc_local_in_last[node], noc_local_in_valid[node],
                            noc_local_in_ready[node], noc_inflight_flits);
                    noc_inject_stall_active[node] = stalled_now;
                    if (noc_local_out_valid[node]) begin
                        noc_eject_offered[node]++;
                        if (noc_local_out_ready[node]) begin
                            accepted_ejections++;
                            noc_eject_accepted[node]++;
                            packet_type = noc_type_index(noc_local_out_type[node]);
                            noc_eject_type_flits[node][packet_type]++;
                            if (noc_local_out_last[node])
                                noc_eject_packets[node]++;
                            if (noc_event_file != 0 && noc_trace_enabled_for(node))
                                $fdisplay(noc_event_file,
                                    "%0d,accept,eject,%0d,%0d,%0d,local,%0d,%0d,%0d,%0d,%0d,%0d,%0d,1,1,%0d",
                                    cycle_count, node, node % MESH_X_COUNT,
                                    node / MESH_X_COUNT, noc_local_out_type[node],
                                    noc_local_out_source_id[node], noc_local_out_epoch[node],
                                    noc_local_out_block_id[node], noc_local_out_dest_x[node],
                                    noc_local_out_dest_y[node], noc_local_out_last[node],
                                    noc_inflight_flits);
                        end
                        else
                            noc_eject_stalled[node]++;
                    end
                    stalled_now = noc_local_out_valid[node] && !noc_local_out_ready[node];
                    if (noc_event_file != 0 && noc_trace_enabled_for(node) &&
                        stalled_now != noc_eject_stall_active[node])
                        $fdisplay(noc_event_file,
                            "%0d,%s,eject,%0d,%0d,%0d,local,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
                            cycle_count, noc_stall_event_name(stalled_now), node,
                            node % MESH_X_COUNT, node / MESH_X_COUNT,
                            noc_local_out_type[node], noc_local_out_source_id[node],
                            noc_local_out_epoch[node], noc_local_out_block_id[node],
                            noc_local_out_dest_x[node], noc_local_out_dest_y[node],
                            noc_local_out_last[node], noc_local_out_valid[node],
                            noc_local_out_ready[node], noc_inflight_flits);
                    noc_eject_stall_active[node] = stalled_now;
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
                                if (noc_event_file != 0 && noc_trace_enabled_for(node))
                                    $fdisplay(noc_event_file,
                                        "%0d,accept,link,%0d,%0d,%0d,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,1,1,%0d",
                                        cycle_count, node, node % MESH_X_COUNT,
                                        node / MESH_X_COUNT, noc_direction_name(dir),
                                        dut.link_out_type[node][dir],
                                        dut.link_out_source_id[node][dir],
                                        dut.link_out_epoch[node][dir],
                                        dut.link_out_block_id[node][dir],
                                        dut.link_out_dest_x[node][dir],
                                        dut.link_out_dest_y[node][dir],
                                        dut.link_out_last[node][dir], noc_inflight_flits);
                            end
                            else
                                noc_link_stalled[node][dir]++;
                        end
                        stalled_now = is_physical_noc_link(node, dir) &&
                            dut.link_out_valid[node][dir] &&
                            !dut.link_out_ready[node][dir];
                        if (noc_event_file != 0 && noc_trace_enabled_for(node) &&
                            stalled_now != noc_link_stall_active[node][dir])
                            $fdisplay(noc_event_file,
                                "%0d,%s,link,%0d,%0d,%0d,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
                                cycle_count, noc_stall_event_name(stalled_now), node,
                                node % MESH_X_COUNT, node / MESH_X_COUNT,
                                noc_direction_name(dir), dut.link_out_type[node][dir],
                                dut.link_out_source_id[node][dir],
                                dut.link_out_epoch[node][dir],
                                dut.link_out_block_id[node][dir],
                                dut.link_out_dest_x[node][dir],
                                dut.link_out_dest_y[node][dir],
                                dut.link_out_last[node][dir],
                                dut.link_out_valid[node][dir],
                                dut.link_out_ready[node][dir], noc_inflight_flits);
                        noc_link_stall_active[node][dir] = stalled_now;
                    end
                end
                noc_inflight_flits += longint'(accepted_injections) -
                                      longint'(accepted_ejections);
                if (noc_inflight_flits < 0)
                    $fatal(1, "NoC in-flight flit count became negative");
                if (noc_inflight_flits > noc_peak_inflight_flits)
                    noc_peak_inflight_flits = noc_inflight_flits;
                if (noc_timeline_file != 0 &&
                    noc_monitor_cycles % longint'(noc_stats_interval) == 0)
                    write_noc_timeline_snapshot(cycle_count + 1);
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
        if (runtime_iteration_count <= 0 ||
            runtime_iteration_count > ITERATION_COUNT)
            $fatal(1,
                   "runtime iteration count must be between 1 and compiled maximum %0d (got %0d)",
                   ITERATION_COUNT, runtime_iteration_count);
        if (!is_power_of_two(MESH_X_COUNT) || !is_power_of_two(MESH_Y_COUNT))
            $fatal(1, "mesh dimensions must each be powers of two");
        if (!is_power_of_two(H0_COUNT))
            $fatal(1, "H0_COUNT must be a power of two");
        if (!is_power_of_two(CORES_PER_H0))
            $fatal(1, "CORES_PER_H0 must be a power of two");
        if (!is_power_of_two(H0_MVM_COUNT) || H0_MVM_COUNT > CORES_PER_H0)
            $fatal(1, "H0_MVM_COUNT must be a power of two no larger than CORES_PER_H0");
        if (!USE_RAMULATOR)
            $fatal(1, "the anchored testbench requires USE_RAMULATOR=1 (model always has live DRAM timing)");
        if (UPLINK_PACKETS < 1)
            $fatal(1, "UPLINK_PACKETS must be positive");
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
        h0_state_fill_valid_i = '0; h0_state_fill_index_i = '0;
        h0_state_fill_data_i = '0; h0_state_fill_done_i = '0;
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
        // Dense convention of ising_mesh_tb: with zero-block skipping off,
        // every unordered off-diagonal block pair is occupied (header-only
        // datasets; weights read as zero, timing-only runs).
        if (!SKIP_ZERO_BLOCKS)
            for (int a = 0; a < TOTAL_BLOCK_COUNT; a++)
                for (int b = a + 1; b < TOTAL_BLOCK_COUNT; b++)
                    active_block_pair[block_pair_key(a, b)] = 1'b1;
        $display("dataset=%s spins=%0d edge_records=%0d known_cut=%0d",
                 dataset_name, dataset_vertex_count, dataset_edge_records,
                 dataset_known_cut);
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

    function automatic logic [SPIN_COUNT-1:0] frozen_block_state(input int block_id);
        int node_id, local_block, h0_id, core_id;
        node_id = block_id / BLOCKS_PER_H1;
        local_block = block_id % BLOCKS_PER_H1;
        h0_id = local_block / CORES_PER_H0;
        core_id = local_block % CORES_PER_H0;
        return state_current_o[node_id][h0_id][core_id];
    endfunction

    // Insert a block into an H0's ascending distinct fill list.
    task automatic note_fill_block(input int global_h0, input int block_id);
        int position;
        position = 0;
        while (position < h0_fill_blocks[global_h0].size() &&
               h0_fill_blocks[global_h0][position] < block_id)
            position++;
        if (position < h0_fill_blocks[global_h0].size() &&
            h0_fill_blocks[global_h0][position] == block_id)
            return;
        h0_fill_blocks[global_h0].insert(position, block_id);
    endtask

    // Read the compiled anchored schedule written by the C++ model.
    task automatic load_anchored_schedule;
        int schedule_file;
        int scan_result;
        string keyword;
        int owner, block_a, block_b, h1;
        int mesh_x, mesh_y, h0_per_h1, cores_per_h0, h0_mvms, accumulate, packets;
        bit saw_geometry, saw_end;
        bit job_seen [longint unsigned];
        longint unsigned key;
        int active_pairs;
        block_work_t work;
        publication_t publication;

        for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++) begin
            h0_node_work_queue[h0].delete();
            h0_fill_blocks[h0].delete();
            h0_fill_count[h0] = 0;
        end
        for (int node = 0; node < NODE_COUNT; node++) begin
            h1_node_work_queue[node].delete();
            cross_node_work_queue[node].delete();
        end
        publication_list.delete();
        schedule_jobs = 0;
        max_fill_count = 0;
        saw_geometry = 1'b0;
        saw_end = 1'b0;

        if (!$value$plusargs("ANCHORED_SCHEDULE=%s", schedule_path))
            $fatal(1, "+ANCHORED_SCHEDULE=<path> is required");
        schedule_file = $fopen(schedule_path, "r");
        if (schedule_file == 0)
            $fatal(1, "cannot open anchored schedule %s", schedule_path);
        while (!saw_end && !$feof(schedule_file)) begin
            scan_result = $fscanf(schedule_file, "%s", keyword);
            if (scan_result != 1)
                break;
            if (keyword == "geometry") begin
                scan_result = $fscanf(schedule_file, "%d %d %d %d %d %d %d", mesh_x, mesh_y,
                                      h0_per_h1, cores_per_h0, h0_mvms, accumulate, packets);
                if (scan_result != 7)
                    $fatal(1, "invalid geometry record in %s", schedule_path);
                if (mesh_x != MESH_X_COUNT || mesh_y != MESH_Y_COUNT ||
                    h0_per_h1 != H0_COUNT || cores_per_h0 != CORES_PER_H0 ||
                    h0_mvms != H0_MVM_COUNT)
                    $fatal(1, "schedule geometry %0dx%0d h0=%0d cores=%0d mvms=%0d does not match the build %0dx%0d h0=%0d cores=%0d mvms=%0d",
                           mesh_x, mesh_y, h0_per_h1, cores_per_h0, h0_mvms,
                           MESH_X_COUNT, MESH_Y_COUNT, H0_COUNT, CORES_PER_H0, H0_MVM_COUNT);
                if (accumulate != int'(ACCUMULATE) || packets != UPLINK_PACKETS)
                    $fatal(1, "schedule uplink mode accumulate=%0d packets=%0d does not match the build accumulate=%0d packets=%0d",
                           accumulate, packets, ACCUMULATE, UPLINK_PACKETS);
                saw_geometry = 1'b1;
            end
            else if (keyword == "job") begin
                scan_result = $fscanf(schedule_file, "%d %d %d", owner, block_a, block_b);
                if (scan_result != 3)
                    $fatal(1, "invalid job record in %s", schedule_path);
                if (owner < 0 || owner >= TOTAL_H0_COUNT)
                    $fatal(1, "job owner %0d out of range", owner);
                if (block_a < 0 || block_b < 0 ||
                    block_a >= TOTAL_BLOCK_COUNT || block_b >= TOTAL_BLOCK_COUNT ||
                    block_a >= block_b)
                    $fatal(1, "invalid scheduled block pair (%0d,%0d)", block_a, block_b);
                if (owner != block_a / CORES_PER_H0 && owner != block_b / CORES_PER_H0)
                    $fatal(1, "job (%0d,%0d) owner %0d is not an endpoint H0",
                           block_a, block_b, owner);
                key = block_pair_key(block_a, block_b);
                if (job_seen.exists(key))
                    $fatal(1, "duplicate scheduled block pair (%0d,%0d)", block_a, block_b);
                if (!active_block_pair.exists(key))
                    $fatal(1, "scheduled block pair (%0d,%0d) has no edge in the dataset",
                           block_a, block_b);
                job_seen[key] = 1'b1;
                work.block_a = block_a;
                work.block_b = block_b;
                h0_node_work_queue[owner].push_back(work);
                note_fill_block(owner, block_a);
                note_fill_block(owner, block_b);
                schedule_jobs++;
            end
            else if (keyword == "pub") begin
                scan_result = $fscanf(schedule_file, "%d %d", block_a, h1);
                if (scan_result != 2)
                    $fatal(1, "invalid pub record in %s", schedule_path);
                if (block_a < 0 || block_a >= TOTAL_BLOCK_COUNT || h1 < 0 || h1 >= NODE_COUNT)
                    $fatal(1, "publication (%0d,%0d) out of range", block_a, h1);
                if (block_a / BLOCKS_PER_H1 == h1)
                    $fatal(1, "publication (%0d,%0d) does not leave its H1", block_a, h1);
                publication.block = block_a;
                publication.h1 = h1;
                publication_list.push_back(publication);
            end
            else if (keyword == "end") begin
                saw_end = 1'b1;
            end
            else begin
                $fatal(1, "unknown record '%s' in %s", keyword, schedule_path);
            end
        end
        $fclose(schedule_file);
        if (!saw_geometry || !saw_end)
            $fatal(1, "anchored schedule %s lacks its geometry or end record", schedule_path);
        active_pairs = 0;
        foreach (active_block_pair[key]) active_pairs++;
        if (schedule_jobs != active_pairs)
            $fatal(1, "schedule has %0d jobs but the dataset has %0d occupied block pairs",
                   schedule_jobs, active_pairs);
        for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++) begin
            h0_fill_count[h0] = h0_fill_blocks[h0].size();
            if (h0_fill_count[h0] > max_fill_count)
                max_fill_count = h0_fill_count[h0];
        end
        issued_h0_blocks = schedule_jobs;
        issued_h1_blocks = 0;
        issued_cross_blocks = 0;
        $display("loaded anchored schedule %s jobs=%0d publications=%0d max_fill=%0d",
                 schedule_path, schedule_jobs, publication_list.size(), max_fill_count);
    endtask

    // Fill every H0 hierarchy node with the distinct blocks its jobs
    // reference, one block per cycle per H0, starting on the cycle after the
    // iter_start pulse (the model's fill rule).  Called from the negedge on
    // which iter_start is deasserted; the zero-block case is handled by
    // asserting state_fill_done_i together with iter_start.
    task automatic fill_h0_states;
        int block_id;
        fill_writes = 0;
        for (int step = 0; step < max_fill_count; step++) begin
            for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++) begin
                int node, local_h0;
                node = h0 / H0_COUNT;
                local_h0 = h0 % H0_COUNT;
                if (step < h0_fill_count[h0]) begin
                    block_id = h0_fill_blocks[h0][step];
                    h0_state_fill_valid_i[node][local_h0] = 1'b1;
                    h0_state_fill_index_i[node][local_h0] = H0_STATE_INDEX_W'(block_id);
                    h0_state_fill_data_i[node][local_h0] = frozen_block_state(block_id);
                    h0_state_fill_done_i[node][local_h0] = step == h0_fill_count[h0] - 1;
                end
                else begin
                    h0_state_fill_valid_i[node][local_h0] = 1'b0;
                    h0_state_fill_done_i[node][local_h0] = 1'b0;
                end
            end
            // The fill port is ready whenever the tile is in its fill state
            // (no dependency on the testbench inputs), so the settled value
            // at this falling edge is the one sampled by the next rising edge.
            for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++) begin
                int node, local_h0;
                node = h0 / H0_COUNT;
                local_h0 = h0 % H0_COUNT;
                if (step < h0_fill_count[h0]) begin
                    if (!h0_state_fill_ready_o[node][local_h0])
                        $fatal(1, "H0 %0d did not accept state fill step %0d at cycle %0d",
                               h0, step, cycle_count);
                    fill_writes++;
                end
            end
            @(negedge clk);
        end
        h0_state_fill_valid_i = '0;
        h0_state_fill_done_i = '0;
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
                int base_engine;
                node = h0 / H0_COUNT;
                local_h0 = h0 % H0_COUNT;
                for (int engine = 0; engine < H0_MVM_COUNT; engine++) begin
                    if (h0_cmd_accepted[node][local_h0][engine]) begin
                        h0_src_cmd_valid[node][local_h0][engine] = 1'b0;
                        h0_cmd_pending[node][local_h0][engine] = 1'b0;
                        remaining--;
                        remaining_h0--;
                    end
                end
                base_engine = h0_next_engine[h0];
                for (int offset = 0; offset < H0_MVM_COUNT; offset++) begin
                    int engine;
                    engine = (base_engine + offset) % H0_MVM_COUNT;
                    if (h0_node_work_queue[h0].size() != 0 &&
                        !h0_cmd_pending[node][local_h0][engine] &&
                        !h0_cmd_accepted[node][local_h0][engine] &&
                        h0_src_cmd_ready[node][local_h0][engine]) begin
                        work = h0_node_work_queue[h0].pop_front();
                        // Anchored: global state indices.
                        h0_src_state_a[node][local_h0][engine] =
                            H0_STATE_INDEX_W'(work.block_a);
                        h0_src_state_b[node][local_h0][engine] =
                            H0_STATE_INDEX_W'(work.block_b);
                        h0_src_block_a[node][local_h0][engine] =
                            GLOBAL_BLOCK_ID_W'(work.block_a);
                        h0_src_block_b[node][local_h0][engine] =
                            GLOBAL_BLOCK_ID_W'(work.block_b);
                        h0_src_cmd_valid[node][local_h0][engine] = 1'b1;
                        h0_cmd_pending[node][local_h0][engine] = 1'b1;
                        h0_next_engine[h0] = (engine + 1) % H0_MVM_COUNT;
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
                end
                begin
                    int base_engine;
                    base_engine = h1_next_engine[node];
                    for (int offset = 0; offset < H1_MVM_COUNT; offset++) begin
                        int engine;
                        engine = (base_engine + offset) % H1_MVM_COUNT;
                        if (h1_node_work_queue[node].size() != 0 &&
                            !h1_cmd_pending[node][engine] &&
                            !h1_cmd_accepted[node][engine] &&
                            h1_src_cmd_ready[node][engine]) begin
                        work = h1_node_work_queue[node].pop_front();
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
                        h1_next_engine[node] = (engine + 1) % H1_MVM_COUNT;
                        end
                    end
                end
                for (int engine = 0; engine < CROSS_MVM_COUNT; engine++) begin
                    if (cross_cmd_accepted[node][engine]) begin
                        cross_src_cmd_valid[node][engine] = 1'b0;
                        cross_cmd_pending[node][engine] = 1'b0;
                        remaining--;
                        remaining_cross--;
                    end
                end
                begin
                    int base_engine;
                    base_engine = cross_next_engine[node];
                    for (int offset = 0; offset < CROSS_MVM_COUNT; offset++) begin
                        int engine;
                        engine = (base_engine + offset) % CROSS_MVM_COUNT;
                        if (cross_node_work_queue[node].size() != 0 &&
                            !cross_cmd_pending[node][engine] &&
                            !cross_cmd_accepted[node][engine] &&
                            cross_src_cmd_ready[node][engine]) begin
                        work = cross_node_work_queue[node].pop_front();
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
                        cross_next_engine[node] = (engine + 1) % CROSS_MVM_COUNT;
                        end
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

    task automatic drive_schedule;
        dispatch_ramulator_schedule();
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

    function automatic string internal_transfer_name(input int transfer);
        case (transfer)
            INTERNAL_CMD: return "command";
            INTERNAL_WEIGHT: return "weight";
            INTERNAL_STATE: return "state_load";
            default: return "partial";
        endcase
    endfunction

    task automatic write_internal_accept_event(
        input int node,
        input string endpoint_class,
        input int endpoint_id,
        input int transfer,
        input int accepted_lanes
    );
        if (internal_event_file != 0 && accepted_lanes != 0)
            $fdisplay(internal_event_file, "%0d,accept,%0d,%s,%0d,%s,%0d",
                cycle_count, node, endpoint_class, endpoint_id,
                internal_transfer_name(transfer), accepted_lanes);
    endtask

    // Observe existing ready/valid boundaries without adding ports or state to
    // synthesizable RTL. Counts are lane-transfers: if two engines handshake
    // in one cycle, two transfers are recorded.
    generate
        for (genvar internal_node = 0; internal_node < NODE_COUNT;
             internal_node++) begin : monitor_internal_node
            localparam int IX = internal_node % MESH_X_COUNT;
            localparam int IY = internal_node / MESH_X_COUNT;
            for (genvar internal_h0 = 0; internal_h0 < H0_COUNT;
                 internal_h0++) begin : monitor_internal_h0
                always @(posedge clk) begin : collect_h0_internal
                    int offered_count;
                    int accepted_count;
                    int endpoint;
                    endpoint = internal_h0;
                    if (rst) begin
                        for (int transfer = 0; transfer < INTERNAL_TRANSFER_COUNT;
                             transfer++) begin
                            internal_offered[internal_node][endpoint][transfer] <= 0;
                            internal_accepted[internal_node][endpoint][transfer] <= 0;
                            internal_stalled[internal_node][endpoint][transfer] <= 0;
                            internal_first_accept[internal_node][endpoint][transfer] <= 0;
                            internal_last_accept[internal_node][endpoint][transfer] <= 0;
                        end
                    end
                    else if (noc_stats_active) begin
                        offered_count = $countones(
                            h0_dma_cmd_valid_i[internal_node][internal_h0]);
                        accepted_count = $countones(
                            h0_dma_cmd_valid_i[internal_node][internal_h0] &
                            h0_dma_cmd_ready_o[internal_node][internal_h0]);
                        write_internal_accept_event(internal_node, "h0", internal_h0,
                            INTERNAL_CMD, accepted_count);
                        internal_offered[internal_node][endpoint][INTERNAL_CMD] += offered_count;
                        internal_accepted[internal_node][endpoint][INTERNAL_CMD] += accepted_count;
                        internal_stalled[internal_node][endpoint][INTERNAL_CMD] +=
                            offered_count - accepted_count;
                        if (accepted_count != 0) begin
                            if (internal_first_accept[internal_node][endpoint][INTERNAL_CMD] == 0)
                                internal_first_accept[internal_node][endpoint][INTERNAL_CMD] <= cycle_count;
                            internal_last_accept[internal_node][endpoint][INTERNAL_CMD] <= cycle_count;
                        end

                        offered_count = $countones(
                            h0_dma_weight_valid_i[internal_node][internal_h0]);
                        accepted_count = $countones(
                            h0_dma_weight_valid_i[internal_node][internal_h0] &
                            h0_dma_weight_ready_o[internal_node][internal_h0]);
                        write_internal_accept_event(internal_node, "h0", internal_h0,
                            INTERNAL_WEIGHT, accepted_count);
                        internal_offered[internal_node][endpoint][INTERNAL_WEIGHT] += offered_count;
                        internal_accepted[internal_node][endpoint][INTERNAL_WEIGHT] += accepted_count;
                        internal_stalled[internal_node][endpoint][INTERNAL_WEIGHT] +=
                            offered_count - accepted_count;
                        if (accepted_count != 0) begin
                            if (internal_first_accept[internal_node][endpoint][INTERNAL_WEIGHT] == 0)
                                internal_first_accept[internal_node][endpoint][INTERNAL_WEIGHT] <= cycle_count;
                            internal_last_accept[internal_node][endpoint][INTERNAL_WEIGHT] <= cycle_count;
                        end

                        offered_count = dut.gen_y[IY].gen_x[IX].tile.h1.
                            gen_h0_tiles[internal_h0].child_h0.node_state_valid;
                        accepted_count = offered_count &&
                            dut.gen_y[IY].gen_x[IX].tile.h1.
                            gen_h0_tiles[internal_h0].child_h0.node_state_ready;
                        write_internal_accept_event(internal_node, "h0", internal_h0,
                            INTERNAL_STATE, accepted_count);
                        internal_offered[internal_node][endpoint][INTERNAL_STATE] += offered_count;
                        internal_accepted[internal_node][endpoint][INTERNAL_STATE] += accepted_count;
                        internal_stalled[internal_node][endpoint][INTERNAL_STATE] +=
                            offered_count - accepted_count;
                        if (accepted_count != 0) begin
                            if (internal_first_accept[internal_node][endpoint][INTERNAL_STATE] == 0)
                                internal_first_accept[internal_node][endpoint][INTERNAL_STATE] <= cycle_count;
                            internal_last_accept[internal_node][endpoint][INTERNAL_STATE] <= cycle_count;
                        end

                        offered_count = $countones(dut.gen_y[IY].gen_x[IX].tile.h1.
                            gen_h0_tiles[internal_h0].child_h0.node_partial_valid);
                        accepted_count = $countones(dut.gen_y[IY].gen_x[IX].tile.h1.
                            gen_h0_tiles[internal_h0].child_h0.node_partial_valid &
                            dut.gen_y[IY].gen_x[IX].tile.h1.
                            gen_h0_tiles[internal_h0].child_h0.node_partial_ready);
                        write_internal_accept_event(internal_node, "h0", internal_h0,
                            INTERNAL_PARTIAL, accepted_count);
                        internal_offered[internal_node][endpoint][INTERNAL_PARTIAL] += offered_count;
                        internal_accepted[internal_node][endpoint][INTERNAL_PARTIAL] += accepted_count;
                        internal_stalled[internal_node][endpoint][INTERNAL_PARTIAL] +=
                            offered_count - accepted_count;
                        if (accepted_count != 0) begin
                            if (internal_first_accept[internal_node][endpoint][INTERNAL_PARTIAL] == 0)
                                internal_first_accept[internal_node][endpoint][INTERNAL_PARTIAL] <= cycle_count;
                            internal_last_accept[internal_node][endpoint][INTERNAL_PARTIAL] <= cycle_count;
                        end
                    end
                end
            end

            always @(posedge clk) begin : collect_h1_cross_internal
                int offered_count;
                int accepted_count;
                int endpoint;
                if (rst) begin
                    for (int hierarchy = 0; hierarchy < 2; hierarchy++) begin
                        endpoint = H0_COUNT + hierarchy;
                        for (int transfer = 0; transfer < INTERNAL_TRANSFER_COUNT;
                             transfer++) begin
                            internal_offered[internal_node][endpoint][transfer] <= 0;
                            internal_accepted[internal_node][endpoint][transfer] <= 0;
                            internal_stalled[internal_node][endpoint][transfer] <= 0;
                            internal_first_accept[internal_node][endpoint][transfer] <= 0;
                            internal_last_accept[internal_node][endpoint][transfer] <= 0;
                        end
                    end
                end
                else if (noc_stats_active) begin
                    endpoint = H0_COUNT;
                    offered_count = $countones(h1_dma_cmd_valid_i[internal_node]);
                    accepted_count = $countones(h1_dma_cmd_valid_i[internal_node] &
                                                h1_dma_cmd_ready_o[internal_node]);
                    write_internal_accept_event(internal_node, "h1", 0,
                        INTERNAL_CMD, accepted_count);
                    internal_offered[internal_node][endpoint][INTERNAL_CMD] += offered_count;
                    internal_accepted[internal_node][endpoint][INTERNAL_CMD] += accepted_count;
                    internal_stalled[internal_node][endpoint][INTERNAL_CMD] += offered_count-accepted_count;
                    if (accepted_count != 0) begin
                        if (internal_first_accept[internal_node][endpoint][INTERNAL_CMD] == 0)
                            internal_first_accept[internal_node][endpoint][INTERNAL_CMD] <= cycle_count;
                        internal_last_accept[internal_node][endpoint][INTERNAL_CMD] <= cycle_count;
                    end
                    offered_count = $countones(h1_dma_weight_valid_i[internal_node]);
                    accepted_count = $countones(h1_dma_weight_valid_i[internal_node] &
                                                h1_dma_weight_ready_o[internal_node]);
                    write_internal_accept_event(internal_node, "h1", 0,
                        INTERNAL_WEIGHT, accepted_count);
                    internal_offered[internal_node][endpoint][INTERNAL_WEIGHT] += offered_count;
                    internal_accepted[internal_node][endpoint][INTERNAL_WEIGHT] += accepted_count;
                    internal_stalled[internal_node][endpoint][INTERNAL_WEIGHT] += offered_count-accepted_count;
                    if (accepted_count != 0) begin
                        if (internal_first_accept[internal_node][endpoint][INTERNAL_WEIGHT] == 0)
                            internal_first_accept[internal_node][endpoint][INTERNAL_WEIGHT] <= cycle_count;
                        internal_last_accept[internal_node][endpoint][INTERNAL_WEIGHT] <= cycle_count;
                    end
                    offered_count = dut.gen_y[IY].gen_x[IX].tile.h1.h1_node_state_valid;
                    accepted_count = offered_count &&
                        dut.gen_y[IY].gen_x[IX].tile.h1.h1_node_state_ready;
                    write_internal_accept_event(internal_node, "h1", 0,
                        INTERNAL_STATE, accepted_count);
                    internal_offered[internal_node][endpoint][INTERNAL_STATE] += offered_count;
                    internal_accepted[internal_node][endpoint][INTERNAL_STATE] += accepted_count;
                    internal_stalled[internal_node][endpoint][INTERNAL_STATE] += offered_count-accepted_count;
                    if (accepted_count != 0) begin
                        if (internal_first_accept[internal_node][endpoint][INTERNAL_STATE] == 0)
                            internal_first_accept[internal_node][endpoint][INTERNAL_STATE] <= cycle_count;
                        internal_last_accept[internal_node][endpoint][INTERNAL_STATE] <= cycle_count;
                    end
                    offered_count = $countones(
                        dut.gen_y[IY].gen_x[IX].tile.h1.node_partial_valid);
                    accepted_count = $countones(
                        dut.gen_y[IY].gen_x[IX].tile.h1.node_partial_valid &
                        dut.gen_y[IY].gen_x[IX].tile.h1.node_partial_ready);
                    write_internal_accept_event(internal_node, "h1", 0,
                        INTERNAL_PARTIAL, accepted_count);
                    internal_offered[internal_node][endpoint][INTERNAL_PARTIAL] += offered_count;
                    internal_accepted[internal_node][endpoint][INTERNAL_PARTIAL] += accepted_count;
                    internal_stalled[internal_node][endpoint][INTERNAL_PARTIAL] += offered_count-accepted_count;
                    if (accepted_count != 0) begin
                        if (internal_first_accept[internal_node][endpoint][INTERNAL_PARTIAL] == 0)
                            internal_first_accept[internal_node][endpoint][INTERNAL_PARTIAL] <= cycle_count;
                        internal_last_accept[internal_node][endpoint][INTERNAL_PARTIAL] <= cycle_count;
                    end

                    endpoint = H0_COUNT + 1;
                    offered_count = $countones(cross_dma_cmd_valid_i[internal_node]);
                    accepted_count = $countones(cross_dma_cmd_valid_i[internal_node] &
                                                cross_dma_cmd_ready_o[internal_node]);
                    write_internal_accept_event(internal_node, "cross", 0,
                        INTERNAL_CMD, accepted_count);
                    internal_offered[internal_node][endpoint][INTERNAL_CMD] += offered_count;
                    internal_accepted[internal_node][endpoint][INTERNAL_CMD] += accepted_count;
                    internal_stalled[internal_node][endpoint][INTERNAL_CMD] += offered_count-accepted_count;
                    if (accepted_count != 0) begin
                        if (internal_first_accept[internal_node][endpoint][INTERNAL_CMD] == 0)
                            internal_first_accept[internal_node][endpoint][INTERNAL_CMD] <= cycle_count;
                        internal_last_accept[internal_node][endpoint][INTERNAL_CMD] <= cycle_count;
                    end
                    offered_count = $countones(cross_dma_weight_valid_i[internal_node]);
                    accepted_count = $countones(cross_dma_weight_valid_i[internal_node] &
                                                cross_dma_weight_ready_o[internal_node]);
                    write_internal_accept_event(internal_node, "cross", 0,
                        INTERNAL_WEIGHT, accepted_count);
                    internal_offered[internal_node][endpoint][INTERNAL_WEIGHT] += offered_count;
                    internal_accepted[internal_node][endpoint][INTERNAL_WEIGHT] += accepted_count;
                    internal_stalled[internal_node][endpoint][INTERNAL_WEIGHT] += offered_count-accepted_count;
                    if (accepted_count != 0) begin
                        if (internal_first_accept[internal_node][endpoint][INTERNAL_WEIGHT] == 0)
                            internal_first_accept[internal_node][endpoint][INTERNAL_WEIGHT] <= cycle_count;
                        internal_last_accept[internal_node][endpoint][INTERNAL_WEIGHT] <= cycle_count;
                    end
                    offered_count = dut.gen_y[IY].gen_x[IX].tile.top.cross_compute.
                        hierarchy_state_valid;
                    accepted_count = offered_count && dut.gen_y[IY].gen_x[IX].tile.
                        top.cross_compute.hierarchy_state_ready;
                    write_internal_accept_event(internal_node, "cross", 0,
                        INTERNAL_STATE, accepted_count);
                    internal_offered[internal_node][endpoint][INTERNAL_STATE] += offered_count;
                    internal_accepted[internal_node][endpoint][INTERNAL_STATE] += accepted_count;
                    internal_stalled[internal_node][endpoint][INTERNAL_STATE] += offered_count-accepted_count;
                    if (accepted_count != 0) begin
                        if (internal_first_accept[internal_node][endpoint][INTERNAL_STATE] == 0)
                            internal_first_accept[internal_node][endpoint][INTERNAL_STATE] <= cycle_count;
                        internal_last_accept[internal_node][endpoint][INTERNAL_STATE] <= cycle_count;
                    end
                    offered_count = $countones(dut.gen_y[IY].gen_x[IX].tile.top.
                        cross_compute.partial_valid);
                    accepted_count = $countones(dut.gen_y[IY].gen_x[IX].tile.top.
                        cross_compute.partial_valid & dut.gen_y[IY].gen_x[IX].tile.
                        top.cross_compute.partial_ready);
                    write_internal_accept_event(internal_node, "cross", 0,
                        INTERNAL_PARTIAL, accepted_count);
                    internal_offered[internal_node][endpoint][INTERNAL_PARTIAL] += offered_count;
                    internal_accepted[internal_node][endpoint][INTERNAL_PARTIAL] += accepted_count;
                    internal_stalled[internal_node][endpoint][INTERNAL_PARTIAL] += offered_count-accepted_count;
                    if (accepted_count != 0) begin
                        if (internal_first_accept[internal_node][endpoint][INTERNAL_PARTIAL] == 0)
                            internal_first_accept[internal_node][endpoint][INTERNAL_PARTIAL] <= cycle_count;
                        internal_last_accept[internal_node][endpoint][INTERNAL_PARTIAL] <= cycle_count;
                    end
                end
            end
        end
    endgenerate

    task automatic report_internal_statistics;
        string stats_path;
        int stats_file;
        string endpoint_class;
        int endpoint_id;
        int lane_count;
        real utilization;
        real pressure;
        if (!$value$plusargs("INTERNAL_STATS_FILE=%s", stats_path))
            stats_path = "internal_stats.csv";
        stats_file = $fopen(stats_path, "w");
        if (stats_file == 0)
            $fatal(1, "could not open internal statistics file %s", stats_path);
        $fdisplay(stats_file,
            "node,x,y,endpoint_class,endpoint_id,transfer,offered_lane_cycles,accepted_transfers,stalled_lane_cycles,first_accept_cycle,last_accept_cycle,capacity_utilization,backpressure_fraction");
        for (int node = 0; node < NODE_COUNT; node++) begin
            for (int endpoint = 0; endpoint < INTERNAL_ENDPOINT_COUNT; endpoint++) begin
                if (endpoint < H0_COUNT) begin
                    endpoint_class = "h0";
                    endpoint_id = endpoint;
                end
                else if (endpoint == H0_COUNT) begin
                    endpoint_class = "h1";
                    endpoint_id = 0;
                end
                else begin
                    endpoint_class = "cross";
                    endpoint_id = 0;
                end
                for (int transfer = 0; transfer < INTERNAL_TRANSFER_COUNT;
                     transfer++) begin
                    if (transfer == INTERNAL_STATE)
                        lane_count = 1;
                    else if (endpoint < H0_COUNT)
                        lane_count = H0_MVM_COUNT;
                    else if (endpoint == H0_COUNT)
                        lane_count = H1_MVM_COUNT;
                    else
                        lane_count = CROSS_MVM_COUNT;
                    utilization = noc_monitor_cycles == 0 ? 0.0 :
                        real'(internal_accepted[node][endpoint][transfer]) /
                        real'(noc_monitor_cycles * lane_count);
                    pressure = internal_offered[node][endpoint][transfer] == 0 ? 0.0 :
                        real'(internal_stalled[node][endpoint][transfer]) /
                        real'(internal_offered[node][endpoint][transfer]);
                    $fdisplay(stats_file,
                        "%0d,%0d,%0d,%s,%0d,%s,%0d,%0d,%0d,%0d,%0d,%0.6f,%0.6f",
                        node, node % MESH_X_COUNT, node / MESH_X_COUNT,
                        endpoint_class, endpoint_id, internal_transfer_name(transfer),
                        internal_offered[node][endpoint][transfer],
                        internal_accepted[node][endpoint][transfer],
                        internal_stalled[node][endpoint][transfer],
                        internal_first_accept[node][endpoint][transfer],
                        internal_last_accept[node][endpoint][transfer], utilization,
                        pressure);
                end
            end
        end
        $fclose(stats_file);
        $display("Internal hierarchy detailed CSV: %s", stats_path);
    endtask

    logic [NODE_COUNT-1:0] router_activity;

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
                    $signed(noise_amplitude_i[0]) :
                    -$signed(noise_amplitude_i[0]);
            field = (current_spin(spin) ? $signed(coeff_a_i[0]) :
                                          -$signed(coeff_a_i[0])) +
                    $signed(coeff_b_i[0]) * golden_sum[spin] + noise;
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
            if (state_dump_file != 0)
                $fdisplay(state_dump_file, "%0d %0d %0d", iteration, spin,
                          state_next_o[node_id][h0_id][core_id][spin_id]);
        end
        if (errors != 0)
            $fatal(1, "iteration %0d failed with %0d state mismatches",
                   iteration, errors);
        $display("iteration=%0d PASS cycles=%0d h0_blocks=%0d h1_blocks=%0d cross_blocks=%0d noc_states=%0d",
                 iteration, cycle_count, issued_h0_blocks, issued_h1_blocks,
                 issued_cross_blocks, published_states);
    endtask

    function automatic longint fill_writes_expected();
        longint total;
        total = 0;
        for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++) total += h0_fill_count[h0];
        return total;
    endfunction

    initial begin : test_sequence
        longint unsigned publication_key;
        int publication_block;
        int publication_owner;
        int matlab_spin_count;
        int matlab_iteration_count;
        int matlab_header_result;
        int runtime_iteration_scan;
        int solver_schedule_file;
        int solver_schedule_scan;

        runtime_iteration_count = ITERATION_COUNT;
        runtime_iteration_scan =
            $value$plusargs("ITERATIONS=%d", runtime_iteration_count);
        for (int iteration = 0; iteration < ITERATION_COUNT; iteration++) begin
            solver_coeff_a[iteration] = COEFF_A_VALUE;
            solver_coeff_b[iteration] = COEFF_B_VALUE;
            solver_noise[iteration] = NOISE_AMPLITUDE;
        end
        if ($value$plusargs("SOLVER_SCHEDULE=%s", solver_schedule_path)) begin
            solver_schedule_file = $fopen(solver_schedule_path, "r");
            if (solver_schedule_file == 0)
                $fatal(1, "cannot open solver schedule %s", solver_schedule_path);
            for (int iteration = 0; iteration < runtime_iteration_count;
                 iteration++) begin
                solver_schedule_scan = $fscanf(
                    solver_schedule_file, "%d %d %d\n",
                    solver_coeff_a[iteration], solver_coeff_b[iteration],
                    solver_noise[iteration]);
                if (solver_schedule_scan != 3)
                    $fatal(1, "solver schedule %s is missing iteration %0d",
                           solver_schedule_path, iteration);
            end
            $fclose(solver_schedule_file);
            $display("using solver schedule %s", solver_schedule_path);
        end
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
        state_dump_file = 0;
        if ($value$plusargs("DUMP_STATES=%s", state_dump_path)) begin
            state_dump_file = $fopen(state_dump_path, "w");
            if (state_dump_file == 0)
                $fatal(1, "cannot create state dump file %s", state_dump_path);
            $fdisplay(state_dump_file, "iteration spin state");
        end
        if ($value$plusargs("MATLAB_GOLDEN=%s", matlab_golden_path)) begin
            matlab_golden_file = $fopen(matlab_golden_path, "r");
            if (matlab_golden_file == 0)
                $fatal(1, "cannot open MATLAB golden file %s", matlab_golden_path);
            matlab_header_result = $fscanf(matlab_golden_file, "%d %d",
                                            matlab_spin_count,
                                            matlab_iteration_count);
            if (matlab_header_result != 2 ||
                matlab_spin_count != TOTAL_SPIN_COUNT ||
                matlab_iteration_count != runtime_iteration_count)
                $fatal(1, "MATLAB golden header mismatch: got spins=%0d iterations=%0d, expected spins=%0d iterations=%0d",
                       matlab_spin_count, matlab_iteration_count,
                       TOTAL_SPIN_COUNT, runtime_iteration_count);
            $display("using MATLAB golden states from %s", matlab_golden_path);
        end
        if (USE_RAMULATOR)
            az_dram_init("tb/ramulator_128x32.yaml", dataset_path,
                         DRAM_SYSTEM_COUNT, TOTAL_BLOCK_COUNT);

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
        // Release after this edge's active/inactive processes: neither the
        // global DRAM tick nor its #0 bridge may observe a partial reset edge.
        // The first nonreset rising edge is accelerator cycle 0, with zero
        // elapsed DRAM ticks; the following falling edge advances cycle 1.
        rst <= 1'b0;
        @(negedge clk);
        init_start_i = '1;
        @(negedge clk);
        init_start_i = '0;
        for (int block = 0; block < TOTAL_BLOCK_COUNT; block++)
            load_diagonal_block(block);
        wait (&init_done_o);
        $display("initialization complete at cycle %0d", cycle_count);

        for (int iteration = 0; iteration < runtime_iteration_count; iteration++) begin
            issued_h0_blocks = 0;
            issued_h1_blocks = 0;
            issued_cross_blocks = 0;
            published_states = 0;
            h0_next_engine = '{default: 0};
            h1_next_engine = '{default: 0};
            cross_next_engine = '{default: 0};
            epoch_i = '{default: EPOCH_W'(iteration)};
            coeff_a_i = '{default: COEFF_W'(solver_coeff_a[iteration])};
            coeff_b_i = '{default: COEFF_W'(solver_coeff_b[iteration])};
            noise_amplitude_i = '{default: COEFF_W'(solver_noise[iteration])};
            compute_golden();
            // The dispatcher consumes the queues; reload every iteration.
            load_anchored_schedule();

            @(negedge clk);
            iter_start_i = '1;
            // Tiles with nothing to fill start their node directly.
            for (int h0 = 0; h0 < TOTAL_H0_COUNT; h0++)
                h0_state_fill_done_i[h0 / H0_COUNT][h0 % H0_COUNT] = h0_fill_count[h0] == 0;
            @(negedge clk);
            iter_start_i = '0;
            h0_state_fill_done_i = '0;

            // State fill runs concurrently with the state publication, like
            // the autonomous H0/H1 snapshot loads of the base tiles.
            fork
                fill_h0_states();
            join_none

            $display("iteration %0d: publishing states", iteration);
            for (int index = 0; index < publication_list.size(); index++) begin
                publication_block = publication_list[index].block;
                publication_owner = publication_list[index].h1;
                publish_state(publication_block, publication_owner);
            end
            wait_for_network_drain();
            // The fill keeps running in the background: like the autonomous
            // H0/H1 snapshot loads of the base tiles it overlaps the cross
            // start and the schedule dispatch (the model dispatches from
            // compute_start while each H0 node starts at its own fill time;
            // the streamers prefetch weights until the node is RUN).  Waiting
            // for the fill here delayed the dispatch by the fill length minus
            // the publication phase and cost 47 cycles of prefetch latency at
            // every node start (found on the dense 1x1 geometries).
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
            // Anchored quiet window: every cross node (idle), every H0
            // hierarchy node and every uplink flush must be done.
            wait (&cross_iter_done_o && &h0_node_done_o && &h0_uplink_flushed_o);
            $display("iteration %0d: cross, H0 nodes and uplinks done at cycle %0d", iteration,
                     cycle_count);
            wait_for_network_drain();
            publish_completion();
            wait (&iter_done_o);
            wait fork;
            if (fill_writes != fill_writes_expected())
                $fatal(1, "state fill wrote %0d blocks, expected %0d", fill_writes,
                       fill_writes_expected());
            @(negedge clk);
            check_next_state(iteration);
            if (iteration == runtime_iteration_count-1)
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
                 runtime_iteration_count, SKIP_ZERO_BLOCKS, cycle_count);
        $display("ANCHORED accumulate=%0d uplink_packets=%0d jobs=%0d publications=%0d",
                 ACCUMULATE, UPLINK_PACKETS, schedule_jobs, publication_list.size());
        write_noc_timeline_snapshot(cycle_count);
        report_noc_statistics();
        report_internal_statistics();
        if (noc_timeline_file != 0)
            $fclose(noc_timeline_file);
        if (noc_event_file != 0)
            $fclose(noc_event_file);
        if (internal_event_file != 0)
            $fclose(internal_event_file);
        if (USE_RAMULATOR) begin
            az_dram_report();
            az_dram_finalize();
        end
        if (matlab_golden_file != 0)
            $fclose(matlab_golden_file);
        if (state_dump_file != 0)
            $fclose(state_dump_file);
        $finish;
    end
endmodule
