`timescale 1ns/1ps
import ising_pkg::*;

// Canonical DRAM, shared local delivery, and real router traffic feeding the
// single MVM in each real spin core. Two complete arithmetic iterations check
// diagonal storage, banked frozen operands, transpose, update, and commit.
// Frozen states and epoch completion use the same persistent routers as weights.
module core_iteration_publication_vcs_tb #(parameter bit USE_MESH=1, parameter int ENGINES=1,
  parameter int H0_ENGINES=ENGINES, parameter int H1_ENGINES=ENGINES,
  parameter int CROSS_ENGINES=ENGINES, parameter int JOBS_PER_SOURCE=2);
  localparam int SOURCES=3, CORES=8, GW=3;
  function automatic int source_engines(input int source);
    return (source==0)?H0_ENGINES:((source==1)?H1_ENGINES:CROSS_ENGINES);
  endfunction
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
  string dataset_path="results/paper_validation_20260907/core_iteration_dataset.txt";

  import "DPI-C" function void az_dram_init(
    input string config_path,input string dataset_path,
    input int systems,input int blocks);
  import "DPI-C" function void az_dram_tick(input int ticks);
  import "DPI-C" function void az_dram_finalize();

  // Geometry: 2 H1 x 2 H0 x 2 cores. Sources are system IDs 0 (H0),
  // 4 (H1-local), and 6 (cross-H1 owner node zero).
  for(genvar s=0;s<SOURCES;s++) begin: memories
    localparam int SYS=(s==0)?0:((s==1)?4:6);
    localparam int COUNT=(s==0)?H0_ENGINES:((s==1)?H1_ENGINES:CROSS_ENGINES);
    if(COUNT<ENGINES) begin: unused_lanes
      assign sr[s][ENGINES-1:COUNT]='0;
      assign cv[s][ENGINES-1:COUNT]='0;
      assign ca[s][ENGINES-1:COUNT]='0;
      assign cb[s][ENGINES-1:COUNT]='0;
      assign wv[s][ENGINES-1:COUNT]='0;
      assign wd[s][ENGINES-1:COUNT]='0;
    end
    ramulator_node_frontend #(.SYSTEM_ID(SYS),.MVM_COUNT(COUNT),
      .STATE_INDEX_W(GW),.GLOBAL_BLOCK_ID_W(GW),.TOTAL_BLOCK_COUNT(8),
      .MEM_LANES(16),.MAX_OUTSTANDING(64)) memory (
      .clk,.rst,.sched_cmd_valid_i(sv[s][COUNT-1:0]),.sched_cmd_ready_o(sr[s][COUNT-1:0]),
      .sched_state_a_index_i(sa[s][COUNT-1:0]),.sched_state_b_index_i(sb[s][COUNT-1:0]),
      .sched_block_a_id_i(sa[s][COUNT-1:0]),.sched_block_b_id_i(sb[s][COUNT-1:0]),
      .node_cmd_valid_o(cv[s][COUNT-1:0]),.node_cmd_ready_i(cr[s][COUNT-1:0]),
      .node_state_a_index_o(),.node_state_b_index_o(),
      .node_block_a_id_o(ca[s][COUNT-1:0]),.node_block_b_id_o(cb[s][COUNT-1:0]),
      .node_weight_valid_o(wv[s][COUNT-1:0]),.node_weight_ready_i(wr[s][COUNT-1:0]),
      .node_weight_data_o(wd[s][COUNT-1:0]),.idle_o(idle[s]));
    always @(posedge clk) if(!rst) for(int e=COUNT;e<ENGINES;e++) begin
      assert(!(sv[s][e] || sr[s][e] || cv[s][e] || cr[s][e] || wv[s][e] || wr[s][e]))
        else $fatal(1,"inactive source lane used source=%0d engine=%0d",s,e);
    end
    always @(posedge clk) if(!rst) for(int lane=0;lane<16;lane++) begin
      if(memory.mem_req_valid[lane]&&memory.mem_req_ready[lane])
        $display("CORE_MEM cycle=%0d event=request system=%0d tag=%0d",cycle,SYS,memory.mem_req_tag[lane]);
      if(memory.mem_rsp_valid[lane]&&memory.mem_rsp_ready[lane])
        $display("CORE_MEM cycle=%0d event=response system=%0d tag=%0d",cycle,SYS,memory.mem_rsp_tag[lane]);
    end
  end
  logic pub_gather=0,pub_network=0,pub_fill=0,completion_network=0;
  logic [31:0] h1_state[2][8];
  logic [3:0] cache_write_valid;
  logic [3:0][2:0] cache_write_index;
  logic [3:0][31:0] cache_write_data;
  logic [1:0] control_sent=0;
  logic control_activity=0;
  integer gather_index=0,fill_index=0,control_start=0,control_received=0;
  logic init_start=0, iter_start=0, partials_done=0, commit=0;
  logic [CORES-1:0] init_done, init_ready, iter_done, weight_ready_raw;
  logic [CORES-1:0][31:0] states, next_states;
  logic [CORES-1:0] state_req, state_ready, state_rsp;
  logic [CORES-1:0][GW-1:0] state_index;
  logic [CORES-1:0][31:0] state_data;
  logic init_valid=0, publish_valid=0, dispatch_enable=0;
  logic [GW-1:0] publish_index=0;
  logic [CORES-1:0][DATA_W-1:0] diagonal_data;
  int diagonal_row=0, iteration=0;
  logic allow_weight;
  logic [CORES-1:0] job_done;
  assign allow_weight=!(stall_period>0 && (cycle%stall_period)<5);
  assign cwr=weight_ready_raw & {CORES{allow_weight}};
  assign rv=job_done;
  logic [CORES-1:0][31:0] golden_states;
  function automatic integer coupling(input integer i,input integer j);
    integer a,b,r,c,tmp;
    if(i==j) return 0;
    if(i>j) begin tmp=i;i=j;j=tmp;end
    a=i/32;b=j/32;r=i%32;c=j%32;
    if(a==b) return ((3*r+5*c+a)%5)-2;
    if(a==0 && (b==1 || b==2 || b==4))
      return ((3*r+5*c+a+b)%7)-3;
    return 0;
  endfunction
  always_comb for(int c=0;c<CORES;c++) begin
    diagonal_data[c]='0;
    for(int col=0;col<32;col++)
      diagonal_data[c][col*8+:8]=8'(coupling(c*32+diagonal_row,c*32+col));
  end
  for(genvar h=0;h<4;h++) begin: state_banks
    banked_state_sram #(.STATE_ENTRY_COUNT(8),.STATE_W(32),
      .STATE_BANK_COUNT(8),.REQUEST_COUNT(2)) bank (
      .clk,.rst,.write_valid_i(cache_write_valid[h]),.write_index_i(cache_write_index[h]),
      .write_data_i(cache_write_data[h]),
      .request_valid_i(state_req[2*h+:2]),.request_ready_o(state_ready[2*h+:2]),
      .request_index_i(state_index[2*h+:2]),
      .response_valid_o(state_rsp[2*h+:2]),.response_data_o(state_data[2*h+:2]));
  end
  for(genvar c=0;c<CORES;c++) begin: cores
    spin_core #(.CORES_ONLY(1),.GLOBAL_BLOCK_ID_W(GW)) core (
      .clk,.rst,.init_start,.init_done(init_done[c]),
      .weight_init_valid(init_valid),.weight_init_ready(init_ready[c]),
      .weight_init_data(diagonal_data[c]),
      .noise_seed(32'(c+1)),.coeff_a(16'sd1),.coeff_b(16'sd1),
      .coeff_c(16'sd0),.noise_amplitude(16'sd0),
      .init_state(32'ha5a55a5a ^ (32'h01010101*c)),
      .iter_start,.partials_done,.commit,.noise_decay(17'd65536),
      .iter_done(iter_done[c]),.done(1'b0),
      .h0_partial_valid(1'b0),.h0_partial_ready(),.h0_partial_data(256'b0),
      .ext_partial_valid(1'b0),.ext_partial_ready(),.ext_partial_data(256'b0),
      .state_next(next_states[c]),.state_current(states[c]),
      .core_job_valid(jv[c]),.core_job_ready(jr[c]),
      .core_job_source_block_id(js[c]),.core_job_transpose(GW'(c)>js[c]),
      .core_state_req_valid(state_req[c]),.core_state_req_ready(state_ready[c]),
      .core_state_req_block_id(state_index[c]),.core_state_rsp_valid(state_rsp[c]),
      .core_state_rsp_data(state_data[c]),
      .core_weight_valid(cwv[c] && allow_weight),.core_weight_ready(weight_ready_raw[c]),
      .core_weight_data(cwd[c]),.core_job_done(job_done[c]));
    assign rs[c]=core.job_source;
    always @(posedge clk) if(!rst)
      $display("CORE_DEBUG cycle=%0d core=%0d core_state=%0d job_state=%0d job_ready=%0d weight_ready=%0d diagonal_done=%0d mvm_done=%0d source=%0d transpose=%0d weight_beat=%0d state=%08h next=%08h",
        cycle,c,core.core_state,core.job_state,jr[c],weight_ready_raw[c],
        core.diagonal_done,core.mvm_done,core.job_source,core.job_transpose,
        core.job_weight_beat,states[c],next_states[c]);
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

    if(USE_MESH && !pub_network && !completion_network) begin
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
    if(pub_network || completion_network) begin
      for(int n=0;n<2;n++) begin
        niv[n][LOCAL]=!control_sent[n] &&
          (completion_network || cycle-control_start>=2*n);
        nid[n][LOCAL]='0;
        if(pub_network) nid[n][LOCAL][31:0]=h1_state[n][n*4];
        nit[n][LOCAL]=pub_network?0:2;
        nix[n][LOCAL]=1'(pub_network?(1-n):n);
        nis[n][LOCAL]=8'(n);nie[n][LOCAL]=16'(iteration);
        nib[n][LOCAL]=GW'(pub_network?(n*4):0);
        nlast[n][LOCAL]=1;nready[n][LOCAL]=1;
      end
    end
    cache_write_valid='0;cache_write_index='0;cache_write_data='0;
    if(pub_fill) begin
      if(fill_index<4) begin
        cache_write_valid[0]=1;cache_write_index[0]=3'((fill_index==3)?4:fill_index);
        cache_write_data[0]=h1_state[0][cache_write_index[0]];
      end else begin
        cache_write_valid[1]=1;cache_write_index[1]=0;cache_write_data[1]=h1_state[0][0];
      end
      if(fill_index==0) begin
        cache_write_valid[2]=1;cache_write_index[2]=0;cache_write_data[2]=h1_state[1][0];
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
      if(USE_MESH && !pub_network && !completion_network) begin
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
      for(int c=0;c<CORES;c++) if(job_done[c]) begin
        completed=completed+1;
        $display("CORE_INT cycle=%0d event=retire source=%0d dst=%0d src=%0d",cycle,active_source[c],c,rs[c]);
      end
      cycle<=cycle+1;

      if(cycle>100000) $fatal(1,"core memory integration timeout");
    end
  end

  always @(negedge clk) begin
    #0.002;
    if(!rst && dispatch_enable) for(int s=0;s<SOURCES;s++) begin
      int other;
      int allocated;
      other=(s==0)?1:((s==1)?2:4);
      allocated=issued[s];
      for(int e=0;e<ENGINES;e++) begin
        sv[s][e]=(e<source_engines(s) && allocated<JOBS_PER_SOURCE && sr[s][e]);
        sa[s][e]=GW'((allocated%2==0)?0:other);
        sb[s][e]=GW'((allocated%2==0)?other:0);
        if(sv[s][e]) allocated++;
      end
    end
  end

  always @(negedge clk) if(!rst) az_dram_tick(40);
  always @(posedge clk) if(!rst) begin
    if(init_start || init_valid || publish_valid || iter_start || partials_done || commit)
      $display("CORE_PHASE cycle=%0d iteration=%0d init=%0d rowvalid=%0d row=%0d publish=%0d block=%0d start=%0d done=%0d commit=%0d",
        cycle,iteration,init_start,init_valid,diagonal_row,publish_valid,publish_index,
        iter_start,partials_done,commit);
    for(int c=0;c<CORES;c++) begin
      if(state_req[c] && state_ready[c])
        $display("CORE_STATE cycle=%0d event=request core=%0d block=%0d",cycle,c,state_index[c]);
      if(state_rsp[c])
        $display("CORE_STATE cycle=%0d event=response core=%0d value=%0h",cycle,c,state_data[c]);
    end
  end
  always @(posedge clk) if(!rst) begin
    if(pub_gather) for(int n=0;n<2;n++) begin
      h1_state[n][n*4+gather_index]<=states[n*4+gather_index];
      $display("PUB_LOCAL epoch=%0d cycle=%0d event=gather h1=%0d h0=-1 block=%0d data=%h",
        iteration,cycle,n,n*4+gather_index,states[n*4+gather_index]);
    end
    if(pub_network || completion_network) begin
      integer accepted;
      accepted=0;control_activity<=|niv|| |nov;
      for(int n=0;n<2;n++) begin
        if(niv[n][LOCAL]&&nir[n][LOCAL]) begin
          control_sent[n]<=1;
          $display("CONTROL_NET epoch=%0d cycle=%0d scope=inject node=%0d type=%0d block=%0d data=%h",
            iteration,cycle,n,nit[n][LOCAL],nib[n][LOCAL],nid[n][LOCAL][31:0]);
        end
        if(nov[n][LOCAL]&&nready[n][LOCAL]) begin
          accepted++;
          if(noe[n][LOCAL]!=iteration) $fatal(1,"control epoch mismatch");
          if(pub_network) begin
            if(nod[n][LOCAL][31:0]!==states[nob[n][LOCAL]]) $fatal(1,"published state mismatch");
            h1_state[n][nob[n][LOCAL]]<=nod[n][LOCAL][31:0];
          end
          $display("CONTROL_NET epoch=%0d cycle=%0d scope=eject node=%0d type=%0d block=%0d data=%h",
            iteration,cycle,n,notype[n][LOCAL],nob[n][LOCAL],nod[n][LOCAL][31:0]);
        end
      end
      if(nov[0][EAST]&&nready[0][EAST])
        $display("CONTROL_NET epoch=%0d cycle=%0d scope=link node=0 type=%0d block=%0d data=%h",
          iteration,cycle,notype[0][EAST],nob[0][EAST],nod[0][EAST][31:0]);
      if(nov[1][WEST]&&nready[1][WEST])
        $display("CONTROL_NET epoch=%0d cycle=%0d scope=link node=1 type=%0d block=%0d data=%h",
          iteration,cycle,notype[1][WEST],nob[1][WEST],nod[1][WEST][31:0]);
      control_received<=control_received+accepted;
    end
    for(int h=0;h<4;h++) if(cache_write_valid[h])
      $display("PUB_LOCAL epoch=%0d cycle=%0d event=fill h1=%0d h0=%0d block=%0d data=%h",
        iteration,cycle,h/2,h,cache_write_index[h],cache_write_data[h]);
  end
  task automatic next_negedge;
    @(negedge clk); #0.001;
  endtask
  initial begin
    if(H0_ENGINES<1 || H1_ENGINES<1 || CROSS_ENGINES<1 ||
       H0_ENGINES>ENGINES || H1_ENGINES>ENGINES || CROSS_ENGINES>ENGINES)
      $fatal(1,"native source engine counts must lie within 1..ENGINES");
    $display("CORE_SOURCE_CONFIG h0=%0d h1=%0d cross=%0d array_max=%0d max_outstanding=64",
      H0_ENGINES,H1_ENGINES,CROSS_ENGINES,ENGINES);
    if(JOBS_PER_SOURCE<2 || JOBS_PER_SOURCE%2)
      $fatal(1,"stress job count must be positive pairs of directed jobs");
    $display("CORE_WORKLOAD repeated_offdiagonal_blocks=%0d graph_case=%0d",
      JOBS_PER_SOURCE/2,JOBS_PER_SOURCE==2);
    void'($value$plusargs("RAMULATOR_CONFIG=%s",config_path));
    void'($value$plusargs("DATASET=%s",dataset_path));
    void'($value$plusargs("STALL_PERIOD=%d",stall_period));
    sv='0;sa='0;sb='0;
    for(int s=0;s<SOURCES;s++) issued[s]=0;
    for(int c=0;c<CORES;c++) begin active_source[c]=-1;active_engine[c]=-1;end
    for(int e=0;e<ENGINES;e++) cross_beat[e]=0;
    az_dram_init(config_path,dataset_path,8,8);
    repeat(4) @(negedge clk);
    rst<=0;
    next_negedge();init_start=1;
    next_negedge();init_start=0;
    for(int row=0;row<32;row++) begin
      diagonal_row=row;init_valid=1;next_negedge();
    end
    init_valid=0;
    wait(&init_done);next_negedge();
    for(iteration=0;iteration<2;iteration++) begin
      for(int c=0;c<CORES;c++) for(int spin=0;spin<32;spin++) begin
        integer field;
        field=states[c][spin]?1:-1;
        for(int other=0;other<256;other++)
          field+=coupling(c*32+spin,other)*(states[other/32][other%32]?1:-1)*
            ((other/32==c)?1:(JOBS_PER_SOURCE/2));
        golden_states[c][spin]=(field>=0);
      end
      iter_start=1;next_negedge();iter_start=0;
      pub_gather=1;
      $display("CONTROL_PHASE epoch=%0d phase=gather cycle=%0d",iteration,cycle);
      for(gather_index=0;gather_index<4;gather_index++) next_negedge();
      pub_gather=0;pub_network=1;control_start=cycle;control_sent=0;control_received=0;
      $display("CONTROL_PHASE epoch=%0d phase=publication_network cycle=%0d",iteration,cycle);
      begin
        integer quiet;
        quiet=0;
        while(control_received<2 || quiet<4) begin
          next_negedge();
          if(control_received==2 && !control_activity) quiet++;else quiet=0;
        end
      end
      pub_network=0;pub_fill=1;
      $display("CONTROL_PHASE epoch=%0d phase=fill cycle=%0d",iteration,cycle);
      for(fill_index=0;fill_index<5;fill_index++) next_negedge();
      pub_fill=0;dispatch_enable=1;
      $display("CONTROL_PHASE epoch=%0d phase=dispatch cycle=%0d",iteration,cycle);
      wait(completed==(iteration+1)*SOURCES*JOBS_PER_SOURCE && (&idle));
      next_negedge();dispatch_enable=0;sv='0;
      completion_network=1;control_start=cycle;control_sent=0;control_received=0;
      $display("CONTROL_PHASE epoch=%0d phase=completion_network cycle=%0d",iteration,cycle);
      begin
        integer quiet;
        quiet=0;
        while(control_received<2 || quiet<4) begin
          next_negedge();
          if(control_received==2 && !control_activity) quiet++;else quiet=0;
        end
      end
      completion_network=0;partials_done=1;
      next_negedge();partials_done=0;
      wait(&iter_done);next_negedge();
      for(int c=0;c<CORES;c++) begin
        $display("CORE_RESULT iteration=%0d core=%0d state=%08h expected=%08h",iteration,c,next_states[c],golden_states[c]);
        if(next_states[c]!==golden_states[c]) $fatal(1,"state mismatch core%0d",c);
      end
      commit=1;next_negedge();commit=0;
      repeat(2) next_negedge();
      for(int s=0;s<SOURCES;s++) issued[s]=0;
    end
    $display("PASS CORE_ITERATION_PUBLICATION cycles=%0d jobs=%0d mesh=%0d iterations=2",cycle,completed,USE_MESH);
    az_dram_finalize();$finish;
  end
endmodule
