`timescale 1ns/1ps
import ising_pkg::*;

// Arithmetic/lifecycle proof for one physical MVM, resident diagonal slot0,
// streamed slot1, normal and transpose products, synchronous state responses.
module core_shared_mvm_vcs_tb;
  logic clk=0,rst=1;
  always #5 clk=~clk;
  logic init_start=0,init_done,wiv=0,wir;
  logic [DATA_W-1:0] wid='0;
  logic iter_start=0,partials_done=0,commit=0,iter_done;
  logic jv=0,jr,jtranspose=0,sreq,sready,srsp=0,wv=0,wr,jdone;
  logic [2:0] source_id=0,sindex;
  logic [SPIN_COUNT-1:0] sdata=0,current,next_state;
  logic [DATA_W-1:0] wd=0;
  logic [31:0] expected_state=32'ha55aa55a,noise_state=32'h12345678;
  int cycle=0,iteration=0,errors=0,retirements=0;

  function automatic int weight(input int block_kind,input int row,input int col);
    int lo,hi;
    if(block_kind==0) begin
      lo=(row<col)?row:col;hi=(row<col)?col:row;
      return (row==col)?0:((lo*5+hi*11)%19)-9;
    end
    if(row==0&&col==0) return -128;
    return ((row*13+col*7+block_kind*29)%255)-127;
  endfunction
  function automatic logic [31:0] source_state(input int id,input int epoch);
    return 32'hf00dcafe ^ (32'h31415927*32'(id+1)) ^ (32'h1020304*32'(epoch));
  endfunction
  function automatic logic [DATA_W-1:0] row_data(input int kind,input int row);
    logic [DATA_W-1:0] value;
    for(int col=0;col<SPIN_COUNT;col++) value[col*WEIGHT_W+:WEIGHT_W]=WEIGHT_W'(weight(kind,row,col));
    return value;
  endfunction

  spin_core #(.CORES_ONLY(1),.GLOBAL_BLOCK_ID_W(3)) dut (
    .clk,.rst,.init_start,.init_done,.weight_init_valid(wiv),.weight_init_ready(wir),.weight_init_data(wid),
    .noise_seed(32'h12345678),.coeff_a(16'sd3),.coeff_b(-16'sd2),.coeff_c(16'sd0),
    .noise_amplitude(16'sd5),.init_state(32'ha55aa55a),.iter_start,.partials_done,.commit,
    .noise_decay(17'd0),.iter_done,.done(1'b0),.h0_partial_valid(1'b0),.h0_partial_ready(),
    .h0_partial_data('0),.ext_partial_valid(1'b0),.ext_partial_ready(),.ext_partial_data('0),
    .core_job_valid(jv),.core_job_ready(jr),.core_job_source_block_id(source_id),
    .core_job_transpose(jtranspose),.core_state_req_valid(sreq),.core_state_req_ready(sready),
    .core_state_req_block_id(sindex),.core_state_rsp_valid(srsp),.core_state_rsp_data(sdata),
    .core_weight_valid(wv),.core_weight_ready(wr),.core_weight_data(wd),.core_job_done(jdone),
    .state_current(current),.state_next(next_state));

  assign sready=(cycle%5)>=2;
  always @(posedge clk) begin
    if(rst) begin cycle<=0;srsp<=0;retirements<=0;end
    else begin
      $display("CORE_SHARED_CYCLE %0d %0d %0d %h %0d %0d %0d %0d %0d %0d %0d %0d %h %0d %h %0d %0d %0d %h %h %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %h",
        cycle,init_start,wiv,wid,iter_start,partials_done,commit,jv,source_id,jtranspose,
        sready,srsp,sdata,wv,wd,init_done,wir,iter_done,current,next_state,jr,sreq,sindex,wr,jdone,
        dut.core_state,dut.job_state,dut.diagonal_done,dut.mvm_done,dut.job_weight_beat,dut.lfsr_state);
      cycle<=cycle+1;
      srsp<=sreq&&sready;
      if(sreq&&sready) sdata<=source_state(int'(sindex),iteration);
      if(jdone) retirements<=retirements+1;
      if(cycle>10000) $fatal(1,"shared core timeout");
    end
  end
  task automatic step;
    @(posedge clk);@(negedge clk);
  endtask
  task automatic directed_job(input int src,input bit trans,input int kind);
    source_id=3'(src);jtranspose=trans;jv=1;
    do step(); while(!dut.core_state_req_valid);
    jv=0;
    while(!wr) step();
    for(int row=0;row<32;row++) begin
      if(row%5==0) begin wv=0;step();end
      wd=row_data(kind,row);wv=1;
      if(!wr) $fatal(1,"weight interface lost readiness before row");
      step();
    end
    wv=0;
    while(!jdone) step();
    step();
  endtask

  initial begin
    logic [31:0] expected_next,s0,s2;
    int field;
    repeat(3) step();rst=0;
    init_start=1;step();init_start=0;
    for(int row=0;row<32;row++) begin
      if(!wir) $fatal(1,"diagonal SRAM initialization not ready");
      wiv=1;wid=row_data(0,row);step();
    end
    wiv=0;while(!init_done) step();
    for(iteration=0;iteration<3;iteration++) begin
      partials_done=0;iter_start=1;step();iter_start=0;
      noise_state={noise_state[30:0],noise_state[31]^noise_state[21]^noise_state[1]^noise_state[0]};
      // Queue the first request while the diagonal still owns the same MVM.
      directed_job(2,0,1);
      directed_job(0,1,2);
      if(current!==expected_state) $fatal(1,"frozen state changed before commit");
      partials_done=1;step();partials_done=0;
      while(!iter_done) step();
      s0=source_state(0,iteration);s2=source_state(2,iteration);
      for(int row=0;row<32;row++) begin
        field=0;
        for(int col=0;col<32;col++) begin
          field+=expected_state[col]?weight(0,row,col):-weight(0,row,col);
          field+=s2[col]?weight(1,row,col):-weight(1,row,col);
          field+=s0[col]?weight(2,col,row):-weight(2,col,row);
        end
        field=(expected_state[row]?3:-3)-2*field+(noise_state[row]?5:-5);
        expected_next[row]=(field>=0);
        $display("CORE_SHARED_FIELD cycle=%0d lane=%0d value=%0d",cycle,row,$signed(dut.accumulator_total[row]));
        if($signed(dut.accumulator_total[row])!==field) begin
          errors++;
          $display("CORE_SHARED_ERROR iteration=%0d lane=%0d expected=%0d actual=%0d",iteration,row,field,$signed(dut.accumulator_total[row]));
        end
      end
      if(next_state!==expected_next) errors++;
      $display("CORE_SHARED_ITER iteration=%0d cycle=%0d state=%h expected=%h",iteration,cycle,next_state,expected_next);
      commit=1;step();commit=0;step();expected_state=expected_next;
      if(current!==expected_state) $fatal(1,"committed state mismatch");
    end
    if(errors||retirements!=6) $fatal(1,"shared core errors=%0d retirements=%0d",errors,retirements);
    $display("PASS CORE_SHARED_MVM iterations=3 fields=96 directed_jobs=6 physical_mvms=1");
    $finish;
  end
endmodule
