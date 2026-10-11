`timescale 1ns/1ps
import ising_pkg::*;
module cir_hierarchy_replay_tb;
  logic clk=0, rst=1, iter_start=0, iter_done;
  logic [3:0] cv=0, cr, wv=0, wr, pv, pr=0, pl;
  logic [3:0][3:0] ai=0, bi=0;
  logic [3:0][15:0] ba=0, bb=0, pb;
  logic [3:0][255:0] wd=0, pd;
  logic sr, sd=0;
  integer fd, rc, cyc, cv_i, wv_i, pr_i, sd_i;
  integer a0,b0,a1,b1,a2,b2,a3,b3;
  integer ba0,bb0,ba1,bb1,ba2,bb2,ba3,bb3;
  string stimulus;
  always #5 clk=~clk;
  hierarchy_node #(.STATE_ENTRY_COUNT(16), .MVM_COUNT(4),
                  .GLOBAL_BLOCK_ID_W(16), .TIMING_ONLY(1)) dut (
    .clk, .rst, .iter_start, .iter_done,
    .state_valid_i(1'b0), .state_ready_o(sr),
    .state_index_i(4'b0), .state_data_i(32'b0), .schedule_done_i(sd),
    .dma_cmd_valid_i(cv), .dma_cmd_ready_o(cr),
    .dma_state_a_index_i(ai), .dma_state_b_index_i(bi),
    .dma_block_a_i(ba), .dma_block_b_i(bb),
    .dma_weight_valid_i(wv), .dma_weight_ready_o(wr), .dma_weight_data_i(wd),
    .partial_valid_o(pv), .partial_ready_i(pr),
    .partial_data_o(pd), .partial_block_id_o(pb), .partial_last_o(pl)
  );
  initial begin
    if (!$value$plusargs("STIMULUS=%s", stimulus)) $fatal(1,"STIMULUS required");
    fd=$fopen(stimulus,"r");
    repeat(2) @(negedge clk);
    rst=0;
    @(negedge clk); iter_start=1;
    @(negedge clk); iter_start=0;
    while (!$feof(fd)) begin
      rc=$fscanf(fd,"%d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d\n",
        cyc,cv_i,wv_i,pr_i,sd_i,a0,b0,ba0,bb0,a1,b1,ba1,bb1,a2,b2,ba2,bb2,a3,b3,ba3,bb3);
      if (rc !=21) $fatal(1,"bad row %0d",rc);
      cv=cv_i; wv=wv_i; pr=pr_i; sd=sd_i;
      ai={4'(a3),4'(a2),4'(a1),4'(a0)};
      bi={4'(b3),4'(b2),4'(b1),4'(b0)};
      ba={16'(ba3),16'(ba2),16'(ba1),16'(ba0)};
      bb={16'(bb3),16'(bb2),16'(bb1),16'(bb0)};
      #1;
      $display("REPLAY %0d %0d %0d %0d",cyc,cr,wr,pv);
      for(integer e=0;e<4;e++)
        $display("ENGINE %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",cyc,e,
          dut.engine_state[e],dut.output_state[e],dut.engine_slot_valid[e],
          dut.engine_result_slot_valid[e],dut.engine_state_a_valid[e],
          dut.engine_state_b_valid[e],dut.engine_done[e], dut.engine_active_slot[e]);
      @(negedge clk);
    end
    $finish;
  end
endmodule
