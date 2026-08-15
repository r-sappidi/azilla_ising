import ising_pkg::*;

// Five-port buffered NoC router with deterministic XY routing.
//
// Port numbering is local, north, south, east, west. Each input owns a FIFO.
// Every output performs round-robin arbitration and locks to the selected input
// until an accepted flit carries last_i. Metadata travels as sideband signals
// and remains attached to every payload flit.
module noc_router #(
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
    localparam int LOCAL = 0;
    localparam int NORTH = 1;
    localparam int SOUTH = 2;
    localparam int EAST = 3;
    localparam int WEST = 4;
    localparam int PORT_ID_W = $clog2(PORT_COUNT);
    localparam int PTR_W = (FIFO_DEPTH > 1) ? $clog2(FIFO_DEPTH) : 1;
    localparam int COUNT_W = $clog2(FIFO_DEPTH + 1);

    logic [DATA_W-1:0] fifo_data [0:PORT_COUNT-1][0:FIFO_DEPTH-1];
    logic [1:0] fifo_type [0:PORT_COUNT-1][0:FIFO_DEPTH-1];
    logic [X_W-1:0] fifo_dest_x [0:PORT_COUNT-1][0:FIFO_DEPTH-1];
    logic [Y_W-1:0] fifo_dest_y [0:PORT_COUNT-1][0:FIFO_DEPTH-1];
    logic [SOURCE_ID_W-1:0] fifo_source_id [0:PORT_COUNT-1][0:FIFO_DEPTH-1];
    logic [EPOCH_W-1:0] fifo_epoch [0:PORT_COUNT-1][0:FIFO_DEPTH-1];
    logic [GLOBAL_BLOCK_ID_W-1:0] fifo_block_id [0:PORT_COUNT-1][0:FIFO_DEPTH-1];
    logic fifo_last [0:PORT_COUNT-1][0:FIFO_DEPTH-1];

    logic [PTR_W-1:0] read_pointer [0:PORT_COUNT-1];
    logic [PTR_W-1:0] write_pointer [0:PORT_COUNT-1];
    logic [COUNT_W-1:0] fifo_count [0:PORT_COUNT-1];

    logic [PORT_COUNT-1:0] output_locked;
    logic [PORT_ID_W-1:0] locked_input [0:PORT_COUNT-1];
    logic [PORT_ID_W-1:0] round_robin_start [0:PORT_COUNT-1];
    logic [PORT_ID_W-1:0] selected_input [0:PORT_COUNT-1];
    logic [PORT_COUNT-1:0] selected_valid;
    logic [PORT_COUNT-1:0] input_pop;

    function automatic logic [PORT_ID_W-1:0] route_port(
        input logic [X_W-1:0] destination_x,
        input logic [Y_W-1:0] destination_y
    );
        if (destination_x > X_W'(ROUTER_X))
            route_port = PORT_ID_W'(EAST);
        else if (destination_x < X_W'(ROUTER_X))
            route_port = PORT_ID_W'(WEST);
        else if (destination_y > Y_W'(ROUTER_Y))
            route_port = PORT_ID_W'(SOUTH);
        else if (destination_y < Y_W'(ROUTER_Y))
            route_port = PORT_ID_W'(NORTH);
        else
            route_port = PORT_ID_W'(LOCAL);
    endfunction

    function automatic logic [PTR_W-1:0] next_pointer(
        input logic [PTR_W-1:0] pointer
    );
        if (pointer == PTR_W'(FIFO_DEPTH-1))
            next_pointer = '0;
        else
            next_pointer = pointer + 1'b1;
    endfunction

    always_comb begin
        in_ready_o = '0;
        out_valid_o = '0;
        out_data_o = '0;
        out_type_o = '0;
        out_dest_x_o = '0;
        out_dest_y_o = '0;
        out_source_id_o = '0;
        out_epoch_o = '0;
        out_block_id_o = '0;
        out_last_o = '0;
        selected_input = '{default: '0};
        selected_valid = '0;

        for (int input_index = 0; input_index < PORT_COUNT; input_index++)
            in_ready_o[input_index] =
                fifo_count[input_index] < COUNT_W'(FIFO_DEPTH);

        for (int output_index = 0; output_index < PORT_COUNT; output_index++) begin
            logic found_request;
            int candidate;
            int selected;
            found_request = 1'b0;
            candidate = 0;
            selected = 0;

            if (output_locked[output_index]) begin
                selected_input[output_index] = locked_input[output_index];
                selected_valid[output_index] =
                    fifo_count[locked_input[output_index]] != 0;
            end
            else begin
                for (int offset = 0; offset < PORT_COUNT; offset++) begin
                    candidate = (int'(round_robin_start[output_index]) + offset)
                        % PORT_COUNT;
                    if (!found_request && fifo_count[candidate] != 0 &&
                        route_port(fifo_dest_x[candidate][read_pointer[candidate]],
                                   fifo_dest_y[candidate][read_pointer[candidate]]) ==
                            PORT_ID_W'(output_index)) begin
                        selected_input[output_index] = PORT_ID_W'(candidate);
                        selected_valid[output_index] = 1'b1;
                        found_request = 1'b1;
                    end
                end
            end

            if (selected_valid[output_index]) begin
                selected = int'(selected_input[output_index]);
                out_valid_o[output_index] = 1'b1;
                out_data_o[output_index] =
                    fifo_data[selected][read_pointer[selected]];
                out_type_o[output_index] =
                    fifo_type[selected][read_pointer[selected]];
                out_dest_x_o[output_index] =
                    fifo_dest_x[selected][read_pointer[selected]];
                out_dest_y_o[output_index] =
                    fifo_dest_y[selected][read_pointer[selected]];
                out_source_id_o[output_index] =
                    fifo_source_id[selected][read_pointer[selected]];
                out_epoch_o[output_index] =
                    fifo_epoch[selected][read_pointer[selected]];
                out_block_id_o[output_index] =
                    fifo_block_id[selected][read_pointer[selected]];
                out_last_o[output_index] =
                    fifo_last[selected][read_pointer[selected]];

            end
        end
    end

    // Keep ready-dependent dequeue control separate from route and payload
    // selection so metadata has no combinational dependency on downstream
    // ready.
    always_comb begin
        input_pop = '0;
        for (int output_index = 0; output_index < PORT_COUNT; output_index++) begin
            if (selected_valid[output_index] && out_ready_i[output_index])
                input_pop[selected_input[output_index]] = 1'b1;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            output_locked <= '0;
            for (int port_index = 0; port_index < PORT_COUNT; port_index++) begin
                read_pointer[port_index] <= '0;
                write_pointer[port_index] <= '0;
                fifo_count[port_index] <= '0;
                locked_input[port_index] <= '0;
                round_robin_start[port_index] <= '0;
            end
        end
        else begin
            for (int input_index = 0; input_index < PORT_COUNT; input_index++) begin
                logic push;
                push = in_valid_i[input_index] && in_ready_o[input_index];

                if (push) begin
                    fifo_data[input_index][write_pointer[input_index]] <=
                        in_data_i[input_index];
                    fifo_type[input_index][write_pointer[input_index]] <=
                        in_type_i[input_index];
                    fifo_dest_x[input_index][write_pointer[input_index]] <=
                        in_dest_x_i[input_index];
                    fifo_dest_y[input_index][write_pointer[input_index]] <=
                        in_dest_y_i[input_index];
                    fifo_source_id[input_index][write_pointer[input_index]] <=
                        in_source_id_i[input_index];
                    fifo_epoch[input_index][write_pointer[input_index]] <=
                        in_epoch_i[input_index];
                    fifo_block_id[input_index][write_pointer[input_index]] <=
                        in_block_id_i[input_index];
                    fifo_last[input_index][write_pointer[input_index]] <=
                        in_last_i[input_index];
                    write_pointer[input_index] <=
                        next_pointer(write_pointer[input_index]);
                end

                if (input_pop[input_index])
                    read_pointer[input_index] <=
                        next_pointer(read_pointer[input_index]);

                unique case ({push, input_pop[input_index]})
                    2'b10: fifo_count[input_index] <= fifo_count[input_index] + 1'b1;
                    2'b01: fifo_count[input_index] <= fifo_count[input_index] - 1'b1;
                    default: fifo_count[input_index] <= fifo_count[input_index];
                endcase
            end

            for (int output_index = 0; output_index < PORT_COUNT; output_index++) begin
                if (out_valid_o[output_index] && out_ready_i[output_index]) begin
                    if (output_locked[output_index]) begin
                        if (out_last_o[output_index]) begin
                            output_locked[output_index] <= 1'b0;
                            round_robin_start[output_index] <=
                                PORT_ID_W'((int'(selected_input[output_index]) + 1)
                                    % PORT_COUNT);
                        end
                    end
                    else if (!out_last_o[output_index]) begin
                        output_locked[output_index] <= 1'b1;
                        locked_input[output_index] <= selected_input[output_index];
                    end
                    else begin
                        round_robin_start[output_index] <=
                            PORT_ID_W'((int'(selected_input[output_index]) + 1)
                                % PORT_COUNT);
                    end
                end
            end
        end
    end
endmodule
