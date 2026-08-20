import ising_pkg::*;

// Simulation-only ready/valid bridge between an RTL weight streamer and one
// independently timed Ramulator memory system.
module ramulator_dpi_bridge #(
    parameter int SYSTEM_ID = 0,
    parameter int REQ_LANES = 16,
    parameter int RSP_LANES = 16,
    parameter int ADDR_W = 64,
    parameter int TAG_W = 10
) (
    input logic clk,
    input logic rst,

    input  logic [REQ_LANES-1:0]                 req_valid_i,
    output logic [REQ_LANES-1:0]                 req_ready_o,
    input  logic [REQ_LANES-1:0][ADDR_W-1:0]     req_addr_i,
    input  logic [REQ_LANES-1:0][TAG_W-1:0]      req_tag_i,

    output logic [RSP_LANES-1:0]                 rsp_valid_o,
    input  logic [RSP_LANES-1:0]                 rsp_ready_i,
    output logic [RSP_LANES-1:0][DATA_W-1:0]     rsp_data_o,
    output logic [RSP_LANES-1:0][TAG_W-1:0]      rsp_tag_o
);
    import "DPI-C" function int az_dram_send(
        input int system_id,
        input longint unsigned address,
        input int tag
    );
    import "DPI-C" function int az_dram_pop(
        input int system_id,
        output longint unsigned address,
        output int tag,
        output bit [DATA_W-1:0] data
    );

    // Calls occur on the falling edge so ready/valid values are stable for the
    // following rising-edge RTL transfer.
    always @(negedge clk) begin
        if (rst) begin
            req_ready_o = '0;
            rsp_valid_o = '0;
            rsp_data_o = '0;
            rsp_tag_o = '0;
        end
        else begin
            req_ready_o = '0;
            for (int lane = 0; lane < REQ_LANES; lane++) begin
                if (req_valid_i[lane])
                    req_ready_o[lane] = az_dram_send(
                        SYSTEM_ID, req_addr_i[lane], int'(req_tag_i[lane])) != 0;
            end

            for (int lane = 0; lane < RSP_LANES; lane++) begin
                if (rsp_valid_o[lane] && rsp_ready_i[lane])
                    rsp_valid_o[lane] = 1'b0;
                if (!rsp_valid_o[lane]) begin
                    longint unsigned response_address;
                    int response_tag;
                    bit [DATA_W-1:0] response_data;
                    if (az_dram_pop(SYSTEM_ID, response_address,
                                    response_tag, response_data) != 0) begin
                        rsp_valid_o[lane] = 1'b1;
                        rsp_data_o[lane] = response_data;
                        rsp_tag_o[lane] = TAG_W'(response_tag);
                    end
                end
            end
        end
    end
endmodule
