`timescale 1ns/1ps
module floo_priority_tree_vcs_tb;
  logic [4:0] requests=0;
  logic [2:0] priority_index=0,selected;
  cc_rr_arb_tree #(.NumIn(5),.data_t(logic),.ExtPrio(1),
    .AxiVldRdy(1),.LockIn(0),.FairArb(1)) dut (
    .clk_i(1'b0),.rst_ni(1'b1),.clr_i(1'b0),.rr_i(priority_index),
    .req_i(requests),.gnt_o(),.data_i(5'b0),.req_o(),.gnt_i(1'b1),
    .data_o(),.idx_o(selected));
  initial begin
    for(integer p=0;p<5;p++) for(integer mask=1;mask<32;mask++) begin
      priority_index=3'(p);requests=5'(mask);#1;
      $display("PRIORITY %0d %0d %0d",p,mask,selected);
    end
    $finish;
  end
endmodule
