`timescale 1ns/1ps
import ising_pkg::*;

// Mixed-ownership RTL test: four star edges execute as two directed endpoint
// jobs and three execute once in a symmetric hierarchy engine. The combined
// result is checked against an independent full-star equation.
module static_hybrid_256_tb;
    localparam int CORES=8;
    logic clk=0, rst=1;
    logic [CORES-1:0] job_valid,job_ready,source_remote,weight_valid,weight_ready;
    logic [CORES-1:0][7:0] source_id;
    logic [CORES-1:0][SPIN_COUNT-1:0] source_state;
    logic [CORES-1:0][DATA_W-1:0] weight_data;
    logic [CORES-1:0] result_valid,result_ready;
    logic signed [ACC_W-1:0] result[0:CORES-1][0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] accum[0:CORES-1][0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] golden[0:CORES-1][0:SPIN_COUNT-1];
    logic [CORES-1:0][SPIN_COUNT-1:0] states;
    logic [0:0] cmd_valid,cmd_ready,wvalid,wready,pvalid,pready,plast;
    logic [0:0][2:0] sa,sb;
    logic [0:0][7:0] ba,bb,pblock;
    logic [0:0][DATA_W-1:0] wdata;
    logic signed [0:0][DATA_W-1:0] pdata;
    logic state_valid,state_ready,iter_start,iter_done,schedule_done;
    logic [2:0] state_index;
    logic [SPIN_COUNT-1:0] state_data;
    int pbeat[0:CORES-1]; int errors=0;
    always #5 clk=~clk;

    function automatic logic signed [WEIGHT_W-1:0] weight(
        input int dst,input int src,input int row,input int col);
        int lo,hi,rr,cc,v;
        lo=dst<src?dst:src; hi=dst<src?src:dst;
        rr=dst<src?row:col; cc=dst<src?col:row;
        v=((lo*17+hi*13+rr*5+cc*11)%15)-7; return WEIGHT_W'(v);
    endfunction

    generate for(genvar c=0;c<CORES;c++) begin:local_engines
        logic [7:0] unused_id; logic [63:0] unused[0:5];
        core_local_mvm_engine #(.GLOBAL_BLOCK_ID_W(8)) dut(
            .clk,.rst,.job_valid_i(job_valid[c]),.job_ready_o(job_ready[c]),
            .source_block_id_i(source_id[c]),.source_state_i(source_state[c]),
            .source_remote_i(source_remote[c]),.weight_valid_i(weight_valid[c]),
            .weight_ready_o(weight_ready[c]),.weight_data_i(weight_data[c]),
            .result_valid_o(result_valid[c]),.result_ready_i(result_ready[c]),
            .result_o(result[c]),.result_source_block_id_o(unused_id),
            .jobs_completed_o(unused[0]),.weight_bytes_o(unused[1]),
            .source_state_bytes_o(unused[2]),
            .remote_source_state_bytes_o(unused[3]),
            .first_job_cycle_o(unused[4]),.last_job_cycle_o(unused[5]));
    end endgenerate

    hierarchy_node #(.STATE_ENTRY_COUNT(CORES),.MVM_COUNT(1),
                     .GLOBAL_BLOCK_ID_W(8)) cir(
        .clk,.rst,.iter_start,.iter_done,.state_valid_i(state_valid),
        .state_ready_o(state_ready),.state_index_i(state_index),
        .state_data_i(state_data),.schedule_done_i(schedule_done),
        .dma_cmd_valid_i(cmd_valid),.dma_cmd_ready_o(cmd_ready),
        .dma_state_a_index_i(sa),.dma_state_b_index_i(sb),
        .dma_block_a_i(ba),.dma_block_b_i(bb),
        .dma_weight_valid_i(wvalid),.dma_weight_ready_o(wready),
        .dma_weight_data_i(wdata),.partial_valid_o(pvalid),
        .partial_ready_i(pready),.partial_data_o(pdata),
        .partial_block_id_o(pblock),.partial_last_o(plast));

    task automatic local_job(input int dst,input int src);
        logic [DATA_W-1:0] word;
        job_valid[dst]=1; source_id[dst]=src; source_state[dst]=states[src];
        source_remote[dst]=0; do @(posedge clk); while(!job_ready[dst]);
        @(negedge clk); job_valid[dst]=0;
        for(int r=0;r<SPIN_COUNT;r++) begin word='0;
            for(int c=0;c<SPIN_COUNT;c++)
                word[c*WEIGHT_W+:WEIGHT_W]=weight(dst,src,r,c);
            weight_data[dst]=word; weight_valid[dst]=1;
            do @(posedge clk); while(!weight_ready[dst]); @(negedge clk);
        end
        weight_valid[dst]=0; do @(posedge clk); while(!result_valid[dst]);
        @(negedge clk); for(int r=0;r<SPIN_COUNT;r++) accum[dst][r]+=result[dst][r];
        result_ready[dst]=1; @(posedge clk); @(negedge clk); result_ready[dst]=0;
    endtask

    task automatic cir_job(input int a,input int b);
        logic [DATA_W-1:0] word;
        cmd_valid[0]=1; sa[0]=a; sb[0]=b; ba[0]=a; bb[0]=b;
        do @(posedge clk); while(!cmd_ready[0]); @(negedge clk); cmd_valid[0]=0;
        for(int r=0;r<SPIN_COUNT;r++) begin word='0;
            for(int c=0;c<SPIN_COUNT;c++)
                word[c*WEIGHT_W+:WEIGHT_W]=weight(a,b,r,c);
            wdata[0]=word; wvalid[0]=1;
            do @(posedge clk); while(!wready[0]); @(negedge clk);
        end wvalid[0]=0;
    endtask

    always @(posedge clk) if(!rst && pvalid[0] && pready[0]) begin
        for(int lane=0;lane<DATA_W/ACC_W;lane++)
            accum[pblock[0]][pbeat[pblock[0]]*(DATA_W/ACC_W)+lane] <=
                accum[pblock[0]][pbeat[pblock[0]]*(DATA_W/ACC_W)+lane] +
                $signed(pdata[0][lane*ACC_W+:ACC_W]);
        pbeat[pblock[0]] <= plast[0]?0:pbeat[pblock[0]]+1;
    end

    initial begin
        job_valid='0;weight_valid='0;result_ready='0;source_remote='0;
        cmd_valid='0;wvalid='0;pready='1;state_valid=0;iter_start=0;schedule_done=0;
        accum='{default:'0};golden='{default:'0}; for(int c=0;c<CORES;c++) pbeat[c]=0;
        for(int c=0;c<CORES;c++) for(int s=0;s<SPIN_COUNT;s++)
            states[c][s]=((c*7+s*3)%5)<2;
        repeat(3) @(posedge clk); @(negedge clk); rst=0;
        for(int c=0;c<CORES;c++) begin state_index=c;state_data=states[c];state_valid=1;
            do @(posedge clk); while(!state_ready); @(negedge clk); end
        state_valid=0;iter_start=1;@(posedge clk);@(negedge clk);iter_start=0;
        fork
            begin local_job(0,1);local_job(0,2);local_job(0,3);local_job(0,4);end
            begin local_job(1,0);end begin local_job(2,0);end
            begin local_job(3,0);end begin local_job(4,0);end
            begin cir_job(0,5);cir_job(0,6);cir_job(0,7);
                  schedule_done=1;@(posedge clk);@(negedge clk);schedule_done=0;end
        join
        wait(iter_done); repeat(2) @(posedge clk);
        for(int s=1;s<CORES;s++) for(int r=0;r<SPIN_COUNT;r++)
            for(int c=0;c<SPIN_COUNT;c++) begin
                golden[0][r]+=states[s][c]?weight(0,s,r,c):-weight(0,s,r,c);
                golden[s][r]+=states[0][c]?weight(s,0,r,c):-weight(s,0,r,c);
            end
        for(int d=0;d<CORES;d++) for(int r=0;r<SPIN_COUNT;r++)
            if(accum[d][r]!==golden[d][r]) begin
                $error("dst=%0d row=%0d got=%0d expected=%0d",d,r,accum[d][r],golden[d][r]);errors++;
            end
        if(errors) $fatal(1,"hybrid arithmetic failed errors=%0d",errors);
        $display("PASS: static hybrid exclusive mixed RTL arithmetic matches golden model core_pairs=4 cir_pairs=3");
        $finish;
    end
endmodule
