`timescale 1ns/1ps
import ising_pkg::*;

// Two real endpoints, one symmetric 64-spin graph. Independent scalar fields
// and cut objectives check timing-independent solver trajectories over epochs.
// Compile unchanged against either buffered or experimental streaming RTL.
module streaming_endpoint_quality_tb;
  logic clk=0,rst=1; always #5 clk=~clk;
  logic init_start=0,iv=0,iter_start=0,partials_done=0,commit=0;
  logic [1:0] init_done,ir,iter_done,jv=0,jr,sreq,srsp=0,wv=0,wr,jdone;
  logic [1:0][255:0] init_data,wd;
  logic [1:0][31:0] current,next_state,sd,expected,noise_state;
  logic [1:0][0:0] si;
  int cycle=0,family=0,seed=1,amplitude=5,iterations=32,row_init=0;
  int retirements=0;
  function automatic int weight(input int i,input int j);
    int lo,hi,v;
    lo=i<j?i:j;hi=i<j?j:i;
    if(lo==hi) return 0;
    if(family==1 && !((hi-lo==1)||(lo==0&&hi==63))) return 0;
    if(family==2 && ((lo*17+hi*13+seed)%7)>1) return 0;
    v=((lo*19+hi*7+seed*3)%9)-4;
    return v;
  endfunction
  function automatic logic [31:0] initial_bits(input int c);
    return 32'ha55aa55a ^ (32'h1234567*32'(seed+c));
  endfunction
  function automatic logic [31:0] advance(input logic [31:0] n);
    return {n[30:0],n[31]^n[21]^n[1]^n[0]};
  endfunction
  always_comb for(int c=0;c<2;c++) for(int k=0;k<32;k++)
    init_data[c][k*8+:8]=8'(weight(c*32+row_init,c*32+k));
  for(genvar c=0;c<2;c++) begin: cores
    spin_core #(.CORES_ONLY(1),.GLOBAL_BLOCK_ID_W(1)) dut (
      .clk,.rst,.init_start,.init_done(init_done[c]),.weight_init_valid(iv),
      .weight_init_ready(ir[c]),.weight_init_data(init_data[c]),
      .noise_seed(32'(seed+c+1)),.coeff_a(16'sd3),.coeff_b(-16'sd2),.coeff_c(16'sd0),
      .noise_amplitude(16'(amplitude)),.noise_decay(17'd65536),.init_state(initial_bits(c)),
      .iter_start,.partials_done,.commit,.iter_done(iter_done[c]),.done(1'b0),
      .h0_partial_valid(1'b0),.h0_partial_ready(),.h0_partial_data(256'b0),
      .ext_partial_valid(1'b0),.ext_partial_ready(),.ext_partial_data(256'b0),
      .core_job_valid(jv[c]),.core_job_ready(jr[c]),.core_job_source_block_id(1'(1-c)),
      .core_job_transpose(1'(c)),.core_state_req_valid(sreq[c]),.core_state_req_ready(1'b1),
      .core_state_req_block_id(si[c]),.core_state_rsp_valid(srsp[c]),.core_state_rsp_data(sd[c]),
      .core_weight_valid(wv[c]),.core_weight_ready(wr[c]),.core_weight_data(wd[c]),
      .core_job_done(jdone[c]),.state_current(current[c]),.state_next(next_state[c]));
    always @(posedge clk) begin
      srsp[c] <= !rst && sreq[c];
      if(sreq[c]) sd[c] <= current[1-c];
    end
  end
  always @(posedge clk) if(!rst) begin
    cycle<=cycle+1;
    retirements<=retirements+int'(jdone[0])+int'(jdone[1]);
    if(cycle>100000) $fatal(1,"quality timeout");
  end
  task automatic step; @(posedge clk); @(negedge clk); endtask
  initial begin
    int field,cut;
    void'($value$plusargs("FAMILY=%d",family));
    void'($value$plusargs("SEED=%d",seed));
    void'($value$plusargs("NOISE=%d",amplitude));
    repeat(3)step(); rst=0;
    init_start=1;step();init_start=0;
    for(row_init=0;row_init<32;row_init++) begin iv=1;step();end
    iv=0;while(!(&init_done))step();
    for(int c=0;c<2;c++)noise_state[c]=32'(seed+c+1);
    for(int epoch=0;epoch<iterations;epoch++) begin
      for(int c=0;c<2;c++) begin
        noise_state[c]=advance(noise_state[c]);
        for(int r=0;r<32;r++) begin
          field=0;
          for(int j=0;j<64;j++)field+=weight(c*32+r,j)*(current[j/32][j%32]?1:-1);
          field=(current[c][r]?3:-3)-2*field+(noise_state[c][r]?amplitude:-amplitude);
          expected[c][r]=(field>=0);
        end
      end
      iter_start=1;step();iter_start=0;
      while(!(&jr))step();jv='1;step();jv=0;
      while(!(&wr))step();
      for(int r=0;r<32;r++)begin
        // Both endpoints consume the SAME canonical rows; endpoint one transposes.
        for(int c=0;c<2;c++)for(int k=0;k<32;k++)wd[c][k*8+:8]=8'(weight(r,32+k));
        if(r%7==0)begin wv=0;repeat(3)step();end
        wv='1;if(!(&wr))$fatal(1,"unexpected readiness loss");step();
      end
      wv=0;while(!(&jdone))step();step();
      partials_done=1;step();partials_done=0;
      while(!(&iter_done))step();
      if(next_state!==expected)$fatal(1,"independent state mismatch epoch=%0d",epoch);
      cut=0;for(int i=0;i<64;i++)for(int j=i+1;j<64;j++)
        if(next_state[i/32][i%32]!=next_state[j/32][j%32])cut+=weight(i,j);
      $display("QUALITY epoch=%0d states=%h cut=%0d",epoch,next_state,cut);
      commit=1;step();commit=0;step();
      if(current!==expected)$fatal(1,"commit mismatch");
    end
    if(retirements!=2*iterations)$fatal(1,"job loss");
    $display("PASS STREAMING_QUALITY cycles=%0d iterations=%0d",cycle,iterations);
    $finish;
  end
endmodule
