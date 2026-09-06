// Interleaved state storage with one synchronous read port per bank.
//
// Request lane priority is fixed within each bank. A requester must keep its
// valid/index stable until ready is observed. Responses return on the same
// lane one cycle after an accepted request. STATE_BANK_COUNT must be a power
// of two so bank and row selection are simple address slices.
module banked_state_sram #(
    parameter int STATE_ENTRY_COUNT = 32,
    parameter int STATE_W           = 32,
    parameter int STATE_BANK_COUNT  = 8,
    parameter int REQUEST_COUNT     = 2
) (
    input  logic clk,
    input  logic rst,

    input  logic write_valid_i,
    input  logic [((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                 write_index_i,
    input  logic [STATE_W-1:0] write_data_i,

    input  logic [REQUEST_COUNT-1:0] request_valid_i,
    output logic [REQUEST_COUNT-1:0] request_ready_o,
    input  logic [REQUEST_COUNT-1:0]
                 [((STATE_ENTRY_COUNT > 1) ? $clog2(STATE_ENTRY_COUNT) : 1)-1:0]
                 request_index_i,

    output logic [REQUEST_COUNT-1:0] response_valid_o,
    output logic [REQUEST_COUNT-1:0][STATE_W-1:0] response_data_o
);
    localparam int BANK_DEPTH =
        (STATE_ENTRY_COUNT + STATE_BANK_COUNT - 1) / STATE_BANK_COUNT;
    localparam int ROW_W = (BANK_DEPTH > 1) ? $clog2(BANK_DEPTH) : 1;
    localparam int REQUEST_W =
        (REQUEST_COUNT > 1) ? $clog2(REQUEST_COUNT) : 1;

    logic [STATE_BANK_COUNT-1:0] bank_request_valid;
    logic [STATE_BANK_COUNT-1:0][ROW_W-1:0] bank_request_row;
    logic [STATE_BANK_COUNT-1:0][REQUEST_W-1:0] bank_request_lane;
    logic [STATE_BANK_COUNT-1:0] bank_response_valid;
    logic [STATE_BANK_COUNT-1:0][REQUEST_W-1:0] bank_response_lane;
    logic [STATE_BANK_COUNT-1:0][STATE_W-1:0] bank_response_data;

    initial begin
        if (STATE_BANK_COUNT < 1 ||
            (STATE_BANK_COUNT & (STATE_BANK_COUNT-1)) != 0)
            $error("STATE_BANK_COUNT must be a positive power of two");
    end

    always_comb begin
        request_ready_o = '0;
        bank_request_valid = '0;
        bank_request_row = '0;
        bank_request_lane = '0;

        for (int lane = 0; lane < REQUEST_COUNT; lane++) begin
            int bank;
            bank = int'(request_index_i[lane]) % STATE_BANK_COUNT;
            if (request_valid_i[lane] && !bank_request_valid[bank]) begin
                request_ready_o[lane] = 1'b1;
                bank_request_valid[bank] = 1'b1;
                bank_request_lane[bank] = REQUEST_W'(lane);
                bank_request_row[bank] = ROW_W'(
                    int'(request_index_i[lane]) / STATE_BANK_COUNT);
            end
        end
    end

    always_comb begin
        response_valid_o = '0;
        response_data_o = '0;
        for (int bank = 0; bank < STATE_BANK_COUNT; bank++) begin
            if (bank_response_valid[bank]) begin
                response_valid_o[bank_response_lane[bank]] = 1'b1;
                response_data_o[bank_response_lane[bank]] =
                    bank_response_data[bank];
            end
        end
    end

    generate
        for (genvar bank = 0; bank < STATE_BANK_COUNT; bank++) begin : gen_banks
            (* ram_style = "block", syn_ramstyle = "block_ram" *)
            logic [STATE_W-1:0] memory [0:BANK_DEPTH-1];

            always_ff @(posedge clk) begin
                if (rst) begin
                    bank_response_valid[bank] <= 1'b0;
                    bank_response_data[bank] <= '0;
                    bank_response_lane[bank] <= '0;
                end
                else begin
                    bank_response_valid[bank] <= bank_request_valid[bank];
                    if (bank_request_valid[bank]) begin
                        bank_response_data[bank] <=
                            memory[bank_request_row[bank]];
                        bank_response_lane[bank] <= bank_request_lane[bank];
                    end
                end

                if (!rst && write_valid_i &&
                    (int'(write_index_i) % STATE_BANK_COUNT) == bank)
                    memory[int'(write_index_i) / STATE_BANK_COUNT] <=
                        write_data_i;
            end
        end
    endgenerate
endmodule
