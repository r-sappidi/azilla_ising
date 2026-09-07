`timescale 1ns/1ps
import ising_pkg::*;

module core_state_publication_vcs_tb;
  localparam int LOCAL=0,EAST=3,WEST=4;
  logic clk=0,rst=1;
  always #5 clk=~clk;
  logic [1:0][4:0] iv,ir,ov,ord,ilast,olast;
  logic [1:0][4:0][255:0] id,od;
  logic [1:0][4:0][1:0] itype,otype;
  logic [1:0][4:0][0:0] ix,iy,ox,oy;
  logic [1:0][4:0][7:0] isource,osource;
  logic [1:0][4:0][15:0] iepoch,oepoch;
  logic [1:0][4:0][2:0] iblock,oblock;
  logic [31:0] h1_state[2][8];
  logic [3:0] write_valid,request_valid,request_ready,response_valid;
  logic [3:0][2:0] write_index,request_index;
  logic [3:0][31:0] write_data,response_data;
  logic gather=0,network=0,fill=0,compute_enable=0;
  int cycle=0,epoch=0,gather_index=0,fill_index=0,net_start=0;
  int received=0;
  logic [1:0] sent=0;
  logic last_activity=0;
  function automatic logic [31:0] state_word(input int e,input int block_id);
    return (32'h9e3779b9*32'(block_id+1)) ^ (32'h1020304*32'(e+1));
  endfunction
  function automatic bit needed(input int h0,input int block_id);
    if(h0==0) return block_id==0||block_id==1||block_id==2||block_id==4;
    if(h0==1||h0==2) return block_id==0;
    return 0;
  endfunction
  for(genvar n=0;n<2;n++) begin: routers
    azilla_floo_router #(.X_W(1),.Y_W(1),.SOURCE_ID_W(8),.EPOCH_W(16),
      .GLOBAL_BLOCK_ID_W(3),.FIFO_DEPTH(4),.ROUTER_X(n),.ROUTER_Y(0)) router (
      .clk,.rst,.in_valid_i(iv[n]),.in_ready_o(ir[n]),.in_data_i(id[n]),
      .in_type_i(itype[n]),.in_dest_x_i(ix[n]),.in_dest_y_i(iy[n]),
      .in_source_id_i(isource[n]),.in_epoch_i(iepoch[n]),.in_block_id_i(iblock[n]),
      .in_last_i(ilast[n]),.out_valid_o(ov[n]),.out_ready_i(ord[n]),
      .out_data_o(od[n]),.out_type_o(otype[n]),.out_dest_x_o(ox[n]),
      .out_dest_y_o(oy[n]),.out_source_id_o(osource[n]),.out_epoch_o(oepoch[n]),
      .out_block_id_o(oblock[n]),.out_last_o(olast[n]));
  end
  for(genvar h=0;h<4;h++) begin: caches
    banked_state_sram #(.STATE_ENTRY_COUNT(8),.STATE_W(32),.STATE_BANK_COUNT(8),.REQUEST_COUNT(1)) cache (
      .clk,.rst,.write_valid_i(write_valid[h]),.write_index_i(write_index[h]),.write_data_i(write_data[h]),
      .request_valid_i(request_valid[h]),.request_ready_o(request_ready[h]),.request_index_i(request_index[h]),
      .response_valid_o(response_valid[h]),.response_data_o(response_data[h]));
  end
  always_comb begin
    iv='0;id='0;itype='0;ix='0;iy='0;isource='0;iepoch='0;iblock='0;ilast='1;ord='1;
    iv[0][EAST]=ov[1][WEST];id[0][EAST]=od[1][WEST];itype[0][EAST]=otype[1][WEST];
    ix[0][EAST]=ox[1][WEST];iy[0][EAST]=oy[1][WEST];isource[0][EAST]=osource[1][WEST];
    iepoch[0][EAST]=oepoch[1][WEST];iblock[0][EAST]=oblock[1][WEST];ilast[0][EAST]=olast[1][WEST];ord[1][WEST]=ir[0][EAST];
    iv[1][WEST]=ov[0][EAST];id[1][WEST]=od[0][EAST];itype[1][WEST]=otype[0][EAST];
    ix[1][WEST]=ox[0][EAST];iy[1][WEST]=oy[0][EAST];isource[1][WEST]=osource[0][EAST];
    iepoch[1][WEST]=oepoch[0][EAST];iblock[1][WEST]=oblock[0][EAST];ilast[1][WEST]=olast[0][EAST];ord[0][EAST]=ir[1][WEST];
    if(network) for(int n=0;n<2;n++) begin
      iv[n][LOCAL]=!sent[n] && cycle-net_start>=2*n;
      id[n][LOCAL][31:0]=h1_state[n][n*4];ix[n][LOCAL]=1'(1-n);
      isource[n][LOCAL]=8'(n);iepoch[n][LOCAL]=16'(epoch);iblock[n][LOCAL]=3'(n*4);
    end
    write_valid='0;write_index='0;write_data='0;
    if(fill) begin
      if(fill_index<4) begin
        write_valid[0]=1;write_index[0]=3'((fill_index==3)?4:fill_index);
        write_data[0]=h1_state[0][write_index[0]];
      end else begin write_valid[1]=1;write_index[1]=0;write_data[1]=h1_state[0][0];end
      if(fill_index==0) begin write_valid[2]=1;write_index[2]=0;write_data[2]=h1_state[1][0];end
    end
  end
  always @(posedge clk) begin
    if(rst) cycle<=0;
    else begin
      cycle<=cycle+1;
      if(gather) for(int n=0;n<2;n++) begin
        h1_state[n][n*4+gather_index]<=state_word(epoch,n*4+gather_index);
        $display("PUB_LOCAL epoch=%0d cycle=%0d event=gather h1=%0d h0=-1 block=%0d data=%h",epoch,cycle,n,n*4+gather_index,state_word(epoch,n*4+gather_index));
      end
      if(network) begin
        int accepted;
        accepted=0;last_activity<=|iv|| |ov;
        for(int n=0;n<2;n++) begin
          if(iv[n][LOCAL]&&ir[n][LOCAL]) begin
            sent[n]<=1;
            $display("PUB_NET epoch=%0d cycle=%0d scope=inject node=%0d block=%0d data=%h",epoch,cycle,n,iblock[n][LOCAL],id[n][LOCAL][31:0]);
          end
          if(ov[n][LOCAL]&&ord[n][LOCAL]) begin
            accepted++;
            if(oepoch[n][LOCAL]!=epoch || od[n][LOCAL][31:0]!==state_word(epoch,oblock[n][LOCAL]))
              $fatal(1,"state publication payload/epoch mismatch");
            h1_state[n][oblock[n][LOCAL]]<=od[n][LOCAL][31:0];
            $display("PUB_NET epoch=%0d cycle=%0d scope=eject node=%0d block=%0d data=%h",epoch,cycle,n,oblock[n][LOCAL],od[n][LOCAL][31:0]);
          end
        end
        if(ov[0][EAST]&&ord[0][EAST]) $display("PUB_NET epoch=%0d cycle=%0d scope=link node=0 block=%0d data=%h",epoch,cycle,oblock[0][EAST],od[0][EAST][31:0]);
        if(ov[1][WEST]&&ord[1][WEST]) $display("PUB_NET epoch=%0d cycle=%0d scope=link node=1 block=%0d data=%h",epoch,cycle,oblock[1][WEST],od[1][WEST][31:0]);
        received<=received+accepted;
      end
      for(int h=0;h<4;h++) if(write_valid[h])
        $display("PUB_LOCAL epoch=%0d cycle=%0d event=fill h1=%0d h0=%0d block=%0d data=%h",epoch,cycle,h/2,h,write_index[h],write_data[h]);
      if(compute_enable && fill) $fatal(1,"compute started before cache fill drained");
      if(cycle>1000) $fatal(1,"publication timeout");
    end
  end
  task automatic step;
    @(posedge clk);@(negedge clk);
  endtask
  initial begin
    int quiet;
    request_valid='0;request_index='0;
    repeat(3) step();rst=0;
    for(epoch=0;epoch<2;epoch++) begin
      compute_enable=0;
      $display("PUB_PHASE epoch=%0d phase=gather cycle=%0d",epoch,cycle);
      gather=1;
      for(gather_index=0;gather_index<4;gather_index++) step();
      gather=0;net_start=cycle;network=1;sent=0;received=0;quiet=0;
      $display("PUB_PHASE epoch=%0d phase=network cycle=%0d",epoch,cycle);
      while(received<2 || quiet<4) begin
        step();
        if(received==2&&!last_activity) quiet++;else quiet=0;
      end
      network=0;fill=1;
      $display("PUB_PHASE epoch=%0d phase=fill cycle=%0d",epoch,cycle);
      for(fill_index=0;fill_index<5;fill_index++) step();
      fill=0;compute_enable=1;
      $display("PUB_PHASE epoch=%0d phase=compute_ready cycle=%0d",epoch,cycle);
      for(int block_id=0;block_id<8;block_id++) begin
        for(int h=0;h<4;h++) begin
          request_valid[h]=needed(h,block_id);request_index[h]=3'(block_id);
        end
        step();
        for(int h=0;h<4;h++) if(needed(h,block_id)) begin
          if(!response_valid[h] || response_data[h]!==state_word(epoch,block_id))
            $fatal(1,"stale/missing frozen state epoch=%0d h0=%0d block=%0d",epoch,h,block_id);
          $display("PUB_READ epoch=%0d cycle=%0d h0=%0d block=%0d data=%h",epoch,cycle,h,block_id,response_data[h]);
        end
      end
      request_valid='0;step();
    end
    $display("PASS CORE_STATE_PUBLICATION epochs=2 remote_flits=4 cache_writes=12");$finish;
  end
endmodule
