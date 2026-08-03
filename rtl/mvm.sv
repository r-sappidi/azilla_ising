import ising_pkg::*;

module mvm (
    input logic clk, rst,
    input logic start,
    input logic [WEIGHT_W*SPIN_COUNT-1:0] weight_mem [0:SPIN_COUNT-1],
    output logic signed [ACC_W-1:0] result [0:SPIN_COUNT-1],
    input logic [SPIN_COUNT-1:0] state,
    output logic done
);
    logic active;
    logic [$clog2(SPIN_COUNT)-1:0]spin_idx;
    always_ff @(posedge clk) begin 
        if (rst) begin
            active<=1'b0;
            spin_idx<='0;
            result<='{default: '0};
            done<=1'b0;
        end
        else if(start&&~active) begin
            active<=1'b1;
            done<=1'b0;
            result<='{default: '0};
            spin_idx<='0;
        end
        else if (active) begin
            for(int i=0;i<SPIN_COUNT;i+=1)begin
                logic[WEIGHT_W-1:0] weight;
                logic signed[WEIGHT_W:0] contribution;
                weight =
                    weight_mem[i][spin_idx*WEIGHT_W +: WEIGHT_W];
                contribution={weight[WEIGHT_W-1], weight};
                if (state[spin_idx])
                    result[i] <= result[i] + contribution;
                else
                    result[i] <= result[i] - contribution;
            end
            if(spin_idx<SPIN_COUNT-1) spin_idx<=spin_idx+1;
            else begin
                spin_idx<='0;
                active<=1'b0;
                done<=1'b1;
            end
        end
    end
endmodule
