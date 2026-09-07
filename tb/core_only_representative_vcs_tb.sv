`timescale 1ns/1ps
import ising_pkg::*;

// Representative integrated no-CIR validation: two destination-stationary
// spin endpoints exchange frozen state through the real FlooNoC routers, then
// execute the two directed forms of one symmetric interaction block.  The
// test checks routing/backpressure, arithmetic, framing, and core counters.
module core_only_representative_vcs_tb;
    localparam int LOCAL=0, NORTH=1, SOUTH=2, EAST=3, WEST=4;
    localparam int NODES=2;
    logic clk=0, rst=1;
    always #5 clk=~clk;

    logic [NODES-1:0][4:0] in_valid,in_ready,out_valid,out_ready;
    logic [NODES-1:0][4:0][DATA_W-1:0] in_data,out_data;
    logic [NODES-1:0][4:0][1:0] in_type,out_type;
    logic [NODES-1:0][4:0][0:0] in_dx,in_dy,out_dx,out_dy;
    logic [NODES-1:0][4:0][7:0] in_source,out_source;
    logic [NODES-1:0][4:0][15:0] in_epoch,out_epoch;
    logic [NODES-1:0][4:0][1:0] in_block,out_block;
    logic [NODES-1:0][4:0] in_last,out_last;
    logic [NODES-1:0] inject_valid;
    logic [NODES-1:0][DATA_W-1:0] inject_data;
    logic [NODES-1:0][1:0] inject_type,inject_block;
    logic [NODES-1:0][0:0] inject_dx,inject_dy;
    logic [NODES-1:0][7:0] inject_source;
    logic [NODES-1:0][15:0] inject_epoch;
    logic [NODES-1:0] inject_last;

    logic [NODES-1:0] job_valid,job_ready,weight_valid,weight_ready;
    logic [NODES-1:0][1:0] source_id,result_source_id;
    logic [NODES-1:0][SPIN_COUNT-1:0] source_state;
    logic [NODES-1:0][DATA_W-1:0] weight_data;
    logic [NODES-1:0] result_valid,result_ready;
    logic signed [ACC_W-1:0] result[0:NODES-1][0:SPIN_COUNT-1];
    logic [63:0] jobs[0:NODES-1],weight_bytes[0:NODES-1];
    logic [63:0] state_bytes[0:NODES-1],remote_bytes[0:NODES-1];
    logic [63:0] first_cycle[0:NODES-1],last_cycle[0:NODES-1];
    logic [NODES-1:0][SPIN_COUNT-1:0] states,received_state;
    integer cycle_count=0, errors=0, stall_cycles=0, seed=1;
    integer injected=0,ejected=0,link_flits=0,blocked_ejection=0;

    function automatic logic signed [WEIGHT_W-1:0] weight(
        input int dst,input int src,input int row,input int col);
        int lo,hi,rr,cc,v;
        lo=dst<src?dst:src; hi=dst<src?src:dst;
        rr=dst<src?row:col; cc=dst<src?col:row;
        v=((lo*17+hi*13+rr*5+cc*11+seed*3)%15)-7;
        return WEIGHT_W'(v);
    endfunction

    function automatic logic signed [ACC_W-1:0] golden(
        input int dst,input int row);
        logic signed [ACC_W-1:0] sum;
        int src;
        src=1-dst; sum='0;
        for(int col=0;col<SPIN_COUNT;col++)
            sum += states[src][col] ? weight(dst,src,row,col)
                                    : -weight(dst,src,row,col);
        return sum;
    endfunction

    generate for(genvar n=0;n<NODES;n++) begin: gen_nodes
        azilla_floo_router #(.X_W(1),.Y_W(1),.SOURCE_ID_W(8),
            .EPOCH_W(16),.GLOBAL_BLOCK_ID_W(2),.FIFO_DEPTH(4),
            .ROUTER_X(n),.ROUTER_Y(0)) router(
            .clk,.rst,.in_valid_i(in_valid[n]),.in_ready_o(in_ready[n]),
            .in_data_i(in_data[n]),.in_type_i(in_type[n]),
            .in_dest_x_i(in_dx[n]),.in_dest_y_i(in_dy[n]),
            .in_source_id_i(in_source[n]),.in_epoch_i(in_epoch[n]),
            .in_block_id_i(in_block[n]),.in_last_i(in_last[n]),
            .out_valid_o(out_valid[n]),.out_ready_i(out_ready[n]),
            .out_data_o(out_data[n]),.out_type_o(out_type[n]),
            .out_dest_x_o(out_dx[n]),.out_dest_y_o(out_dy[n]),
            .out_source_id_o(out_source[n]),.out_epoch_o(out_epoch[n]),
            .out_block_id_o(out_block[n]),.out_last_o(out_last[n]));
        core_local_mvm_engine #(.GLOBAL_BLOCK_ID_W(2)) engine(
            .clk,.rst,.job_valid_i(job_valid[n]),.job_ready_o(job_ready[n]),
            .source_block_id_i(source_id[n]),.source_state_i(source_state[n]),
            .source_remote_i(1'b1),.weight_valid_i(weight_valid[n]),
            .weight_ready_o(weight_ready[n]),.weight_data_i(weight_data[n]),
            .result_valid_o(result_valid[n]),.result_ready_i(result_ready[n]),
            .result_o(result[n]),.result_source_block_id_o(result_source_id[n]),
            .jobs_completed_o(jobs[n]),.weight_bytes_o(weight_bytes[n]),
            .source_state_bytes_o(state_bytes[n]),
            .remote_source_state_bytes_o(remote_bytes[n]),
            .first_job_cycle_o(first_cycle[n]),.last_job_cycle_o(last_cycle[n]));
    end endgenerate

    // Join the only physical link. All other cardinal ports are boundaries.
    always_comb begin
        in_valid='0;in_data='0;in_type='0;in_dx='0;in_dy='0;
        in_source='0;in_epoch='0;in_block='0;in_last='1;
        weight_valid='0; weight_data='0;
        for(int n=0;n<NODES;n++) begin
            out_ready[n]='1;
            in_valid[n][LOCAL]=inject_valid[n];in_data[n][LOCAL]=inject_data[n];
            in_type[n][LOCAL]=inject_type[n];in_dx[n][LOCAL]=inject_dx[n];
            in_dy[n][LOCAL]=inject_dy[n];in_source[n][LOCAL]=inject_source[n];
            in_epoch[n][LOCAL]=inject_epoch[n];in_block[n][LOCAL]=inject_block[n];
            in_last[n][LOCAL]=inject_last[n];
            if(out_valid[n][LOCAL] && out_type[n][LOCAL]==2'd3) begin
                weight_valid[n]=1'b1;
                weight_data[n]=out_data[n][LOCAL];
                out_ready[n][LOCAL]=weight_ready[n];
            end
        end
        in_valid[0][EAST]=out_valid[1][WEST];
        in_data[0][EAST]=out_data[1][WEST];in_type[0][EAST]=out_type[1][WEST];
        in_dx[0][EAST]=out_dx[1][WEST];in_dy[0][EAST]=out_dy[1][WEST];
        in_source[0][EAST]=out_source[1][WEST];in_epoch[0][EAST]=out_epoch[1][WEST];
        in_block[0][EAST]=out_block[1][WEST];in_last[0][EAST]=out_last[1][WEST];
        out_ready[1][WEST]=in_ready[0][EAST];
        in_valid[1][WEST]=out_valid[0][EAST];
        in_data[1][WEST]=out_data[0][EAST];in_type[1][WEST]=out_type[0][EAST];
        in_dx[1][WEST]=out_dx[0][EAST];in_dy[1][WEST]=out_dy[0][EAST];
        in_source[1][WEST]=out_source[0][EAST];in_epoch[1][WEST]=out_epoch[0][EAST];
        in_block[1][WEST]=out_block[0][EAST];in_last[1][WEST]=out_last[0][EAST];
        out_ready[0][EAST]=in_ready[1][WEST];
        if(cycle_count < stall_cycles) begin
            out_ready[0][LOCAL]=0; out_ready[1][LOCAL]=0;
        end
    end

    always_ff @(posedge clk) begin
      if(rst) begin
        cycle_count<=0;received_state<='0;injected<=0;ejected<=0;
        link_flits<=0;blocked_ejection<=0;
      end else begin
        cycle_count <= cycle_count+1;
        for(int n=0;n<NODES;n++) begin
            if(out_valid[n][LOCAL]&&out_ready[n][LOCAL] &&
               out_type[n][LOCAL]==2'd0) begin
                received_state[n] <= out_data[n][LOCAL][SPIN_COUNT-1:0];
                $display("CORE_REP_EVENT cycle=%0d event=eject node=%0d block=%0d",
                         cycle_count,n,out_block[n][LOCAL]);
            end
        end
        injected <= injected +
            (in_valid[0][LOCAL]&&in_ready[0][LOCAL]) +
            (in_valid[1][LOCAL]&&in_ready[1][LOCAL]);
        ejected <= ejected +
            (out_valid[0][LOCAL]&&out_ready[0][LOCAL]) +
            (out_valid[1][LOCAL]&&out_ready[1][LOCAL]);
        blocked_ejection <= blocked_ejection +
            (out_valid[0][LOCAL]&&!out_ready[0][LOCAL]) +
            (out_valid[1][LOCAL]&&!out_ready[1][LOCAL]);
        link_flits <= link_flits +
            (out_valid[0][EAST]&&out_ready[0][EAST]) +
            (out_valid[1][WEST]&&out_ready[1][WEST]);
      end
    end

    // The cross-H1 memory owner is node zero. Stream one canonical 1-KiB
    // block copy through the real router to each destination core. The second
    // endpoint consumes the transposed row order locally, as required for the
    // reverse directed product of a symmetric interaction block.
    task automatic send_weight_packet(input int destination);
        for(int row=0;row<SPIN_COUNT;row++) begin
            inject_valid[0]=1'b1; inject_type[0]=2'd3;
            inject_dx[0]=destination; inject_dy[0]=0;
            inject_source[0]=0; inject_epoch[0]=1;
            inject_block[0]=destination; inject_last[0]=(row==SPIN_COUNT-1);
            for(int col=0;col<SPIN_COUNT;col++)
                inject_data[0][col*WEIGHT_W+:WEIGHT_W]=
                    weight(destination,1-destination,row,col);
            do @(posedge clk); while(!in_ready[0][LOCAL]);
            @(negedge clk);
        end
        inject_valid[0]=1'b0; inject_data[0]='0; inject_last[0]=1'b1;
    endtask

    initial begin
        void'($value$plusargs("STALL_CYCLES=%d",stall_cycles));
        void'($value$plusargs("SEED=%d",seed));
        inject_valid='0;inject_data='0;inject_type='0;inject_dx='0;inject_dy='0;
        inject_source='0;inject_epoch='0;inject_block='0;inject_last='1;
        job_valid='0;
        result_ready='0;source_id='0;source_state='0;
        for(int n=0;n<NODES;n++) for(int s=0;s<SPIN_COUNT;s++)
            states[n][s]=((n*7+s*3+seed)%5)<2;
        repeat(3) @(posedge clk); @(negedge clk); rst=0;

        // Publish one frozen-state flit in both directions concurrently.
        for(int n=0;n<NODES;n++) begin
            inject_valid[n]=1;inject_data[n]=states[n];inject_type[n]=0;
            inject_dx[n]=1-n;inject_dy[n]=0;inject_source[n]=n;
            inject_epoch[n]=1;inject_block[n]=n;inject_last[n]=1;
        end
        do @(posedge clk); while(!(in_ready[0][LOCAL]&&in_ready[1][LOCAL]));
        @(negedge clk);inject_valid='0;
        wait(ejected==2); @(negedge clk);
        for(int n=0;n<NODES;n++) begin
            if(received_state[n]!==states[1-n]) begin
                $error("remote state mismatch node=%0d",n);errors++;
            end
            source_id[n]=1-n;source_state[n]=received_state[n];job_valid[n]=1;
        end
        do @(posedge clk); while(!(job_ready[0]&&job_ready[1]));
        @(negedge clk);job_valid='0;
        // Both directed jobs read the same canonical cross-H1 memory owner;
        // the complete weight block, rather than a computed partial, travels
        // to each endpoint core.
        send_weight_packet(0);
        send_weight_packet(1);
        wait(result_valid==2'b11);@(negedge clk);
        for(int n=0;n<NODES;n++) begin
            for(int row=0;row<SPIN_COUNT;row++) if(result[n][row]!==golden(n,row)) begin
                $error("result mismatch node=%0d row=%0d rtl=%0d golden=%0d",
                       n,row,result[n][row],golden(n,row));errors++;
            end
            if(result_source_id[n]!=(1-n)) begin $error("source id mismatch");errors++;end
        end
        result_ready='1;@(posedge clk);@(negedge clk);result_ready='0;
        for(int n=0;n<NODES;n++) if(jobs[n]!=1||weight_bytes[n]!=1024||
            state_bytes[n]!=4||remote_bytes[n]!=4) begin
            $error("counter mismatch node=%0d",n);errors++;
        end
        if(injected!=66||ejected!=66||link_flits!=34) begin
            $error("traffic mismatch injected=%0d ejected=%0d links=%0d",
                   injected,ejected,link_flits);errors++;
        end
        $display("CORE_REP_SUMMARY seed=%0d stall_cycles=%0d cycles=%0d injected=%0d ejected=%0d link_flits=%0d blocked_ejection=%0d first0=%0d last0=%0d",
                 seed,stall_cycles,cycle_count,injected,ejected,link_flits,
                 blocked_ejection,first_cycle[0],last_cycle[0]);
        if(errors) $fatal(1,"cores-only representative failure errors=%0d",errors);
        $display("PASS: cores-only representative VCS RTL real-router arithmetic");
        $finish;
    end
endmodule
