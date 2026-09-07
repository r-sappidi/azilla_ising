// Experimental register-buffered single-fetch/two-destination replay.
// No interaction arithmetic, multicast, or dual-port SRAM is assumed.
module shared_fetch_replay #(
    parameter int BLOCK_W = 16
) (
    input logic clk, rst,
    input logic in_cmd_valid,
    output logic in_cmd_ready,
    input logic [BLOCK_W-1:0] in_a, in_b,
    input logic in_weight_valid,
    output logic in_weight_ready,
    input logic [255:0] in_weight_data,
    output logic out_cmd_valid,
    input logic out_cmd_ready,
    output logic [BLOCK_W-1:0] out_a, out_b,
    output logic out_weight_valid,
    input logic out_weight_ready,
    output logic [255:0] out_weight_data
);
    typedef enum logic [1:0] {IDLE, FILL, COMMAND, SEND} state_t;
    state_t state;
    logic [BLOCK_W-1:0] a, b;
    logic [255:0] rows [0:31];
    logic [4:0] row;
    logic direction;
    assign in_cmd_ready = state == IDLE;
    assign in_weight_ready = state == FILL;
    assign out_cmd_valid = state == COMMAND;
    assign out_weight_valid = state == SEND;
    assign out_a = direction ? b : a;
    assign out_b = direction ? a : b;
    assign out_weight_data = rows[row];
    always_ff @(posedge clk) begin
        if (rst) begin
            state <= IDLE; row <= 0; direction <= 0; a <= 0; b <= 0;
        end else case (state)
            IDLE: if (in_cmd_valid) begin
                a <= in_a; b <= in_b; row <= 0; direction <= 0; state <= FILL;
            end
            FILL: if (in_weight_valid) begin
                rows[row] <= in_weight_data;
                if (row == 31) begin row <= 0; state <= COMMAND; end
                else row <= row + 1'b1;
            end
            COMMAND: if (out_cmd_ready) state <= SEND;
            SEND: if (out_weight_ready) begin
                if (row != 31) row <= row + 1'b1;
                else if (!direction) begin
                    direction <= 1; row <= 0; state <= COMMAND;
                end else begin state <= IDLE; row <= 0; end
            end
        endcase
    end
endmodule
