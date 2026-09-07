`timescale 1ns/1ps
import ising_pkg::*;
// Full-size single-H1 timing differential. No synthetic compute latency or
// trace-driven readiness: real cores, state banks, streamers and Floo router.
module shared_fetch_single_h1_tb #(
  parameter int H0_COUNT=8, CORES_PER_H0=256,
  parameter int H0_ENGINES=4,H1_ENGINES=2,CROSS_ENGINES=4
);
  localparam int CORES=H0_COUNT*CORES_PER_H0,GW=$clog2(CORES),SOURCES=H0_COUNT+2;
  localparam int ENGINES=(H0_ENGINES>H1_ENGINES)?
       ((H0_ENGINES>CROSS_ENGINES)?H0_ENGINES:CROSS_ENGINES):
       ((H1_ENGINES>CROSS_ENGINES)?H1_ENGINES:CROSS_ENGINES);
  function automatic int lanes(input int s);
    return s<H0_COUNT?H0_ENGINES:(s==H0_COUNT?H1_ENGINES:CROSS_ENGINES);
  endfunction
  logic clk=0,rst=1;always #5 clk=~clk;
  integer cycle=0,completed=0,total_jobs=0,phase=0,offset=0,init_core=0,init_row=0,quiet=0,delay_count=0;
  localparam int INITIAL=0,START_INIT=1,INIT_GAP=2,INIT_ROWS=3,INIT_NEXT=4,WAIT_INIT=5,
    GATHER=6,PUBLICATION=7,FILL=8,COMPUTE=9,COMPLETION=10,DONE=11,UPDATE=12,COMMIT_DELAY=13,FINISHED=14;
  typedef struct packed {logic [GW-1:0] a,b;} pair_t;
  pair_t queue[SOURCES][$];
  integer issued[SOURCES];
  bit needed[H0_COUNT][CORES];
  integer fill_h[$],fill_b[$];
  logic [31:0] frozen[CORES];
  logic [SOURCES-1:0][ENGINES-1:0] sv,sr,cv,cr,wv,wr,rcv,rcr,rwv,rwr,replay_idle;
  logic [SOURCES-1:0][ENGINES-1:0][GW-1:0] sa,sb,ca,cb,ra,rb;
  logic [SOURCES-1:0][ENGINES-1:0][255:0] wd,rwd;
  logic [SOURCES-1:0] idle,raw_idle;
  logic [CORES-1:0] init_done,init_ready,init_valid,iter_done,jv,jr,cwv,cwr,job_done;
  logic [CORES-1:0][GW-1:0] js,state_index;
  logic [CORES-1:0][255:0] cwd;
  logic [CORES-1:0][31:0] states,next_states,state_data;
  logic [CORES-1:0] state_req,state_ready,state_rsp;
  logic [H0_COUNT-1:0] cache_write;
  logic [H0_COUNT-1:0][GW-1:0] cache_index;
  logic [H0_COUNT-1:0][31:0] cache_data;
  integer active_source[CORES],active_engine[CORES],external_lock[H0_COUNT],external_choice[H0_COUNT];
  logic [4:0] external_beat[H0_COUNT];
  logic init_start,iter_start,partials_done,commit;
  string dataset,config_path="tb/ramulator_128x32.yaml";
  import "DPI-C" function void az_dram_init(input string config_path,input string dataset_path,input int systems,input int blocks);
  import "DPI-C" function void az_dram_tick(input int ticks);
  import "DPI-C" function void az_dram_finalize();
  always @(negedge clk) if(!rst) az_dram_tick(40);
  assign init_start=(phase==START_INIT);
  assign iter_start=(phase==WAIT_INIT && (&init_done));
  assign partials_done=(phase==DONE);
  assign commit=(phase==UPDATE && (&iter_done));
  always_comb begin
    init_valid='0;
    if(phase==INIT_ROWS) init_valid[init_core]=1;
    cache_write='0;cache_index='0;cache_data='0;
    if(phase==FILL && offset<fill_h.size()) begin
      cache_write[fill_h[offset]]=1;cache_index[fill_h[offset]]=GW'(fill_b[offset]);
      cache_data[fill_h[offset]]=frozen[fill_b[offset]];
    end
  end
  for(genvar h=0;h<H0_COUNT;h++) begin: banks
    banked_state_sram #(.STATE_ENTRY_COUNT(CORES),.STATE_W(32),.STATE_BANK_COUNT(8),.REQUEST_COUNT(CORES_PER_H0)) bank (
      .clk,.rst,.write_valid_i(cache_write[h]),.write_index_i(cache_index[h]),.write_data_i(cache_data[h]),
      .request_valid_i(state_req[h*CORES_PER_H0+:CORES_PER_H0]),.request_ready_o(state_ready[h*CORES_PER_H0+:CORES_PER_H0]),
      .request_index_i(state_index[h*CORES_PER_H0+:CORES_PER_H0]),.response_valid_o(state_rsp[h*CORES_PER_H0+:CORES_PER_H0]),
      .response_data_o(state_data[h*CORES_PER_H0+:CORES_PER_H0]));
  end
  for(genvar c=0;c<CORES;c++) begin: cores
    spin_core #(.CORES_ONLY(1),.GLOBAL_BLOCK_ID_W(GW)) core (
      .clk,.rst,.init_start,.init_done(init_done[c]),.weight_init_valid(init_valid[c]),.weight_init_ready(init_ready[c]),
      .weight_init_data(256'b0),.noise_seed(32'b0),.coeff_a(16'sd1),.coeff_b(16'sd1),.coeff_c(16'sd0),
      .noise_amplitude(16'sd0),.init_state(32'b0),.iter_start,.partials_done,.commit,.noise_decay(17'd65536),
      .iter_done(iter_done[c]),.done(1'b0),.h0_partial_valid(1'b0),.h0_partial_ready(),.h0_partial_data(256'b0),
      .ext_partial_valid(1'b0),.ext_partial_ready(),.ext_partial_data(256'b0),.state_next(next_states[c]),.state_current(states[c]),
      .core_job_valid(jv[c]),.core_job_ready(jr[c]),.core_job_source_block_id(js[c]),.core_job_transpose(GW'(c)>js[c]),
      .core_state_req_valid(state_req[c]),.core_state_req_ready(state_ready[c]),.core_state_req_block_id(state_index[c]),
      .core_state_rsp_valid(state_rsp[c]),.core_state_rsp_data(state_data[c]),
      .core_weight_valid(cwv[c]),.core_weight_ready(cwr[c]),.core_weight_data(cwd[c]),.core_job_done(job_done[c]));
  end
  for(genvar s=0;s<SOURCES;s++) begin: memories
    localparam int COUNT=lanes(s);
    if(COUNT<ENGINES) begin
      assign sr[s][ENGINES-1:COUNT]='0;assign cv[s][ENGINES-1:COUNT]='0;
      assign ca[s][ENGINES-1:COUNT]='0;assign cb[s][ENGINES-1:COUNT]='0;
      assign wv[s][ENGINES-1:COUNT]='0;assign wd[s][ENGINES-1:COUNT]='0;
    end
    ramulator_node_frontend #(.SYSTEM_ID(s),.MVM_COUNT(COUNT),.STATE_INDEX_W(GW),.GLOBAL_BLOCK_ID_W(GW),
      .TOTAL_BLOCK_COUNT(CORES),.MEM_LANES(16),.MAX_OUTSTANDING(64)) memory (
      .clk,.rst,.sched_cmd_valid_i(sv[s][COUNT-1:0]),.sched_cmd_ready_o(sr[s][COUNT-1:0]),
      .sched_state_a_index_i(sa[s][COUNT-1:0]),.sched_state_b_index_i(sb[s][COUNT-1:0]),
      .sched_block_a_id_i(sa[s][COUNT-1:0]),.sched_block_b_id_i(sb[s][COUNT-1:0]),
      .node_cmd_valid_o(rcv[s][COUNT-1:0]),.node_cmd_ready_i(rcr[s][COUNT-1:0]),.node_state_a_index_o(),.node_state_b_index_o(),
      .node_block_a_id_o(ra[s][COUNT-1:0]),.node_block_b_id_o(rb[s][COUNT-1:0]),
      .node_weight_valid_o(rwv[s][COUNT-1:0]),.node_weight_ready_i(rwr[s][COUNT-1:0]),
      .node_weight_data_o(rwd[s][COUNT-1:0]),.idle_o(raw_idle[s]));
    for(genvar e=0;e<COUNT;e++) begin
      shared_fetch_replay_double #(.BLOCK_W(GW)) replay (.clk,.rst,
        .in_cmd_valid(rcv[s][e]),.in_cmd_ready(rcr[s][e]),.in_a(ra[s][e]),.in_b(rb[s][e]),
        .in_weight_valid(rwv[s][e]),.in_weight_ready(rwr[s][e]),.in_weight_data(rwd[s][e]),
        .out_cmd_valid(cv[s][e]),.out_cmd_ready(cr[s][e]),.out_a(ca[s][e]),.out_b(cb[s][e]),
        .out_weight_valid(wv[s][e]),.out_weight_ready(wr[s][e]),.out_weight_data(wd[s][e]),.idle_o(replay_idle[s][e]));
    end
    assign idle[s]=raw_idle[s] && (&replay_idle[s][COUNT-1:0]);
    always @(posedge clk) if(!rst) for(int lane=0;lane<16;lane++) begin
      if(memory.mem_req_valid[lane] && memory.mem_req_ready[lane])
        $display("CORE_MEM cycle=%0d event=request system=%0d tag=%0d",cycle,s,memory.mem_req_tag[lane]);
      if(memory.mem_rsp_valid[lane] && memory.mem_rsp_ready[lane])
        $display("CORE_MEM cycle=%0d event=response system=%0d tag=%0d",cycle,s,memory.mem_rsp_tag[lane]);
    end
  end
  // One physical local router; only completion control uses it for one H1.
  logic [4:0] niv,nir,nov,nready,nlast,olast;
  logic [4:0][255:0] nod;
  logic [4:0][1:0] nit,notype;
  logic completion_pending=0;integer inflight=0;
  assign niv={4'b0,(phase==COMPLETION && completion_pending)};
  assign nit={8'b0,2'd2};assign nready='1;assign nlast='1;
  azilla_floo_router #(.X_W(1),.Y_W(1),.GLOBAL_BLOCK_ID_W(GW),.FIFO_DEPTH(4)) router (
    .clk,.rst,.in_valid_i(niv),.in_ready_o(nir),.in_data_i('0),
    .in_type_i(nit),.in_dest_x_i('0),.in_dest_y_i('0),.in_source_id_i('0),.in_epoch_i('0),.in_block_id_i('0),
    .in_last_i(nlast),.out_valid_o(nov),.out_ready_i(nready),.out_data_o(nod),.out_type_o(notype),
    .out_dest_x_o(),.out_dest_y_o(),.out_source_id_o(),.out_epoch_o(),.out_block_id_o(),.out_last_o(olast));
  always_comb begin
    cr='0;wr='0;jv='0;js='0;cwv='0;cwd='0;
    for(int h=0;h<H0_COUNT;h++) external_choice[h]=external_lock[h];
    for(int e=0;e<H1_ENGINES;e++) begin
      int dst;dst=int'(ca[H0_COUNT][e]);
      if(wv[H0_COUNT][e] && active_source[dst]==H0_COUNT && active_engine[dst]==e && external_choice[dst/CORES_PER_H0]<0)
        external_choice[dst/CORES_PER_H0]=e;
    end
    for(int s=0;s<=H0_COUNT;s++) for(int e=0;e<lanes(s);e++) begin
      int dst;dst=int'(ca[s][e]);
      if(cv[s][e] && !jv[dst] && jr[dst]) begin jv[dst]=1;js[dst]=cb[s][e];cr[s][e]=1;end
    end
    for(int s=0;s<=H0_COUNT;s++) for(int e=0;e<lanes(s);e++) begin
      int dst;dst=int'(ca[s][e]);
      if(wv[s][e] && active_source[dst]==s && active_engine[dst]==e && !cwv[dst] &&
         (s<H0_COUNT || external_choice[dst/CORES_PER_H0]==e)) begin
        cwv[dst]=1;cwd[dst]=wd[s][e];wr[s][e]=cwr[dst];
      end
    end
  end
  always @(negedge clk) begin
    #0.002;sv='0;sa='0;sb='0;
    if(!rst && phase==COMPUTE) for(int s=0;s<SOURCES;s++) begin
      int cursor;cursor=issued[s];
      for(int e=0;e<lanes(s);e++) if(cursor<queue[s].size() && sr[s][e]) begin
        sv[s][e]=1;sa[s][e]=queue[s][cursor].a;sb[s][e]=queue[s][cursor].b;cursor++;
      end
    end
  end
  always @(posedge clk) if(!rst) begin
    for(int s=0;s<SOURCES;s++) for(int e=0;e<lanes(s);e++) begin
      if(sv[s][e]&&sr[s][e]) begin issued[s]++;
        $display("CORE_INT cycle=%0d event=schedule source=%0d dst=%0d src=%0d",cycle,s,sa[s][e],sb[s][e]);end
      if(cv[s][e]&&cr[s][e]) begin
        active_source[ca[s][e]]<=s;active_engine[ca[s][e]]<=e;
        $display("CORE_INT cycle=%0d event=command source=%0d dst=%0d src=%0d",cycle,s,ca[s][e],cb[s][e]);end
      if(wv[s][e]) $display("CORE_INT cycle=%0d event=%s source=%0d dst=%0d src=%0d",cycle,
        wr[s][e]?"weight":"weight_stall",s,ca[s][e],cb[s][e]);
    end
    for(int c=0;c<CORES;c++) begin
      if(job_done[c]) begin completed++;
        $display("CORE_INT cycle=%0d event=retire source=%0d dst=%0d src=%0d",cycle,active_source[c],c,state_index[c]);end
      if(state_req[c]&&state_ready[c]) $display("CORE_STATE cycle=%0d event=request core=%0d block=%0d",cycle,c,state_index[c]);
      if(state_rsp[c]) $display("CORE_STATE cycle=%0d event=response core=%0d value=%0h",cycle,c,state_data[c]);
      if(cwv[c]&&cwr[c]&&active_source[c]==H0_COUNT) begin
        external_lock[c/CORES_PER_H0]<=external_beat[c/CORES_PER_H0]==31?-1:active_engine[c];
        external_beat[c/CORES_PER_H0]<=external_beat[c/CORES_PER_H0]+1'b1;
      end
    end
    if(niv[0]&&nir[0]) begin completion_pending<=0;inflight++;$display("CORE_NOC cycle=%0d event=inject",cycle);end
    if(nov[0]) begin inflight--;$display("CORE_NOC cycle=%0d event=eject",cycle);end
    case(phase)
      INITIAL:phase<=START_INIT;
      START_INIT:phase<=INIT_GAP;
      INIT_GAP:phase<=INIT_ROWS;
      INIT_ROWS:if(init_row==31) begin init_row<=0;phase<=INIT_NEXT;end else init_row<=init_row+1;
      INIT_NEXT:if(init_core==CORES-1) phase<=WAIT_INIT;else begin init_core<=init_core+1;phase<=INIT_ROWS;end
      WAIT_INIT:if(&init_done) begin $display("FULL_INIT cycle=%0d",cycle);phase<=GATHER;offset<=0;end
      GATHER:begin frozen[offset]<=states[offset];offset<=offset+1;
        if(offset==CORES-1) begin phase<=PUBLICATION;quiet<=0;end end
      PUBLICATION:if(quiet==3) begin phase<=fill_h.size()?FILL:COMPUTE;offset<=0;end else quiet<=quiet+1;
      FILL:if(offset==fill_h.size()-1) phase<=COMPUTE;else offset<=offset+1;
      COMPUTE:if(completed==2*total_jobs && (&idle)) begin phase<=COMPLETION;completion_pending<=1;quiet<=0;end
      COMPLETION:if(inflight==0 && !niv[0] && !(|nov)) begin
        if(quiet==3) phase<=DONE;else quiet<=quiet+1;end else quiet<=0;
      DONE:phase<=UPDATE;
      UPDATE:if(&iter_done) begin phase<=COMMIT_DELAY;delay_count<=2;end
      COMMIT_DELAY:if(delay_count==1) begin
        $display("PASS SHARED_FETCH_SINGLE_H1 total_cycles=%0d jobs=%0d spins=%0d",cycle+1,completed,CORES*32);
        az_dram_finalize();$finish;end else delay_count<=delay_count-1;
      default:;
    endcase
    cycle<=cycle+1;
    if(cycle%10000==0) $display("PROGRESS cycle=%0d phase=%0d jobs=%0d/%0d",cycle,phase,completed,2*total_jobs);
    if(cycle>2000000) $fatal(1,"timeout");
  end
  initial begin
    integer f,n,cut,i,j,w,scan,a,b,s;
    bit pairs[longint unsigned];
    pair_t pair;
    for(int h=0;h<H0_COUNT;h++) begin external_lock[h]=-1;external_beat[h]=0;end
    for(int c=0;c<CORES;c++) begin active_source[c]=-1;active_engine[c]=-1;end
    for(int x=0;x<SOURCES;x++) issued[x]=0;
    if(!$value$plusargs("DATASET=%s",dataset)) $fatal(1,"DATASET required");
    void'($value$plusargs("RAMULATOR_CONFIG=%s",config_path));
    f=$fopen(dataset,"r");if(!f) $fatal(1,"dataset open failed");
    scan=$fscanf(f,"%d %d",n,cut);if(scan!=2 || n!=CORES*32) $fatal(1,"dataset geometry");
    while(!$feof(f)) begin
      scan=$fscanf(f,"%d %d %d",i,j,w);
      if(scan==3 && w!=0) begin a=(i-1)/32;b=(j-1)/32;
        if(a>b) begin s=a;a=b;b=s;end
        if(a!=b) pairs[(64'(a)<<32)|64'(b)]=1;
      end else if(scan!=3 && scan!=-1) $fatal(1,"malformed dataset");
    end
    $fclose(f);
    foreach(pairs[key]) begin
      a=int'(key>>32);b=int'(key);s=(a/CORES_PER_H0==b/CORES_PER_H0)?a/CORES_PER_H0:H0_COUNT;
      pair.a=GW'(a);pair.b=GW'(b);queue[s].push_back(pair);total_jobs++;
      needed[a/CORES_PER_H0][b]=1;needed[b/CORES_PER_H0][a]=1;
    end
    for(int h=0;h<H0_COUNT;h++) for(int c=0;c<CORES;c++) if(needed[h][c]) begin fill_h.push_back(h);fill_b.push_back(c);end
    $display("FULL_CONFIG cores=%0d h0=%0d lanes=%0d/%0d/%0d canonical_jobs=%0d cache_writes=%0d",
      CORES,H0_COUNT,H0_ENGINES,H1_ENGINES,CROSS_ENGINES,total_jobs,fill_h.size());
    az_dram_init(config_path,dataset,SOURCES,CORES);
    repeat(5) @(negedge clk);rst<=0;
  end
endmodule
