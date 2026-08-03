module h0_node#(
    parameter MVM_COUNT=16,
)(
    input logic clk, rst,
    input logic init_start, iter_start,
    input logic weight_init_valid[MVM_COUNT-1:0],
    output logic weight_init_ready[MVM_COUNT-1:0],
    input logic [DATA_W-1:0] weight_init_data [MVM_COUNT-1:0],
    
)
    typedef enum {
        NODE_RESET,
        NODE_INIT,
        NODE_IDLE,
        NODE_ITER,
        NODE_DONE
    } node_state_t ;
    node_state_t node_state, node_state_n;
    

    module mvm (
    input logic clk, rst,
    input logic start,
    input logic [WEIGHT_W*SPIN_COUNT-1:0] weight_mem [0:SPIN_COUNT-1],
    output logic signed [ACC_W-1:0] result [0:SPIN_COUNT-1],
    input logic [SPIN_COUNT-1:0] state,
    output logic done
);

    genvar i;
    generate
        for(i=0;i<MVM_COUNT;i++) begin
            mvm h0_mvm(
                .clk,
                .rst,
                .start
            )
        end
    endgenerate
    always_comb begin 
        unique case (node_state)
            NODE_RESET: begin
                
            end
            NODE_INIT: begin
                
            end
            NODE_IDLE: begin
                
            end
            NODE_ITER: begin
                
            end
            NODE_DONE: begin
                node_state_n=node_state;
            end
            default: 
        endcase
    end
endmodule;
