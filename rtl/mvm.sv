import ising_pkg::*;

// Row-serial matrix-vector multiply for a core-resident diagonal J block.
//
// The state vector contains binary +/-1 spins, so multiplying a signed-int8
// weight by a spin reduces to adding or subtracting that weight. The connected
// SRAM has a one-cycle read latency: weight_row_o requests the next row while
// weight_data_i supplies the previously requested row.
module mvm #(
    parameter bit SUPPORT_TRANSPOSE = 1'b0
) (
    input  logic                                     clk,
    input  logic                                     rst,
    input  logic                                     start,
    output logic [((SPIN_COUNT > 1) ? $clog2(SPIN_COUNT) : 1)-1:0]
                                                     weight_row_o,
    input  logic [WEIGHT_W*SPIN_COUNT-1:0]           weight_data_i,
    output logic signed [ACC_W-1:0] result [0:SPIN_COUNT-1],
    input  logic [SPIN_COUNT-1:0]                    state,
    input  logic                                     transpose_i,
    output logic                                     done
);
    localparam int ROW_W = (SPIN_COUNT > 1) ? $clog2(SPIN_COUNT) : 1;

    logic                    active;
    logic [ROW_W-1:0]        process_row;
    logic [ROW_W-1:0]        request_row;
    logic                    read_valid;
    logic signed [ACC_W-1:0] row_dot_product;
    logic transpose_mode;

    // The read address and returned row are deliberately tracked separately.
    assign weight_row_o = request_row;

    // Balanced reduction: signed int8 times +/-1 needs 9 bits (including +128).
    // 32 such terms fit exactly in 14 signed bits. No register/latency changes.
    localparam int TREE_LEVELS = $clog2(SPIN_COUNT);
    localparam int TREE_LEAVES = 1 << TREE_LEVELS;
    localparam int SUM_W = WEIGHT_W + 1 + TREE_LEVELS;
    wire signed [SUM_W-1:0] sum_tree [1:2*TREE_LEAVES-1];
    generate
        for (genvar leaf = 0; leaf < TREE_LEAVES; leaf++) begin : gen_terms
            if (leaf < SPIN_COUNT) begin
                wire signed [WEIGHT_W:0] extended_weight =
                    {weight_data_i[leaf*WEIGHT_W+WEIGHT_W-1],
                     weight_data_i[leaf*WEIGHT_W +: WEIGHT_W]};
                wire signed [WEIGHT_W:0] signed_term =
                    state[leaf] ? extended_weight : -extended_weight;
                assign sum_tree[TREE_LEAVES+leaf] = signed_term;
            end else begin
                assign sum_tree[TREE_LEAVES+leaf] = '0;
            end
        end
        for (genvar branch = 1; branch < TREE_LEAVES; branch++) begin : gen_sums
            assign sum_tree[branch] = sum_tree[2*branch] + sum_tree[2*branch+1];
        end
    endgenerate
`ifdef AZILLA_TIMING_ONLY
    assign row_dot_product = '0;
`else
    assign row_dot_product = ACC_W'($signed(sum_tree[1]));
`endif

    always_ff @(posedge clk) begin
        if (rst) begin
            active      <= 1'b0;
            process_row <= '0;
            request_row <= '0;
            read_valid  <= 1'b0;
            result      <= '{default: '0};
            done        <= 1'b0;
            transpose_mode <= 1'b0;
        end
        else if (start && !active) begin
            active      <= 1'b1;
            done        <= 1'b0;
            transpose_mode <= SUPPORT_TRANSPOSE && transpose_i;
            result      <= '{default: '0};
            process_row <= '0;
            // Row zero is requested during the start cycle. Queue row one
            // while the SRAM returns row zero on the following cycle.
            request_row <= ROW_W'(1);
            read_valid  <= 1'b1;
        end
        else if (active && read_valid) begin
            if (SUPPORT_TRANSPOSE && transpose_mode) begin
                // J^T*x consumes the same canonical row-major block. Each
                // row contributes x[row] times that row to all output lanes.
                // No transposed DRAM copy or hidden SRAM transpose is needed.
                for (int column = 0; column < SPIN_COUNT; column++) begin
`ifndef AZILLA_TIMING_ONLY
                    if (state[process_row])
                        result[column] <= result[column] +
                            ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
                    else
                        result[column] <= result[column] -
                            ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
`else
                    result[column] <= '0;
`endif
                end
            end else begin
                result[process_row] <= row_dot_product;
            end

            if (process_row == ROW_W'(SPIN_COUNT-1)) begin
                process_row <= '0;
                request_row <= '0;
                read_valid  <= 1'b0;
                active      <= 1'b0;
                done        <= 1'b1;
            end
            else begin
                process_row <= process_row + 1'b1;
                if (request_row != ROW_W'(SPIN_COUNT-1))
                    request_row <= request_row + 1'b1;
            end
        end
    end
endmodule
