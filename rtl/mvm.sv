import ising_pkg::*;

module mvm (
    input logic clk, rst,
    input logic start,
    output logic [((SPIN_COUNT > 1) ? $clog2(SPIN_COUNT) : 1)-1:0]
                                                     weight_row_o,
    input logic [WEIGHT_W*SPIN_COUNT-1:0]           weight_data_i,
    output logic signed [ACC_W-1:0] result [0:SPIN_COUNT-1],
    input logic [SPIN_COUNT-1:0] state,
    output logic done
);
    localparam int ROW_W = (SPIN_COUNT > 1) ? $clog2(SPIN_COUNT) : 1;

    logic active;
    logic [ROW_W-1:0] process_row;
    logic [ROW_W-1:0] request_row;
    logic read_valid;
    logic signed [ACC_W-1:0] row_dot_product;

    assign weight_row_o = request_row;

    always_comb begin
        row_dot_product = '0;
        for (int column = 0; column < SPIN_COUNT; column++) begin
            if (state[column])
                row_dot_product = row_dot_product +
                    ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
            else
                row_dot_product = row_dot_product -
                    ACC_W'($signed(weight_data_i[column*WEIGHT_W +: WEIGHT_W]));
        end
    end

    always_ff @(posedge clk) begin 
        if (rst) begin
            active<=1'b0;
            process_row<='0;
            request_row<='0;
            read_valid<=1'b0;
            result<='{default: '0};
            done<=1'b0;
        end
        else if(start&&~active) begin
            active<=1'b1;
            done<=1'b0;
            result<='{default: '0};
            process_row<='0;
            request_row<=ROW_W'(1);
            read_valid<=1'b1;
        end
        else if (active && read_valid) begin
            result[process_row] <= row_dot_product;

            if(process_row==ROW_W'(SPIN_COUNT-1)) begin
                process_row<='0;
                request_row<='0;
                read_valid<=1'b0;
                active<=1'b0;
                done<=1'b1;
            end
            else begin
                process_row<=process_row+1'b1;
                if(request_row!=ROW_W'(SPIN_COUNT-1))
                    request_row<=request_row+1'b1;
            end
        end
    end
endmodule
