import ising_pkg::*;

// Row-serial matrix-vector multiply for a core-resident diagonal J block.
//
// The state vector contains binary +/-1 spins, so multiplying a signed-int8
// weight by a spin reduces to adding or subtracting that weight. The connected
// SRAM has a one-cycle read latency: weight_row_o requests the next row while
// weight_data_i supplies the previously requested row.
module mvm (
    input  logic                                     clk,
    input  logic                                     rst,
    input  logic                                     start,
    output logic [((SPIN_COUNT > 1) ? $clog2(SPIN_COUNT) : 1)-1:0]
                                                     weight_row_o,
    input  logic [WEIGHT_W*SPIN_COUNT-1:0]           weight_data_i,
    output logic signed [ACC_W-1:0] result [0:SPIN_COUNT-1],
    input  logic [SPIN_COUNT-1:0]                    state,
    output logic                                     done
);
    localparam int ROW_W = (SPIN_COUNT > 1) ? $clog2(SPIN_COUNT) : 1;

    logic                    active;
    logic [ROW_W-1:0]        process_row;
    logic [ROW_W-1:0]        request_row;
    logic                    read_valid;
    logic signed [ACC_W-1:0] row_dot_product;

    // The read address and returned row are deliberately tracked separately.
    assign weight_row_o = request_row;

    // Combinational dot product for the row returned by the resident SRAM.
    always_comb begin
        row_dot_product = '0;
`ifndef AZILLA_TIMING_ONLY
        for (int column = 0; column < SPIN_COUNT; column++) begin
            if (state[column])
                row_dot_product = row_dot_product +
                    ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
            else
                row_dot_product = row_dot_product -
                    ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
        end
`endif
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            active      <= 1'b0;
            process_row <= '0;
            request_row <= '0;
            read_valid  <= 1'b0;
            result      <= '{default: '0};
            done        <= 1'b0;
        end
        else if (start && !active) begin
            active      <= 1'b1;
            done        <= 1'b0;
            result      <= '{default: '0};
            process_row <= '0;
            // Row zero is requested during the start cycle. Queue row one
            // while the SRAM returns row zero on the following cycle.
            request_row <= ROW_W'(1);
            read_valid  <= 1'b1;
        end
        else if (active && read_valid) begin
            result[process_row] <= row_dot_product;

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
