import ising_pkg::*;

// Fused bidirectional evaluation of one symmetric 32x32 interaction block.
//
// One row of J_ab is consumed per cycle. The row completes one element of
// J_ab*x_b while simultaneously contributing one term to every element of
// transpose(J_ab)*x_a. Since spin bits encode +/-1, both operations use signed
// add/subtract rather than general multipliers.
module symmetric_mvm (
    input  logic                                      clk,
    input  logic                                      rst,
    input  logic                                      start,
    input  logic [SPIN_COUNT-1:0]                    state_a,
    input  logic [SPIN_COUNT-1:0]                    state_b,

    output logic [((SPIN_COUNT > 1) ? $clog2(SPIN_COUNT) : 1)-1:0]
                                                     weight_row_o,
    input  logic [SPIN_COUNT*WEIGHT_W-1:0]           weight_data_i,

    output logic signed [ACC_W-1:0]                  result_a [0:SPIN_COUNT-1],
    output logic signed [ACC_W-1:0]                  result_b [0:SPIN_COUNT-1],
    output logic                                      done
);
    localparam int ROW_W = (SPIN_COUNT > 1) ? $clog2(SPIN_COUNT) : 1;

    logic active;
    logic [ROW_W-1:0] process_row;
    logic [ROW_W-1:0] request_row;
    logic read_valid;
    logic signed [ACC_W-1:0] row_dot_product;

    // The SRAM response arrives one cycle after this address is presented.
    assign weight_row_o = request_row;

    // Complete the normal-direction dot product for the current row.
    always_comb begin
        row_dot_product = '0;
        for (int column = 0; column < SPIN_COUNT; column++) begin
            if (state_b[column])
                row_dot_product = row_dot_product +
                    ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
            else
                row_dot_product = row_dot_product -
                    ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            active <= 1'b0;
            process_row <= '0;
            request_row <= '0;
            read_valid <= 1'b0;
            done <= 1'b0;
            for (int index = 0; index < SPIN_COUNT; index++) begin
                result_a[index] <= '0;
                result_b[index] <= '0;
            end
        end
        else begin
            done <= 1'b0;

            if (start && !active) begin
                active <= 1'b1;
                process_row <= '0;
                // Row zero is requested during this start cycle. Queue row
                // one for the cycle in which row zero is consumed.
                request_row <= ROW_W'(1);
                read_valid <= 1'b1;
                for (int index = 0; index < SPIN_COUNT; index++) begin
                    result_a[index] <= '0;
                    result_b[index] <= '0;
                end
            end
            else if (active && read_valid) begin
                // This row is one complete output dot product for J_ab*x_b.
                result_a[process_row] <= row_dot_product;

                // The same row contributes one signed term to every output of
                // transpose(J_ab)*x_a.
                for (int column = 0; column < SPIN_COUNT; column++) begin
                    if (state_a[process_row])
                        result_b[column] <= result_b[column] +
                            ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
                    else
                        result_b[column] <= result_b[column] -
                            ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
                end

                if (process_row == ROW_W'(SPIN_COUNT-1)) begin
                    active <= 1'b0;
                    process_row <= '0;
                    request_row <= '0;
                    read_valid <= 1'b0;
                    done <= 1'b1;
                end
                else begin
                    process_row <= process_row + 1'b1;
                    if (request_row != ROW_W'(SPIN_COUNT-1))
                        request_row <= request_row + 1'b1;
                end
            end
        end
    end
endmodule
