`timescale 1ns/1ps
import ising_pkg::*;

// Representative arithmetic test for the destination-stationary no-CIR
// baseline: eight 32-spin cores (256 spins), one local directed engine/core,
// and every dense off-diagonal interaction evaluated at its destination.
module core_local_baseline_256_tb;
    localparam int CORES = 8;
    logic clk = 0, rst = 1;
    logic [CORES-1:0] job_valid, job_ready;
    logic [CORES-1:0][7:0] source_id;
    logic [CORES-1:0][SPIN_COUNT-1:0] source_state;
    logic [CORES-1:0] source_remote;
    logic [CORES-1:0] weight_valid, weight_ready;
    logic [CORES-1:0][DATA_W-1:0] weight_data;
    logic [CORES-1:0] result_valid, result_ready;
    logic signed [ACC_W-1:0] result [0:CORES-1][0:SPIN_COUNT-1];
    logic [CORES-1:0][7:0] result_source_id;
    logic [63:0] jobs [0:CORES-1], weight_bytes [0:CORES-1];
    logic [63:0] state_bytes [0:CORES-1], remote_bytes [0:CORES-1];
    logic [63:0] first_cycle [0:CORES-1], last_cycle [0:CORES-1];
    logic [CORES-1:0][SPIN_COUNT-1:0] states;
    logic signed [ACC_W-1:0] accumulated [0:CORES-1][0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] golden [0:CORES-1][0:SPIN_COUNT-1];
    int errors = 0;
    always #5 clk = ~clk;

    function automatic logic signed [WEIGHT_W-1:0] weight(
        input int dst, input int src, input int row, input int col);
        int lo, hi, rr, cc, v;
        lo = dst < src ? dst : src; hi = dst < src ? src : dst;
        rr = dst < src ? row : col; cc = dst < src ? col : row;
        v = ((lo*17 + hi*13 + rr*5 + cc*11) % 15) - 7;
        return WEIGHT_W'(v);
    endfunction

    task automatic drive_job(input int dst, input int src);
        logic [DATA_W-1:0] row_word;
        job_valid[dst] = 1; source_id[dst] = src;
        source_state[dst] = states[src]; source_remote[dst] = 1;
        do @(posedge clk); while (!job_ready[dst]);
        @(negedge clk); job_valid[dst] = 0;
        for (int row = 0; row < SPIN_COUNT; row++) begin
            row_word = '0;
            for (int col = 0; col < SPIN_COUNT; col++)
                row_word[col*WEIGHT_W +: WEIGHT_W] = weight(dst,src,row,col);
            weight_data[dst] = row_word; weight_valid[dst] = 1;
            do @(posedge clk); while (!weight_ready[dst]);
            @(negedge clk);
        end
        weight_valid[dst] = 0;
        do @(posedge clk); while (!result_valid[dst]);
        @(negedge clk);
        for (int row = 0; row < SPIN_COUNT; row++)
            accumulated[dst][row] += result[dst][row];
        result_ready[dst] = 1;
        @(posedge clk); @(negedge clk); result_ready[dst] = 0;
    endtask

    generate for (genvar c=0; c<CORES; c++) begin : engines
        core_local_mvm_engine #(.GLOBAL_BLOCK_ID_W(8)) dut (
            .clk,.rst,.job_valid_i(job_valid[c]),.job_ready_o(job_ready[c]),
            .source_block_id_i(source_id[c]),.source_state_i(source_state[c]),
            .source_remote_i(source_remote[c]),.weight_valid_i(weight_valid[c]),
            .weight_ready_o(weight_ready[c]),.weight_data_i(weight_data[c]),
            .result_valid_o(result_valid[c]),.result_ready_i(result_ready[c]),
            .result_o(result[c]),.result_source_block_id_o(result_source_id[c]),
            .jobs_completed_o(jobs[c]),.weight_bytes_o(weight_bytes[c]),
            .source_state_bytes_o(state_bytes[c]),
            .remote_source_state_bytes_o(remote_bytes[c]),
            .first_job_cycle_o(first_cycle[c]),.last_job_cycle_o(last_cycle[c])
        );
    end endgenerate

    initial begin
        job_valid='0; weight_valid='0; result_ready='0; source_remote='0;
        weight_data='0; source_id='0; source_state='0;
        accumulated='{default:'0}; golden='{default:'0};
        for (int c=0;c<CORES;c++)
            for (int s=0;s<SPIN_COUNT;s++) states[c][s]=((c*7+s*3)%5)<2;
        repeat(3) @(posedge clk); @(negedge clk); rst=0;

        // Independent destination streams execute concurrently.
        fork
            begin for(int s=0;s<CORES;s++) if(s!=0) drive_job(0,s); end
            begin for(int s=0;s<CORES;s++) if(s!=1) drive_job(1,s); end
            begin for(int s=0;s<CORES;s++) if(s!=2) drive_job(2,s); end
            begin for(int s=0;s<CORES;s++) if(s!=3) drive_job(3,s); end
            begin for(int s=0;s<CORES;s++) if(s!=4) drive_job(4,s); end
            begin for(int s=0;s<CORES;s++) if(s!=5) drive_job(5,s); end
            begin for(int s=0;s<CORES;s++) if(s!=6) drive_job(6,s); end
            begin for(int s=0;s<CORES;s++) if(s!=7) drive_job(7,s); end
        join

        for (int d=0;d<CORES;d++) for (int s=0;s<CORES;s++) if(s!=d)
            for (int r=0;r<SPIN_COUNT;r++) for(int c=0;c<SPIN_COUNT;c++)
                golden[d][r] += states[s][c] ? weight(d,s,r,c) : -weight(d,s,r,c);
        for(int d=0;d<CORES;d++) begin
            for(int r=0;r<SPIN_COUNT;r++) if(accumulated[d][r]!==golden[d][r]) begin
                $error("dst=%0d spin=%0d rtl=%0d golden=%0d",d,r,accumulated[d][r],golden[d][r]); errors++;
            end
            if(jobs[d]!=CORES-1 || weight_bytes[d]!=(CORES-1)*1024 ||
               state_bytes[d]!=(CORES-1)*4 || remote_bytes[d]!=(CORES-1)*4) begin
                $error("counter mismatch core=%0d jobs=%0d weights=%0d state=%0d remote=%0d",d,jobs[d],weight_bytes[d],state_bytes[d],remote_bytes[d]); errors++;
            end
            $display("CORE_LOCAL_METRICS core=%0d jobs=%0d weight_bytes=%0d remote_state_bytes=%0d first=%0d last=%0d",d,jobs[d],weight_bytes[d],remote_bytes[d],first_cycle[d],last_cycle[d]);
        end
        if(errors==0) $display("PASS: 256-spin destination-stationary no-CIR arithmetic and counters match golden model");
        else $fatal(1,"FAIL: %0d errors",errors);
        $finish;
    end
endmodule
