`timescale 1ns/1ps
import ising_pkg::*;

// Three real canonical-memory frontends feeding directed core RTL. This
// diagnostic checks shared delivery, finite buffering, and (USE_MESH=1)
// actual cross-source routing. Full iteration/state control is outside scope.
module core_memory_integration_vcs_tb #(parameter bit USE_MESH=0, parameter int ENGINES=1, parameter int JOBS_PER_SOURCE=2);
  localparam int SOURCES=3, CORES=8, GW=3;
  logic clk=0, rst=1;
  always #5 clk=~clk;
  logic [SOURCES-1:0][ENGINES-1:0] sv,sr,cv,cr,wv,wr;
  logic [SOURCES-1:0][ENGINES-1:0][GW-1:0] sa,sb,ca,cb;
  logic [SOURCES-1:0][ENGINES-1:0][DATA_W-1:0] wd;
  logic [SOURCES-1:0] idle;
  logic [CORES-1:0] jv,jr,cwv,cwr,rv,rr;
  logic [CORES-1:0][GW-1:0] js,rs;
  logic [CORES-1:0][DATA_W-1:0] cwd;
  logic signed [ACC_W-1:0] result[CORES][SPIN_COUNT];
  logic [63:0] jobs[CORES],bytes_w[CORES],bytes_s[CORES],bytes_r[CORES];
  logic [63:0] first_cycle[CORES],last_cycle[CORES];
  int issued[SOURCES], active_source[CORES], active_engine[CORES];
  int cycle=0, completed=0, stall_period=0;
  localparam int LOCAL=0,EAST=3,WEST=4;
  logic [1:0][4:0] niv,nir,nov,nready,nlast,olast;
  logic [1:0][4:0][DATA_W-1:0] nid,nod;
  logic [1:0][4:0][1:0] nit,notype;
  logic [1:0][4:0][0:0] nix,niy,nox,noy;
  logic [1:0][4:0][7:0] nis,nos;
  logic [1:0][4:0][15:0] nie,noe;
  logic [1:0][4:0][GW-1:0] nib,nob;
  int cross_beat[ENGINES];
  int cross_lock=-1, cross_choice;
  string config_path="tb/ramulator_128x32.yaml";
  string dataset_path="tb/datasets/g256_smoke.txt";

  import "DPI-C" function void az_dram_init_timing(
    input string config_path,input string dataset_path,
    input int systems,input int blocks);
  import "DPI-C" function void az_dram_tick(input int ticks);
  import "DPI-C" function void az_dram_finalize();

  // Geometry: 2 H1 x 2 H0 x 2 cores. Sources are system IDs 0 (H0),
  // 4 (H1-local), and 6 (cross-H1 owner node zero).
  for(genvar s=0;s<SOURCES;s++) begin: memories
    localparam int SYS=(s==0)?0:((s==1)?4:6);
    ramulator_node_frontend #(.SYSTEM_ID(SYS),.MVM_COUNT(ENGINES),
      .STATE_INDEX_W(GW),.GLOBAL_BLOCK_ID_W(GW),.TOTAL_BLOCK_COUNT(8),
      .MEM_LANES(16),.MAX_OUTSTANDING(64)) memory (
      .clk,.rst,.sched_cmd_valid_i(sv[s]),.sched_cmd_ready_o(sr[s]),
      .sched_state_a_index_i(sa[s]),.sched_state_b_index_i(sb[s]),
      .sched_block_a_id_i(sa[s]),.sched_block_b_id_i(sb[s]),
      .node_cmd_valid_o(cv[s]),.node_cmd_ready_i(cr[s]),
      .node_state_a_index_o(),.node_state_b_index_o(),
      .node_block_a_id_o(ca[s]),.node_block_b_id_o(cb[s]),
      .node_weight_valid_o(wv[s]),.node_weight_ready_i(wr[s]),
      .node_weight_data_o(wd[s]),.idle_o(idle[s]));
    always @(posedge clk) if(!rst) for(int lane=0;lane<16;lane++) begin
      if(memory.mem_req_valid[lane]&&memory.mem_req_ready[lane])
        $display("CORE_MEM cycle=%0d event=request system=%0d tag=%0d",cycle,SYS,memory.mem_req_tag[lane]);
      if(memory.mem_rsp_valid[lane]&&memory.mem_rsp_ready[lane])
        $display("CORE_MEM cycle=%0d event=response system=%0d tag=%0d",cycle,SYS,memory.mem_rsp_tag[lane]);
    end
  end
  for(genvar c=0;c<CORES;c++) begin: cores
    core_local_mvm_engine #(.GLOBAL_BLOCK_ID_W(GW)) engine (
      .clk,.rst,.job_valid_i(jv[c]),.job_ready_o(jr[c]),
      .source_block_id_i(js[c]),.source_state_i(32'hffffffff),
      .source_remote_i(1'b1),.weight_valid_i(cwv[c]),
      .weight_ready_o(cwr[c]),.weight_data_i(cwd[c]),
      .result_valid_o(rv[c]),.result_ready_i(rr[c]),.result_o(result[c]),
      .result_source_block_id_o(rs[c]),.jobs_completed_o(jobs[c]),
      .weight_bytes_o(bytes_w[c]),.source_state_bytes_o(bytes_s[c]),
      .remote_source_state_bytes_o(bytes_r[c]),
      .first_job_cycle_o(first_cycle[c]),.last_job_cycle_o(last_cycle[c]));
  end
  for(genvar n=0;n<2;n++) begin: routers
    azilla_floo_router #(.X_W(1),.Y_W(1),.SOURCE_ID_W(8),.EPOCH_W(16),
      .GLOBAL_BLOCK_ID_W(GW),.FIFO_DEPTH(4),.ROUTER_X(n),.ROUTER_Y(0)) router (
      .clk,.rst,.in_valid_i(niv[n]),.in_ready_o(nir[n]),.in_data_i(nid[n]),
      .in_type_i(nit[n]),.in_dest_x_i(nix[n]),.in_dest_y_i(niy[n]),
      .in_source_id_i(nis[n]),.in_epoch_i(nie[n]),.in_block_id_i(nib[n]),
      .in_last_i(nlast[n]),.out_valid_o(nov[n]),.out_ready_i(nready[n]),
      .out_data_o(nod[n]),.out_type_o(notype[n]),.out_dest_x_o(nox[n]),
      .out_dest_y_o(noy[n]),.out_source_id_o(nos[n]),.out_epoch_o(noe[n]),
      .out_block_id_o(nob[n]),.out_last_o(olast[n]));
  end

  // Fixed priority and one row per destination H0 per edge. Command metadata
  // identifies the directed destination (block_a); the frontend itself stores
  // and reads the canonical unordered weight block.
  always_comb begin
    logic [3:0] used;
    logic [SOURCES-1:0] used_source;
    used='0;used_source='0;cross_choice=cross_lock;cr='0;wr='0;jv='0;js='0;cwv='0;cwd='0;rr='1;
    niv='0;nid='0;nit='0;nix='0;niy='0;nis='0;nie='0;nib='0;nlast='1;nready='1;
    niv[0][EAST]=nov[1][WEST];nid[0][EAST]=nod[1][WEST];
    nit[0][EAST]=notype[1][WEST];nix[0][EAST]=nox[1][WEST];niy[0][EAST]=noy[1][WEST];
    nis[0][EAST]=nos[1][WEST];nie[0][EAST]=noe[1][WEST];nib[0][EAST]=nob[1][WEST];nlast[0][EAST]=olast[1][WEST];
    nready[1][WEST]=nir[0][EAST];
    niv[1][WEST]=nov[0][EAST];nid[1][WEST]=nod[0][EAST];
    nit[1][WEST]=notype[0][EAST];nix[1][WEST]=nox[0][EAST];niy[1][WEST]=noy[0][EAST];
    nis[1][WEST]=nos[0][EAST];nie[1][WEST]=noe[0][EAST];nib[1][WEST]=nob[0][EAST];nlast[1][WEST]=olast[0][EAST];
    nready[0][EAST]=nir[1][WEST];
    if(stall_period>0 && (cycle%stall_period)<5) rr='0;
    if(USE_MESH) begin
      cr[2]='1;
      for(int e=0;e<ENGINES;e++) if(cross_choice<0 && wv[2][e]) cross_choice=e;
      if(cross_choice>=0) begin
        niv[0][LOCAL]=wv[2][cross_choice];nid[0][LOCAL]=wd[2][cross_choice];nit[0][LOCAL]=3;
        nix[0][LOCAL]=(int'(ca[2][cross_choice])>=4);nib[0][LOCAL]=ca[2][cross_choice];
        nie[0][LOCAL]=16'(cb[2][cross_choice]);nlast[0][LOCAL]=(cross_beat[cross_choice]==31);
        wr[2][cross_choice]=nir[0][LOCAL];
      end
      for(int n=0;n<2;n++) begin
        int dst;
        dst=int'(nob[n][LOCAL]);
        nready[n][LOCAL]=0;
        if(nov[n][LOCAL]) begin
          if(jr[dst]) begin
            jv[dst]=1;js[dst]=GW'(noe[n][LOCAL]);
          end
          if(active_source[dst]==2 && cwr[dst]) begin
            cwv[dst]=1;cwd[dst]=nod[n][LOCAL];nready[n][LOCAL]=1;used[dst/2]=1;
          end
        end
      end
    end
    for(int s=0;s<SOURCES;s++) for(int e=0;e<ENGINES;e++) begin
      int dst;
      dst=int'(ca[s][e]);
      if((!USE_MESH||s!=2) && cv[s][e] && !jv[dst] && jr[dst]) begin
        jv[dst]=1;js[dst]=cb[s][e];cr[s][e]=1;
      end
    end
    for(int s=0;s<SOURCES;s++) for(int e=0;e<ENGINES;e++) begin
      int dst;
      dst=int'(ca[s][e]);
      if((!USE_MESH||s!=2) && wv[s][e] && active_source[dst]==s && active_engine[dst]==e && !used[dst/2] && !used_source[s]) begin
        cwv[dst]=1;cwd[dst]=wd[s][e];wr[s][e]=cwr[dst];
        used[dst/2]=1;used_source[s]=1;
      end
    end
  end

  always @(posedge clk) begin
    if(!rst) begin
      for(int s=0;s<SOURCES;s++) begin
        int accepted;
        accepted=0;
        for(int e=0;e<ENGINES;e++) begin
        if(sv[s][e]&&sr[s][e]) begin
          accepted++;
          $display("CORE_INT cycle=%0d event=schedule source=%0d dst=%0d src=%0d",cycle,s,sa[s][e],sb[s][e]);
        end
        if(cv[s][e]&&cr[s][e]) begin
          if(!USE_MESH||s!=2) begin
            active_source[int'(ca[s][e])] <= s;
            active_engine[int'(ca[s][e])] <= e;
          end
          $display("CORE_INT cycle=%0d event=command source=%0d dst=%0d src=%0d",cycle,s,ca[s][e],cb[s][e]);
        end
        if(wv[s][e]&&wr[s][e])
          $display("CORE_INT cycle=%0d event=weight source=%0d dst=%0d src=%0d",cycle,s,ca[s][e],cb[s][e]);
        if(wv[s][e]&&!wr[s][e])
          $display("CORE_INT cycle=%0d event=weight_stall source=%0d dst=%0d src=%0d",cycle,s,ca[s][e],cb[s][e]);
      end
        issued[s] <= issued[s]+accepted;
      end
      if(USE_MESH) begin
        if(cross_choice>=0) begin
          cross_lock<=cross_choice;
          if(wv[2][cross_choice]&&wr[2][cross_choice]) begin
            cross_beat[cross_choice] <= (cross_beat[cross_choice]==31)?0:cross_beat[cross_choice]+1;
            if(cross_beat[cross_choice]==31) cross_lock<=-1;
          end
        end
        for(int n=0;n<2;n++) begin
          int dst;
          dst=int'(nob[n][LOCAL]);
          if(nov[n][LOCAL]&&jr[dst]) active_source[dst]<=2;
          if(nov[n][LOCAL]&&nready[n][LOCAL])
            $display("CORE_INT cycle=%0d event=eject source=2 dst=%0d src=%0d",cycle,dst,noe[n][LOCAL]);
          if(nov[n][LOCAL]&&!nready[n][LOCAL])
            $display("CORE_INT cycle=%0d event=eject_stall source=2 dst=%0d src=%0d",cycle,dst,noe[n][LOCAL]);
        end
        if(nov[0][EAST]&&nready[0][EAST])
          $display("CORE_INT cycle=%0d event=link source=2 dst=%0d src=%0d",cycle,nob[0][EAST],noe[0][EAST]);
        if(nov[0][EAST]&&!nready[0][EAST])
          $display("CORE_INT cycle=%0d event=link_stall source=2 dst=%0d src=%0d",cycle,nob[0][EAST],noe[0][EAST]);
      end
      for(int c=0;c<CORES;c++) if(rv[c]&&rr[c]) begin
        completed=completed+1;
        $display("CORE_INT cycle=%0d event=retire source=%0d dst=%0d src=%0d",cycle,active_source[c],c,rs[c]);
      end
      cycle<=cycle+1;
      az_dram_tick(40);
      if(cycle>100000) $fatal(1,"core memory integration timeout");
    end
  end

  always @(negedge clk) begin
    if(!rst) for(int s=0;s<SOURCES;s++) begin
      int other;
      int allocated;
      other=(s==0)?1:((s==1)?2:4);
      allocated=issued[s];
      for(int e=0;e<ENGINES;e++) begin
        sv[s][e]=(allocated<JOBS_PER_SOURCE && sr[s][e]);
        sa[s][e]=GW'((allocated%2==0)?0:other);
        sb[s][e]=GW'((allocated%2==0)?other:0);
        if(sv[s][e]) allocated++;
      end
    end
  end

  initial begin
    void'($value$plusargs("RAMULATOR_CONFIG=%s",config_path));
    void'($value$plusargs("DATASET=%s",dataset_path));
    void'($value$plusargs("STALL_PERIOD=%d",stall_period));
    sv='0;sa='0;sb='0;
    for(int s=0;s<SOURCES;s++) issued[s]=0;
    for(int c=0;c<CORES;c++) begin active_source[c]=-1;active_engine[c]=-1;end
    for(int e=0;e<ENGINES;e++) cross_beat[e]=0;
    az_dram_init_timing(config_path,dataset_path,8,8);
    repeat(4) @(negedge clk);
    rst=0;
    wait(completed==SOURCES*JOBS_PER_SOURCE);
    @(negedge clk);
    $display("PASS CORE_MEMORY_COMPONENT_INTEGRATION cycles=%0d jobs=%0d mesh=%0d",cycle,completed,USE_MESH);
    az_dram_finalize();$finish;
  end
endmodule
