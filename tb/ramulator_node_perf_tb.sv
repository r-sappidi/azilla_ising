`timescale 1ns/1ps

// Dense single-node throughput microbenchmark. Production RTL is unchanged:
// only the number of MVM engines and available 256-bit output lanes vary.
module ramulator_node_perf_tb #(
  parameter int MVM_COUNT         = 4,
  parameter int OUTPUT_LANES      = 1,
  parameter int WORK_BLOCK_COUNT  = 256,
  parameter int TOTAL_BLOCK_COUNT = 2048,
  parameter int STATE_ENTRY_COUNT = 64,
  parameter int MEM_LANES         = 16,
  parameter int MAX_OUTSTANDING   = 64,
  parameter int CLK_PERIOD_PS     = 2000,
  parameter int RAMULATOR_TCK_PS  = 250
);
  import ising_pkg::*;

  localparam int STATE_W  = (STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1;
  localparam int GLOBAL_W = (TOTAL_BLOCK_COUNT > 1) ? $clog2(TOTAL_BLOCK_COUNT) : 1;
  localparam int TIMEOUT_CYCLES = WORK_BLOCK_COUNT * 2000 + 100000;
  localparam int SAFE_RAMULATOR_TCK_PS =
      (RAMULATOR_TCK_PS > 0) ? RAMULATOR_TCK_PS : 1;
  localparam int RAMULATOR_TICKS_PER_CYCLE =
      CLK_PERIOD_PS / SAFE_RAMULATOR_TCK_PS;

  logic clk = 1'b0;
  logic rst = 1'b1;
  always #(CLK_PERIOD_PS / 2000.0) clk = ~clk;

  logic iter_start, schedule_done, iter_done;
  logic state_valid, state_ready;
  logic [STATE_W-1:0] state_index;
  logic [SPIN_COUNT-1:0] state_data;

  logic [MVM_COUNT-1:0] sched_cmd_valid, sched_cmd_ready;
  logic [MVM_COUNT-1:0][STATE_W-1:0] sched_cmd_state_a, sched_cmd_state_b;
  logic [MVM_COUNT-1:0][GLOBAL_W-1:0] sched_cmd_global_a, sched_cmd_global_b;
  logic [MVM_COUNT-1:0] node_cmd_valid, node_cmd_ready;
  logic [MVM_COUNT-1:0][STATE_W-1:0] node_cmd_state_a, node_cmd_state_b;
  logic [MVM_COUNT-1:0][GLOBAL_W-1:0] node_cmd_global_a, node_cmd_global_b;
  logic [MVM_COUNT-1:0] node_weight_valid, node_weight_ready;
  logic [MVM_COUNT-1:0][DATA_W-1:0] node_weight_data;
  logic streamer_idle;

  logic [MVM_COUNT-1:0] partial_valid, partial_ready, partial_last;
  logic [MVM_COUNT-1:0][GLOBAL_W-1:0] partial_dest;
  logic [MVM_COUNT-1:0][DATA_W-1:0] partial_data;

  longint unsigned cycle_count, output_flits, output_packets;
  longint unsigned output_stall_cycles;
  int unsigned rr_base;
  string dataset_name = "g65536_kings.txt";
  string config_name = "tb/ramulator_128x32.yaml";

  import "DPI-C" function void az_dram_init(
    input string config_path, input string dataset_path,
    input int system_count, input int total_blocks);
  import "DPI-C" function void az_dram_tick(input int ticks);
  import "DPI-C" function void az_dram_report();
  import "DPI-C" function void az_dram_finalize();

  ramulator_node_frontend #(
    .SYSTEM_ID(0), .MVM_COUNT(MVM_COUNT), .STATE_INDEX_W(STATE_W),
    .GLOBAL_BLOCK_ID_W(GLOBAL_W), .TOTAL_BLOCK_COUNT(TOTAL_BLOCK_COUNT),
    .MEM_LANES(MEM_LANES), .MAX_OUTSTANDING(MAX_OUTSTANDING)
  ) memory (
    .clk(clk), .rst(rst),
    .sched_cmd_valid_i(sched_cmd_valid), .sched_cmd_ready_o(sched_cmd_ready),
    .sched_state_a_index_i(sched_cmd_state_a), .sched_state_b_index_i(sched_cmd_state_b),
    .sched_block_a_id_i(sched_cmd_global_a), .sched_block_b_id_i(sched_cmd_global_b),
    .node_cmd_valid_o(node_cmd_valid), .node_cmd_ready_i(node_cmd_ready),
    .node_state_a_index_o(node_cmd_state_a), .node_state_b_index_o(node_cmd_state_b),
    .node_block_a_id_o(node_cmd_global_a), .node_block_b_id_o(node_cmd_global_b),
    .node_weight_valid_o(node_weight_valid), .node_weight_ready_i(node_weight_ready),
    .node_weight_data_o(node_weight_data),
    .idle_o(streamer_idle)
  );

  hierarchy_node #(
    .MVM_COUNT(MVM_COUNT), .STATE_ENTRY_COUNT(STATE_ENTRY_COUNT),
    .GLOBAL_BLOCK_ID_W(GLOBAL_W)
  ) node (
    .clk(clk), .rst(rst), .iter_start(iter_start),
    .schedule_done_i(schedule_done), .iter_done(iter_done),
    .state_valid_i(state_valid), .state_ready_o(state_ready),
    .state_index_i(state_index), .state_data_i(state_data),
    .dma_cmd_valid_i(node_cmd_valid), .dma_cmd_ready_o(node_cmd_ready),
    .dma_state_a_index_i(node_cmd_state_a), .dma_state_b_index_i(node_cmd_state_b),
    .dma_block_a_i(node_cmd_global_a), .dma_block_b_i(node_cmd_global_b),
    .dma_weight_valid_i(node_weight_valid), .dma_weight_ready_o(node_weight_ready),
    .dma_weight_data_i(node_weight_data),
    .partial_valid_o(partial_valid), .partial_ready_i(partial_ready),
    .partial_block_id_o(partial_dest), .partial_data_o(partial_data),
    .partial_last_o(partial_last)
  );

  // Select at most OUTPUT_LANES engine streams per cycle. Each selected
  // engine retains its own packet framing; this models parallel injection
  // streams rather than widening a FlooNoC flit.
  always_comb begin
    int selected;
    int engine;
    partial_ready = '0;
    selected = 0;
    for (int offset = 0; offset < MVM_COUNT; offset++) begin
      engine = (rr_base + offset) % MVM_COUNT;
      if ((selected < OUTPUT_LANES) && partial_valid[engine]) begin
        partial_ready[engine] = 1'b1;
        selected++;
      end
    end
  end

  always_ff @(posedge clk) begin
    int accepted;
    int packets;
    int last_engine;
    if (rst) begin
      cycle_count <= 0;
      output_flits <= 0;
      output_packets <= 0;
      output_stall_cycles <= 0;
      rr_base <= 0;
    end else begin
      cycle_count <= cycle_count + 1;
      accepted = 0;
      packets = 0;
      last_engine = rr_base;
      for (int engine = 0; engine < MVM_COUNT; engine++) begin
        if (partial_valid[engine] && partial_ready[engine]) begin
          accepted++;
          packets += int'(partial_last[engine]);
          last_engine = engine;
        end
      end
      output_flits <= output_flits + longint'(accepted);
      output_packets <= output_packets + longint'(packets);
      if (accepted != 0) rr_base <= (last_engine + 1) % MVM_COUNT;
      if ((|partial_valid) && (accepted < $countones(partial_valid)))
        output_stall_cycles <= output_stall_cycles + 1;
    end
  end

  // Advance modeled DRAM time by exactly one accelerator clock period.
  always @(negedge clk)
    if (!rst) az_dram_tick(RAMULATOR_TICKS_PER_CYCLE);

  initial begin : run_benchmark
    longint unsigned start_cycle;
    longint unsigned elapsed_cycles;
    int engine, block_a, block_b;
    real blocks_per_cycle;

    if ((OUTPUT_LANES < 1) || (OUTPUT_LANES > MVM_COUNT))
      $fatal(1, "OUTPUT_LANES must be in [1, MVM_COUNT]");
    if ((RAMULATOR_TCK_PS <= 0) ||
        ((CLK_PERIOD_PS % SAFE_RAMULATOR_TCK_PS) != 0))
      $fatal(1, "CLK_PERIOD_PS must be an integer multiple of RAMULATOR_TCK_PS");
    if ((WORK_BLOCK_COUNT < 1) || (TOTAL_BLOCK_COUNT < 2))
      $fatal(1, "invalid benchmark dimensions");
    void'($value$plusargs("DATASET=%s", dataset_name));
    void'($value$plusargs("RAMULATOR_CONFIG=%s", config_name));

    iter_start = 0;
    schedule_done = 0;
    state_valid = 0;
    state_index = '0;
    state_data = '0;
    sched_cmd_valid = '0;
    sched_cmd_state_a = '0;
    sched_cmd_state_b = '0;
    sched_cmd_global_a = '0;
    sched_cmd_global_b = '0;

    az_dram_init(config_name, {"tb/datasets/", dataset_name}, 1,
                 TOTAL_BLOCK_COUNT);
    repeat (5) @(negedge clk);
    rst = 0;

    for (int entry = 0; entry < STATE_ENTRY_COUNT; entry++) begin
      @(negedge clk);
      state_index = STATE_W'(entry);
      state_data = SPIN_COUNT'((32'h9e37_79b9 * (entry + 1)) ^ 32'ha5a_5a5a);
      state_valid = 1;
      while (!state_ready) @(negedge clk);
    end
    @(negedge clk);
    state_valid = 0;
    iter_start = 1;
    start_cycle = cycle_count;
    @(negedge clk);
    iter_start = 0;

    for (int command = 0; command < WORK_BLOCK_COUNT; command++) begin
      engine = command % MVM_COUNT;
      block_a = (command * 37) % TOTAL_BLOCK_COUNT;
      block_b = (block_a + 1 + ((command * 53) % (TOTAL_BLOCK_COUNT - 1)))
                % TOTAL_BLOCK_COUNT;
      @(negedge clk);
      sched_cmd_state_a[engine] = STATE_W'(command % STATE_ENTRY_COUNT);
      sched_cmd_state_b[engine] = STATE_W'((command + 17) % STATE_ENTRY_COUNT);
      sched_cmd_global_a[engine] = GLOBAL_W'(block_a);
      sched_cmd_global_b[engine] = GLOBAL_W'(block_b);
      sched_cmd_valid[engine] = 1;
      while (!sched_cmd_ready[engine]) @(negedge clk);
      @(negedge clk);
      sched_cmd_valid[engine] = 0;
    end

    wait (streamer_idle);
    @(negedge clk);
    schedule_done = 1;
    @(negedge clk);
    schedule_done = 0;

    for (int timeout = 0; timeout < TIMEOUT_CYCLES; timeout++) begin
      @(posedge clk);
      if (iter_done) break;
    end
    if (!iter_done) $fatal(1, "benchmark timeout");

    elapsed_cycles = cycle_count - start_cycle;
    blocks_per_cycle = real'(WORK_BLOCK_COUNT) / real'(elapsed_cycles);
    if (output_packets != (2 * WORK_BLOCK_COUNT))
      $fatal(1, "expected %0d packets, observed %0d",
             2 * WORK_BLOCK_COUNT, output_packets);
    az_dram_report();
    $display("PERF_CSV,mvm_count=%0d,output_lanes=%0d,work_blocks=%0d,cycles=%0d,blocks_per_cycle=%.9f,output_flits=%0d,output_stall_cycles=%0d",
             MVM_COUNT, OUTPUT_LANES, WORK_BLOCK_COUNT, elapsed_cycles,
             blocks_per_cycle, output_flits, output_stall_cycles);
    az_dram_finalize();
    $finish;
  end
endmodule
