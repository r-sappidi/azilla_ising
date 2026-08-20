import ising_pkg::*;

// Native Azilla wrapper around FlooNoC's protocol-independent five-port
// router.  The accelerator keeps its existing ready/valid packet interface;
// this module packs that interface into a Floo flit at every input and
// unpacks it again at every output.
//
// FlooNoC port order is north, east, south, west, local/eject.  Azilla's
// top_node uses local, north, south, east, west, so the explicit mapping below
// is intentionally kept in one place.
//
// No Azilla scheduling or endpoint behavior belongs here. This wrapper only
// translates types, port numbering, coordinates, and ready/valid signals; the
// vendored FlooNoC instance owns buffering, routing, and output arbitration.
module azilla_floo_router #(
    parameter int X_W               = 3,
    parameter int Y_W               = 3,
    parameter int SOURCE_ID_W       = 8,
    parameter int EPOCH_W           = 16,
    parameter int GLOBAL_BLOCK_ID_W = 16,
    parameter int FIFO_DEPTH        = 8,
    parameter int ROUTER_X          = 0,
    parameter int ROUTER_Y          = 0
) (
    input logic clk,
    input logic rst,

    input  logic [4:0]                         in_valid_i,
    output logic [4:0]                         in_ready_o,
    input  logic [4:0][DATA_W-1:0]             in_data_i,
    input  logic [4:0][1:0]                    in_type_i,
    input  logic [4:0][X_W-1:0]                in_dest_x_i,
    input  logic [4:0][Y_W-1:0]                in_dest_y_i,
    input  logic [4:0][SOURCE_ID_W-1:0]        in_source_id_i,
    input  logic [4:0][EPOCH_W-1:0]            in_epoch_i,
    input  logic [4:0][GLOBAL_BLOCK_ID_W-1:0]  in_block_id_i,
    input  logic [4:0]                         in_last_i,

    output logic [4:0]                         out_valid_o,
    input  logic [4:0]                         out_ready_i,
    output logic [4:0][DATA_W-1:0]             out_data_o,
    output logic [4:0][1:0]                    out_type_o,
    output logic [4:0][X_W-1:0]                out_dest_x_o,
    output logic [4:0][Y_W-1:0]                out_dest_y_o,
    output logic [4:0][SOURCE_ID_W-1:0]        out_source_id_o,
    output logic [4:0][EPOCH_W-1:0]            out_epoch_o,
    output logic [4:0][GLOBAL_BLOCK_ID_W-1:0]  out_block_id_o,
    output logic [4:0]                         out_last_o
);
    localparam int PORT_COUNT = 5;
    localparam int VC_COUNT = 1;

    localparam int AZ_LOCAL = 0;
    localparam int AZ_NORTH = 1;
    localparam int AZ_SOUTH = 2;
    localparam int AZ_EAST  = 3;
    localparam int AZ_WEST  = 4;

    localparam int FLOO_NORTH = 0;
    localparam int FLOO_EAST  = 1;
    localparam int FLOO_SOUTH = 2;
    localparam int FLOO_WEST  = 3;
    localparam int FLOO_LOCAL = 4;

    typedef logic [X_W-1:0] x_id_t;
    typedef logic [Y_W-1:0] y_id_t;
    typedef logic port_id_t;

    typedef struct packed {
        x_id_t x;
        y_id_t y;
        port_id_t port_id;
    } node_id_t;

    // This is a native, non-AXI Floo header.  FlooNoC's generic router only
    // requires destination/source IDs, a packet tail bit, and the collective
    // opcode used by its optional collective logic.
    typedef struct packed {
        node_id_t dst_id;
        node_id_t src_id;
        logic last;
        logic [1:0] message_type;
        logic [3:0] collective_op;
    } floo_hdr_t;

    typedef struct packed {
        logic [EPOCH_W-1:0] epoch;
        logic [GLOBAL_BLOCK_ID_W-1:0] block_id;
        logic [SOURCE_ID_W-1:0] source_id;
        logic [DATA_W-1:0] data;
    } floo_payload_t;

    typedef struct packed {
        floo_hdr_t hdr;
        floo_payload_t payload;
    } floo_flit_t;

    node_id_t local_id;
    logic [PORT_COUNT-1:0][VC_COUNT-1:0] floo_valid_i;
    logic [PORT_COUNT-1:0][VC_COUNT-1:0] floo_ready_o;
    floo_flit_t [PORT_COUNT-1:0][0:0] floo_data_i;
    logic [PORT_COUNT-1:0][VC_COUNT-1:0] floo_credit_o;
    logic [PORT_COUNT-1:0][VC_COUNT-1:0] floo_valid_o;
    logic [PORT_COUNT-1:0][VC_COUNT-1:0] floo_ready_i;
    floo_flit_t [PORT_COUNT-1:0][0:0] floo_data_o;
    logic [PORT_COUNT-1:0][VC_COUNT-1:0] floo_credit_i;

    // Collective reduction is disabled, so these ports are inert.
    logic offload_req;

    function automatic int floo_port(input int azilla_port);
        case (azilla_port)
            AZ_LOCAL: floo_port = FLOO_LOCAL;
            AZ_NORTH: floo_port = FLOO_NORTH;
            AZ_SOUTH: floo_port = FLOO_SOUTH;
            AZ_EAST:  floo_port = FLOO_EAST;
            default:  floo_port = FLOO_WEST;
        endcase
    endfunction

    always_comb begin
        local_id = '0;
        local_id.x = X_W'(ROUTER_X);
        local_id.y = Y_W'(ROUTER_Y);
    end

    // Input-side packet packing.  Keep it separate from output unpacking so
    // simulation and synthesis tools do not infer a false combinational loop
    // through the entire adapter.
    always_comb begin
        floo_valid_i = '0;
        floo_data_i = '0;

        for (int az_port = 0; az_port < PORT_COUNT; az_port++) begin
            floo_valid_i[floo_port(az_port)][0] = in_valid_i[az_port];
            floo_data_i[floo_port(az_port)][0].hdr = '0;
            floo_data_i[floo_port(az_port)][0].hdr.dst_id.x = in_dest_x_i[az_port];
            floo_data_i[floo_port(az_port)][0].hdr.dst_id.y = in_dest_y_i[az_port];
            floo_data_i[floo_port(az_port)][0].hdr.dst_id.port_id = 1'b0;
            floo_data_i[floo_port(az_port)][0].hdr.src_id = local_id;
            floo_data_i[floo_port(az_port)][0].hdr.last = in_last_i[az_port];
            floo_data_i[floo_port(az_port)][0].hdr.message_type = in_type_i[az_port];
            floo_data_i[floo_port(az_port)][0].payload.data = in_data_i[az_port];
            floo_data_i[floo_port(az_port)][0].payload.source_id =
                in_source_id_i[az_port];
            floo_data_i[floo_port(az_port)][0].payload.epoch = in_epoch_i[az_port];
            floo_data_i[floo_port(az_port)][0].payload.block_id =
                in_block_id_i[az_port];

        end
    end

    // Ready/valid mapping contains no packet data transformations.
    always_comb begin
        floo_ready_i = '0;
        floo_credit_i = '1;
        in_ready_o = '0;
        out_valid_o = '0;
        for (int az_port = 0; az_port < PORT_COUNT; az_port++) begin
            in_ready_o[az_port] = floo_ready_o[floo_port(az_port)][0];
            out_valid_o[az_port] = floo_valid_o[floo_port(az_port)][0];
            floo_ready_i[floo_port(az_port)][0] = out_ready_i[az_port];
        end
    end

    // Output-side packet unpacking.
    always_comb begin
        out_data_o = '0;
        out_type_o = '0;
        out_dest_x_o = '0;
        out_dest_y_o = '0;
        out_source_id_o = '0;
        out_epoch_o = '0;
        out_block_id_o = '0;
        out_last_o = '0;
        for (int az_port = 0; az_port < PORT_COUNT; az_port++) begin
            out_data_o[az_port] = floo_data_o[floo_port(az_port)][0].payload.data;
            out_type_o[az_port] =
                floo_data_o[floo_port(az_port)][0].hdr.message_type;
            out_dest_x_o[az_port] =
                floo_data_o[floo_port(az_port)][0].hdr.dst_id.x;
            out_dest_y_o[az_port] =
                floo_data_o[floo_port(az_port)][0].hdr.dst_id.y;
            out_source_id_o[az_port] =
                floo_data_o[floo_port(az_port)][0].payload.source_id;
            out_epoch_o[az_port] = floo_data_o[floo_port(az_port)][0].payload.epoch;
            out_block_id_o[az_port] =
                floo_data_o[floo_port(az_port)][0].payload.block_id;
            out_last_o[az_port] = floo_data_o[floo_port(az_port)][0].hdr.last;
        end
    end

    floo_router #(
        .NumRoutes(PORT_COUNT),
        .NumInput(PORT_COUNT),
        .NumOutput(PORT_COUNT),
        .NumVirtChannels(VC_COUNT),
        .NumPhysChannels(1),
        .InFifoDepth(FIFO_DEPTH),
        .OutFifoDepth(0),
        .RouteAlgo(floo_pkg::XYRouting),
        .IdWidth($bits(node_id_t)),
        .id_t(node_id_t),
        // A schedule may publish a state to the co-located cross-H1 endpoint,
        // so local injection to local ejection must remain legal.
        .NoLoopback(1'b0),
        .flit_t(floo_flit_t),
        .hdr_t(floo_hdr_t),
        .red_req_t(logic),
        .red_rsp_t(logic)
    ) floo_router_i (
        .clk_i(clk),
        .rst_ni(!rst),
        .test_enable_i(1'b0),
        .xy_id_i(local_id),
        .id_route_map_i('0),
        .valid_i(floo_valid_i),
        .ready_o(floo_ready_o),
        .data_i(floo_data_i),
        .credit_o(floo_credit_o),
        .valid_o(floo_valid_o),
        .ready_i(floo_ready_i),
        .data_o(floo_data_o),
        .credit_i(floo_credit_i),
        .offload_req_o(offload_req),
        .offload_rsp_i(1'b0)
    );

    logic unused_floo_status;
    assign unused_floo_status = ^floo_credit_o ^ offload_req;
endmodule
