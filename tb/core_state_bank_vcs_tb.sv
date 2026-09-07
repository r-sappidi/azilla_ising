`timescale 1ns/1ps
module core_state_bank_vcs_tb;
  logic clk=0,rst=1,wv=0;
  logic [5:0] wi=0;
  logic [31:0] wd=0;
  logic [3:0] qv=0,qr,rv;
  logic [3:0][5:0] qi=0;
  logic [3:0][31:0] rd;
  integer cycle=0;
  always #5 clk=~clk;
  banked_state_sram #(.STATE_ENTRY_COUNT(64),.STATE_BANK_COUNT(8),
    .REQUEST_COUNT(4),.STATE_W(32)) dut(
    .clk,.rst,.write_valid_i(wv),.write_index_i(wi),.write_data_i(wd),
    .request_valid_i(qv),.request_ready_o(qr),.request_index_i(qi),
    .response_valid_o(rv),.response_data_o(rd));
  task automatic step;
    #0.001;
    $display("BANK %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
      cycle,wv,wi,wd,qv,qi[0],qi[1],qi[2],qi[3],qr,rv,
      rd[0],rd[1],rd[2],rd[3],rst);
    @(posedge clk);@(negedge clk);cycle++;
  endtask
  initial begin
    step();step();rst=0;
    for(integer i=0;i<64;i++) begin
      wv=1;wi=6'(i);wd=32'ha5000000^32'(i);step();
    end
    wv=0;
    // All lanes contend for bank zero. Each valid is held until accepted.
    qv=15;qi={6'd24,6'd16,6'd8,6'd0};
    for(integer i=0;i<4;i++) begin
      logic [3:0] accepted;
      #0.001;accepted=qv&qr;step();qv=qv&~accepted;
    end
    step();
    // Four banks accept concurrently. Refresh frozen word zero on the same
    // edge it is read: response must contain the pre-write value.
    qv=15;qi={6'd3,6'd2,6'd1,6'd0};wv=1;wi=0;wd=32'hdeadbeef;step();
    wv=0;qv=0;step();
    qv=1;qi[0]=0;step();qv=0;step();
    // Persistent high-priority demand stalls lower lanes for several cycles.
    qv=15;qi={6'd25,6'd17,6'd9,6'd1};
    repeat(5) step();qv=14;
    for(integer i=0;i<3;i++) begin
      logic [3:0] accepted;
      #0.001;accepted=qv&qr;step();qv=qv&~accepted;
    end
    step();
    $display("PASS CORE_STATE_BANK_STIMULUS cycles=%0d",cycle);$finish;
  end
endmodule
