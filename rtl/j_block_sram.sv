// Double-buffered J-block storage for one symmetric MVM engine.
//
// The memory has one synchronous read port and one write port. The slot bit is
// part of each address, allowing the streamer to fill one 32-row block while
// the MVM reads the other. This coding style is suitable for FPGA block-RAM
// inference and for replacement by an ASIC 1R1W SRAM macro.
module j_block_sram #(
    parameter int ROW_COUNT = 32,
    parameter int ROW_W     = 256
) (
    input  logic                                              clk,

    input  logic                                              write_enable_i,
    input  logic                                              write_slot_i,
    input  logic [((ROW_COUNT > 1) ? $clog2(ROW_COUNT) : 1)-1:0]
                                                               write_row_i,
    input  logic [ROW_W-1:0]                                 write_data_i,

    input  logic                                              read_slot_i,
    input  logic [((ROW_COUNT > 1) ? $clog2(ROW_COUNT) : 1)-1:0]
                                                               read_row_i,
    output logic [ROW_W-1:0]                                 read_data_o
);
    localparam int DEPTH = 2 * ROW_COUNT;
    localparam int ADDR_W = (DEPTH > 1) ? $clog2(DEPTH) : 1;

    logic [ADDR_W-1:0] write_address;
    logic [ADDR_W-1:0] read_address;

    // Common inference hints. Unsupported attributes are ignored by tools;
    // ASIC flows can replace this module with a technology SRAM wrapper.
    (* ram_style = "block", syn_ramstyle = "block_ram" *)
    logic [ROW_W-1:0] memory [0:DEPTH-1];

    always_comb begin
        write_address = {write_slot_i, write_row_i};
        read_address = {read_slot_i, read_row_i};
    end

    always_ff @(posedge clk) begin
        if (write_enable_i)
            memory[write_address] <= write_data_i;

        read_data_o <= memory[read_address];
    end
endmodule
