`timescale 1ns/1ps

import ising_pkg::*;

// Compile-once, router-accurate NoC performance model. The maximum router
// fabric is elaborated once; runtime traffic files select an active rectangle
// and drive packets produced by the software hierarchy/timing model.
module hybrid_noc_perf_tb #(
    parameter int MAX_MESH_X = 4,
    parameter int MAX_MESH_Y = 4,
    parameter int FIFO_DEPTH = 4,
    parameter int X_W = 4,
    parameter int Y_W = 4,
    parameter int SOURCE_ID_W = 8,
    parameter int EPOCH_W = 8,
    parameter int BLOCK_ID_W = 16,
    parameter longint MAX_CYCLES = 64'd1_000_000_000
);
    localparam int MAX_NODES = MAX_MESH_X * MAX_MESH_Y;
    localparam int LOCAL = 0;
    localparam int NORTH = 1;
    localparam int SOUTH = 2;
    localparam int EAST = 3;
    localparam int WEST = 4;

    typedef struct {
        int phase;
        longint release_cycle;
        int destination;
        int packet_type;
        int block_id;
        int flit_count;
    } traffic_packet_t;

    logic clk;
    logic rst;
    int active_x;
    int active_y;
    int active_nodes;
    int max_phase;
    int current_phase;
    longint cycle_count;
    longint phase_cycle;
    int quiet_cycles;
    bit model_done;

    traffic_packet_t traffic_queue [0:MAX_NODES-1][$];
    traffic_packet_t current_packet [0:MAX_NODES-1];
    bit current_valid [0:MAX_NODES-1];
    int current_flits_left [0:MAX_NODES-1];
    longint phase_packets [0:31];

    logic [MAX_NODES-1:0][4:0] router_in_valid;
    logic [MAX_NODES-1:0][4:0] router_in_ready;
    logic [MAX_NODES-1:0][4:0][DATA_W-1:0] router_in_data;
    logic [MAX_NODES-1:0][4:0][1:0] router_in_type;
    logic [MAX_NODES-1:0][4:0][X_W-1:0] router_in_dest_x;
    logic [MAX_NODES-1:0][4:0][Y_W-1:0] router_in_dest_y;
    logic [MAX_NODES-1:0][4:0][SOURCE_ID_W-1:0] router_in_source_id;
    logic [MAX_NODES-1:0][4:0][EPOCH_W-1:0] router_in_epoch;
    logic [MAX_NODES-1:0][4:0][BLOCK_ID_W-1:0] router_in_block_id;
    logic [MAX_NODES-1:0][4:0] router_in_last;

    logic [MAX_NODES-1:0][4:0] router_out_valid;
    logic [MAX_NODES-1:0][4:0] router_out_ready;
    logic [MAX_NODES-1:0][4:0][DATA_W-1:0] router_out_data;
    logic [MAX_NODES-1:0][4:0][1:0] router_out_type;
    logic [MAX_NODES-1:0][4:0][X_W-1:0] router_out_dest_x;
    logic [MAX_NODES-1:0][4:0][Y_W-1:0] router_out_dest_y;
    logic [MAX_NODES-1:0][4:0][SOURCE_ID_W-1:0] router_out_source_id;
    logic [MAX_NODES-1:0][4:0][EPOCH_W-1:0] router_out_epoch;
    logic [MAX_NODES-1:0][4:0][BLOCK_ID_W-1:0] router_out_block_id;
    logic [MAX_NODES-1:0][4:0] router_out_last;

    longint unsigned offered [0:MAX_NODES-1][0:5];
    longint unsigned accepted [0:MAX_NODES-1][0:5];
    longint unsigned stalled [0:MAX_NODES-1][0:5];
    longint unsigned packets [0:MAX_NODES-1][0:5];
    longint unsigned type_flits [0:MAX_NODES-1][0:5][0:3];
    longint unsigned prev_offered [0:MAX_NODES-1][0:5];
    longint unsigned prev_accepted [0:MAX_NODES-1][0:5];
    longint unsigned prev_stalled [0:MAX_NODES-1][0:5];
    longint unsigned prev_packets [0:MAX_NODES-1][0:5];
    longint unsigned prev_types [0:MAX_NODES-1][0:5][0:3];
    bit stall_active [0:MAX_NODES-1][0:5];
    longint signed inflight_flits;
    longint unsigned peak_inflight;
    longint unsigned interval_peak_inflight;
    longint unsigned interval_start;
    int stats_interval;
    int stats_file;
    int timeline_file;
    int event_file;

    function automatic int physical_node(input int logical_node);
        return (logical_node / active_x) * MAX_MESH_X +
               logical_node % active_x;
    endfunction

    function automatic int logical_node(input int physical);
        return (physical / MAX_MESH_X) * active_x +
               physical % MAX_MESH_X;
    endfunction

    function automatic bit node_active(input int physical);
        return physical % MAX_MESH_X < active_x &&
               physical / MAX_MESH_X < active_y;
    endfunction

    function automatic int type_index(input logic [1:0] packet_type);
        case (packet_type)
            NOC_STATE: return 0;
            NOC_PARTIAL: return 1;
            NOC_EPOCH_DONE: return 2;
            default: return 3;
        endcase
    endfunction

    function automatic string direction_name(input int resource);
        case (resource)
            0: return "north";
            1: return "south";
            2: return "east";
            3: return "west";
            default: return "local";
        endcase
    endfunction

    function automatic int direction_port(input int resource);
        case (resource)
            0: return NORTH;
            1: return SOUTH;
            2: return EAST;
            3: return WEST;
            default: return LOCAL;
        endcase
    endfunction

    function automatic bit physical_link(input int physical, input int resource);
        int x;
        int y;
        x = physical % MAX_MESH_X;
        y = physical / MAX_MESH_X;
        case (resource)
            0: return y > 0;
            1: return y + 1 < active_y;
            2: return x + 1 < active_x;
            default: return x > 0;
        endcase
    endfunction

    generate
        for (genvar y = 0; y < MAX_MESH_Y; y++) begin : gen_y
            for (genvar x = 0; x < MAX_MESH_X; x++) begin : gen_x
                localparam int N = y*MAX_MESH_X+x;
                azilla_floo_router #(
                    .X_W(X_W), .Y_W(Y_W), .SOURCE_ID_W(SOURCE_ID_W),
                    .EPOCH_W(EPOCH_W), .GLOBAL_BLOCK_ID_W(BLOCK_ID_W),
                    .FIFO_DEPTH(FIFO_DEPTH), .ROUTER_X(x), .ROUTER_Y(y)
                ) router (
                    .clk, .rst,
                    .in_valid_i(router_in_valid[N]),
                    .in_ready_o(router_in_ready[N]),
                    .in_data_i(router_in_data[N]),
                    .in_type_i(router_in_type[N]),
                    .in_dest_x_i(router_in_dest_x[N]),
                    .in_dest_y_i(router_in_dest_y[N]),
                    .in_source_id_i(router_in_source_id[N]),
                    .in_epoch_i(router_in_epoch[N]),
                    .in_block_id_i(router_in_block_id[N]),
                    .in_last_i(router_in_last[N]),
                    .out_valid_o(router_out_valid[N]),
                    .out_ready_i(router_out_ready[N]),
                    .out_data_o(router_out_data[N]),
                    .out_type_o(router_out_type[N]),
                    .out_dest_x_o(router_out_dest_x[N]),
                    .out_dest_y_o(router_out_dest_y[N]),
                    .out_source_id_o(router_out_source_id[N]),
                    .out_epoch_o(router_out_epoch[N]),
                    .out_block_id_o(router_out_block_id[N]),
                    .out_last_o(router_out_last[N])
                );
            end
        end
    endgenerate

    // Runtime-active rectangular submesh. Inactive routers stay reset-free but
    // disconnected and never receive traffic.
    always_comb begin : connect_mesh
        router_in_valid = '0;
        router_in_type = '0;
        router_in_dest_x = '0;
        router_in_dest_y = '0;
        router_in_source_id = '0;
        router_in_epoch = '0;
        router_in_block_id = '0;
        router_in_last = '1;
        router_out_ready = '1;
        for (int physical = 0; physical < MAX_NODES; physical++) begin
            int x;
            int y;
            int logical_id;
            x = physical % MAX_MESH_X;
            y = physical / MAX_MESH_X;
            logical_id = logical_node(physical);
            router_in_data[physical] = '0;
            if (node_active(physical)) begin
                if (current_valid[physical]) begin
                    router_in_valid[physical][LOCAL] = 1'b1;
                    router_in_type[physical][LOCAL] =
                        2'(current_packet[physical].packet_type);
                    router_in_dest_x[physical][LOCAL] =
                        X_W'(current_packet[physical].destination % active_x);
                    router_in_dest_y[physical][LOCAL] =
                        Y_W'(current_packet[physical].destination / active_x);
                    router_in_source_id[physical][LOCAL] = SOURCE_ID_W'(logical_id);
                    router_in_block_id[physical][LOCAL] =
                        BLOCK_ID_W'(current_packet[physical].block_id);
                    router_in_last[physical][LOCAL] =
                        current_flits_left[physical] == 1;
                end
                if (y > 0) begin
                    router_in_valid[physical][NORTH] =
                        router_out_valid[physical-MAX_MESH_X][SOUTH];
                    router_in_type[physical][NORTH] =
                        router_out_type[physical-MAX_MESH_X][SOUTH];
                    router_in_dest_x[physical][NORTH] =
                        router_out_dest_x[physical-MAX_MESH_X][SOUTH];
                    router_in_dest_y[physical][NORTH] =
                        router_out_dest_y[physical-MAX_MESH_X][SOUTH];
                    router_in_source_id[physical][NORTH] =
                        router_out_source_id[physical-MAX_MESH_X][SOUTH];
                    router_in_epoch[physical][NORTH] =
                        router_out_epoch[physical-MAX_MESH_X][SOUTH];
                    router_in_block_id[physical][NORTH] =
                        router_out_block_id[physical-MAX_MESH_X][SOUTH];
                    router_in_last[physical][NORTH] =
                        router_out_last[physical-MAX_MESH_X][SOUTH];
                    router_out_ready[physical-MAX_MESH_X][SOUTH] =
                        router_in_ready[physical][NORTH];
                end
                if (y + 1 < active_y) begin
                    router_in_valid[physical][SOUTH] =
                        router_out_valid[physical+MAX_MESH_X][NORTH];
                    router_in_type[physical][SOUTH] =
                        router_out_type[physical+MAX_MESH_X][NORTH];
                    router_in_dest_x[physical][SOUTH] =
                        router_out_dest_x[physical+MAX_MESH_X][NORTH];
                    router_in_dest_y[physical][SOUTH] =
                        router_out_dest_y[physical+MAX_MESH_X][NORTH];
                    router_in_source_id[physical][SOUTH] =
                        router_out_source_id[physical+MAX_MESH_X][NORTH];
                    router_in_epoch[physical][SOUTH] =
                        router_out_epoch[physical+MAX_MESH_X][NORTH];
                    router_in_block_id[physical][SOUTH] =
                        router_out_block_id[physical+MAX_MESH_X][NORTH];
                    router_in_last[physical][SOUTH] =
                        router_out_last[physical+MAX_MESH_X][NORTH];
                    router_out_ready[physical+MAX_MESH_X][NORTH] =
                        router_in_ready[physical][SOUTH];
                end
                if (x + 1 < active_x) begin
                    router_in_valid[physical][EAST] =
                        router_out_valid[physical+1][WEST];
                    router_in_type[physical][EAST] =
                        router_out_type[physical+1][WEST];
                    router_in_dest_x[physical][EAST] =
                        router_out_dest_x[physical+1][WEST];
                    router_in_dest_y[physical][EAST] =
                        router_out_dest_y[physical+1][WEST];
                    router_in_source_id[physical][EAST] =
                        router_out_source_id[physical+1][WEST];
                    router_in_epoch[physical][EAST] =
                        router_out_epoch[physical+1][WEST];
                    router_in_block_id[physical][EAST] =
                        router_out_block_id[physical+1][WEST];
                    router_in_last[physical][EAST] =
                        router_out_last[physical+1][WEST];
                    router_out_ready[physical+1][WEST] =
                        router_in_ready[physical][EAST];
                end
                if (x > 0) begin
                    router_in_valid[physical][WEST] =
                        router_out_valid[physical-1][EAST];
                    router_in_type[physical][WEST] =
                        router_out_type[physical-1][EAST];
                    router_in_dest_x[physical][WEST] =
                        router_out_dest_x[physical-1][EAST];
                    router_in_dest_y[physical][WEST] =
                        router_out_dest_y[physical-1][EAST];
                    router_in_source_id[physical][WEST] =
                        router_out_source_id[physical-1][EAST];
                    router_in_epoch[physical][WEST] =
                        router_out_epoch[physical-1][EAST];
                    router_in_block_id[physical][WEST] =
                        router_out_block_id[physical-1][EAST];
                    router_in_last[physical][WEST] =
                        router_out_last[physical-1][EAST];
                    router_out_ready[physical-1][EAST] =
                        router_in_ready[physical][WEST];
                end
            end
        end
    end

    function automatic bit routers_busy;
        for (int physical = 0; physical < MAX_NODES; physical++)
            if (node_active(physical) && |router_out_valid[physical])
                return 1'b1;
        return 1'b0;
    endfunction

    function automatic bit injectors_busy;
        for (int physical = 0; physical < MAX_NODES; physical++)
            if (node_active(physical) && current_valid[physical])
                return 1'b1;
        return 1'b0;
    endfunction

    always #5 clk = ~clk;

    always @(posedge clk) begin : drive_traffic
        if (rst) begin
            cycle_count = 0;
            phase_cycle = 0;
            current_phase = 0;
            quiet_cycles = 0;
            model_done = 1'b0;
            for (int physical = 0; physical < MAX_NODES; physical++) begin
                current_valid[physical] = 1'b0;
                current_flits_left[physical] = 0;
            end
        end else if (!model_done) begin
            cycle_count++;
            phase_cycle++;
            if (cycle_count >= MAX_CYCLES)
                $fatal(1, "hybrid NoC timeout at cycle %0d", cycle_count);
            for (int physical = 0; physical < MAX_NODES; physical++) begin
                if (node_active(physical)) begin
                    if (current_valid[physical] &&
                        router_in_ready[physical][LOCAL]) begin
                        if (current_flits_left[physical] == 1) begin
                            current_valid[physical] = 1'b0;
                            current_flits_left[physical] = 0;
                            phase_packets[current_phase]--;
                        end else begin
                            current_flits_left[physical]--;
                        end
                    end
                    if (!current_valid[physical] &&
                        traffic_queue[physical].size() != 0 &&
                        traffic_queue[physical][0].phase == current_phase &&
                        traffic_queue[physical][0].release_cycle <= phase_cycle) begin
                        current_packet[physical] = traffic_queue[physical].pop_front();
                        current_flits_left[physical] =
                            current_packet[physical].flit_count;
                        current_valid[physical] = 1'b1;
                    end
                end
            end
            if (phase_packets[current_phase] == 0 &&
                !injectors_busy() && !routers_busy())
                quiet_cycles++;
            else
                quiet_cycles = 0;
            if (quiet_cycles == 4) begin
                $display("hybrid phase %0d drained at cycle %0d",
                         current_phase, cycle_count);
                if (current_phase == max_phase)
                    model_done = 1'b1;
                else begin
                    current_phase++;
                    phase_cycle = 0;
                    quiet_cycles = 0;
                end
            end
        end
    end

    task automatic log_event(input string event_name, input string scope,
                             input int physical, input int resource,
                             input logic [1:0] packet_type,
                             input logic [SOURCE_ID_W-1:0] source,
                             input logic [BLOCK_ID_W-1:0] block_id,
                             input logic [X_W-1:0] dest_x,
                             input logic [Y_W-1:0] dest_y,
                             input logic last, input logic valid,
                             input logic ready);
        int node;
        if (event_file == 0)
            return;
        node = logical_node(physical);
        $fdisplay(event_file,
            "%0d,%s,%s,%0d,%0d,%0d,%s,%0d,%0d,0,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
            cycle_count, event_name, scope, node, node % active_x,
            node / active_x, direction_name(resource), packet_type, source,
            block_id, dest_x, dest_y, last, valid, ready, inflight_flits);
    endtask

    always @(posedge clk) begin : collect_statistics
        int inject_count;
        int eject_count;
        int port;
        int packet;
        bit stalled_now;
        if (rst) begin
            inflight_flits = 0;
            peak_inflight = 0;
            interval_peak_inflight = 0;
            interval_start = 0;
            for (int physical = 0; physical < MAX_NODES; physical++)
                for (int resource = 0; resource < 6; resource++) begin
                    offered[physical][resource] = 0;
                    accepted[physical][resource] = 0;
                    stalled[physical][resource] = 0;
                    packets[physical][resource] = 0;
                    prev_offered[physical][resource] = 0;
                    prev_accepted[physical][resource] = 0;
                    prev_stalled[physical][resource] = 0;
                    prev_packets[physical][resource] = 0;
                    stall_active[physical][resource] = 0;
                    for (int kind = 0; kind < 4; kind++) begin
                        type_flits[physical][resource][kind] = 0;
                        prev_types[physical][resource][kind] = 0;
                    end
                end
        end else if (!model_done) begin
            inject_count = 0;
            eject_count = 0;
            for (int physical = 0; physical < MAX_NODES; physical++) begin
                if (node_active(physical)) begin
                    // resource 4: injection; resource 5: ejection.
                    if (router_in_valid[physical][LOCAL]) begin
                        offered[physical][4]++;
                        if (router_in_ready[physical][LOCAL]) begin
                            inject_count++;
                            accepted[physical][4]++;
                            packet = type_index(router_in_type[physical][LOCAL]);
                            type_flits[physical][4][packet]++;
                            if (router_in_last[physical][LOCAL]) packets[physical][4]++;
                            log_event("accept", "inject", physical, 4,
                                router_in_type[physical][LOCAL],
                                router_in_source_id[physical][LOCAL],
                                router_in_block_id[physical][LOCAL],
                                router_in_dest_x[physical][LOCAL],
                                router_in_dest_y[physical][LOCAL],
                                router_in_last[physical][LOCAL], 1'b1, 1'b1);
                        end else stalled[physical][4]++;
                    end
                    stalled_now = router_in_valid[physical][LOCAL] &&
                                  !router_in_ready[physical][LOCAL];
                    if (stalled_now != stall_active[physical][4])
                        log_event(stalled_now ? "stall_begin" : "stall_end",
                            "inject", physical, 4,
                            router_in_type[physical][LOCAL],
                            router_in_source_id[physical][LOCAL],
                            router_in_block_id[physical][LOCAL],
                            router_in_dest_x[physical][LOCAL],
                            router_in_dest_y[physical][LOCAL],
                            router_in_last[physical][LOCAL],
                            router_in_valid[physical][LOCAL],
                            router_in_ready[physical][LOCAL]);
                    stall_active[physical][4] = stalled_now;

                    if (router_out_valid[physical][LOCAL]) begin
                        offered[physical][5]++;
                        eject_count++;
                        accepted[physical][5]++;
                        packet = type_index(router_out_type[physical][LOCAL]);
                        type_flits[physical][5][packet]++;
                        if (router_out_last[physical][LOCAL]) packets[physical][5]++;
                        log_event("accept", "eject", physical, 5,
                            router_out_type[physical][LOCAL],
                            router_out_source_id[physical][LOCAL],
                            router_out_block_id[physical][LOCAL],
                            router_out_dest_x[physical][LOCAL],
                            router_out_dest_y[physical][LOCAL],
                            router_out_last[physical][LOCAL], 1'b1, 1'b1);
                    end
                    for (int resource = 0; resource < 4; resource++) begin
                        if (physical_link(physical, resource)) begin
                            port = direction_port(resource);
                            if (router_out_valid[physical][port]) begin
                                offered[physical][resource]++;
                                if (router_out_ready[physical][port]) begin
                                    accepted[physical][resource]++;
                                    packet = type_index(router_out_type[physical][port]);
                                    type_flits[physical][resource][packet]++;
                                    if (router_out_last[physical][port])
                                        packets[physical][resource]++;
                                    log_event("accept", "link", physical, resource,
                                        router_out_type[physical][port],
                                        router_out_source_id[physical][port],
                                        router_out_block_id[physical][port],
                                        router_out_dest_x[physical][port],
                                        router_out_dest_y[physical][port],
                                        router_out_last[physical][port], 1'b1, 1'b1);
                                end else stalled[physical][resource]++;
                            end
                            stalled_now = router_out_valid[physical][port] &&
                                          !router_out_ready[physical][port];
                            if (stalled_now != stall_active[physical][resource])
                                log_event(stalled_now ? "stall_begin" : "stall_end",
                                    "link", physical, resource,
                                    router_out_type[physical][port],
                                    router_out_source_id[physical][port],
                                    router_out_block_id[physical][port],
                                    router_out_dest_x[physical][port],
                                    router_out_dest_y[physical][port],
                                    router_out_last[physical][port],
                                    router_out_valid[physical][port],
                                    router_out_ready[physical][port]);
                            stall_active[physical][resource] = stalled_now;
                        end
                    end
                end
            end
            inflight_flits += longint'(inject_count) - longint'(eject_count);
            if (inflight_flits < 0)
                $fatal(1, "negative hybrid NoC in-flight count");
            if (inflight_flits > peak_inflight) peak_inflight = inflight_flits;
            if (inflight_flits > interval_peak_inflight)
                interval_peak_inflight = inflight_flits;
            if (cycle_count != 0 && cycle_count % longint'(stats_interval) == 0)
                write_timeline(cycle_count);
        end
    end

    task automatic write_timeline(input longint end_cycle);
        longint unsigned window;
        longint unsigned d_offered, d_accepted, d_stalled, d_packets;
        longint unsigned d_types [0:3];
        real utilization;
        real pressure;
        string scope;
        if (timeline_file == 0 || end_cycle <= interval_start)
            return;
        window = end_cycle - interval_start;
        for (int physical = 0; physical < MAX_NODES; physical++) begin
            if (node_active(physical)) begin
                int node;
                node = logical_node(physical);
                for (int resource = 0; resource < 6; resource++) begin
                    if (resource >= 4 || physical_link(physical, resource)) begin
                        d_offered = offered[physical][resource] - prev_offered[physical][resource];
                        d_accepted = accepted[physical][resource] - prev_accepted[physical][resource];
                        d_stalled = stalled[physical][resource] - prev_stalled[physical][resource];
                        d_packets = packets[physical][resource] - prev_packets[physical][resource];
                        for (int kind = 0; kind < 4; kind++)
                            d_types[kind] = type_flits[physical][resource][kind] -
                                            prev_types[physical][resource][kind];
                        utilization = real'(d_accepted) / real'(window);
                        pressure = d_offered == 0 ? 0.0 :
                                   real'(d_stalled) / real'(d_offered);
                        scope = resource == 4 ? "inject" :
                                resource == 5 ? "eject" : "link";
                        $fdisplay(timeline_file,
                            "%0d,%0d,%s,%0d,%0d,%0d,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0.6f,%0.6f,%0d,%0d",
                            interval_start, end_cycle, scope, node,
                            node % active_x, node / active_x,
                            direction_name(resource), d_offered, d_accepted,
                            d_stalled, d_packets, d_types[0], d_types[1],
                            d_types[2], d_types[3], utilization, pressure,
                            inflight_flits, interval_peak_inflight);
                        prev_offered[physical][resource] = offered[physical][resource];
                        prev_accepted[physical][resource] = accepted[physical][resource];
                        prev_stalled[physical][resource] = stalled[physical][resource];
                        prev_packets[physical][resource] = packets[physical][resource];
                        for (int kind = 0; kind < 4; kind++)
                            prev_types[physical][resource][kind] =
                                type_flits[physical][resource][kind];
                    end
                end
            end
        end
        interval_start = end_cycle;
        interval_peak_inflight = inflight_flits;
        $fflush(timeline_file);
    endtask

    task automatic write_aggregate;
        longint unsigned monitor_cycles;
        string scope;
        real utilization;
        real pressure;
        monitor_cycles = cycle_count;
        for (int physical = 0; physical < MAX_NODES; physical++) begin
            if (node_active(physical)) begin
                int node;
                node = logical_node(physical);
                for (int resource = 0; resource < 6; resource++) begin
                    if (resource >= 4 || physical_link(physical, resource)) begin
                        scope = resource == 4 ? "inject" :
                                resource == 5 ? "eject" : "link";
                        utilization = monitor_cycles == 0 ? 0.0 :
                            real'(accepted[physical][resource]) / real'(monitor_cycles);
                        pressure = offered[physical][resource] == 0 ? 0.0 :
                            real'(stalled[physical][resource]) /
                            real'(offered[physical][resource]);
                        $fdisplay(stats_file,
                            "%s,%0d,%0d,%0d,%s,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0.6f,%0.6f",
                            scope, node, node % active_x, node / active_x,
                            direction_name(resource), offered[physical][resource],
                            accepted[physical][resource], stalled[physical][resource],
                            packets[physical][resource], type_flits[physical][resource][0],
                            type_flits[physical][resource][1],
                            type_flits[physical][resource][2],
                            type_flits[physical][resource][3], utilization, pressure);
                    end
                end
            end
        end
    endtask

    initial begin : run_model
        string traffic_path;
        string stats_path;
        string timeline_path;
        string event_path;
        int traffic_file;
        int file_x, file_y;
        int phase, source, destination, packet_type, block_id, flits;
        longint release_cycle;
        longint loaded_packets;
        int result;
        traffic_packet_t packet_record;

        clk = 1'b0;
        rst = 1'b1;
        stats_interval = 100;
        stats_file = 0;
        timeline_file = 0;
        event_file = 0;
        loaded_packets = 0;
        phase_packets = '{default: 0};
        if (!$value$plusargs("TRAFFIC=%s", traffic_path))
            $fatal(1, "+TRAFFIC=<path> is required");
        void'($value$plusargs("NOC_STATS_INTERVAL=%d", stats_interval));
        if (stats_interval <= 0)
            $fatal(1, "NOC_STATS_INTERVAL must be positive");
        traffic_file = $fopen(traffic_path, "r");
        if (traffic_file == 0)
            $fatal(1, "cannot open traffic file %s", traffic_path);
        result = $fscanf(traffic_file, "%d %d %d\n", file_x, file_y, max_phase);
        if (result != 3 || file_x <= 0 || file_y <= 0 ||
            file_x > MAX_MESH_X || file_y > MAX_MESH_Y || max_phase > 31)
            $fatal(1, "invalid traffic header in %s", traffic_path);
        active_x = file_x;
        active_y = file_y;
        active_nodes = active_x * active_y;
        while (!$feof(traffic_file)) begin
            result = $fscanf(traffic_file, "%d %d %d %d %d %d %d\n",
                phase, release_cycle, source, destination, packet_type,
                block_id, flits);
            if (result == 7) begin
                if (phase < 0 || phase > max_phase || source < 0 ||
                    source >= active_nodes || destination < 0 ||
                    destination >= active_nodes || flits <= 0)
                    $fatal(1, "invalid traffic record in %s", traffic_path);
                packet_record.phase = phase;
                packet_record.release_cycle = release_cycle;
                packet_record.destination = destination;
                packet_record.packet_type = packet_type;
                packet_record.block_id = block_id;
                packet_record.flit_count = flits;
                traffic_queue[physical_node(source)].push_back(packet_record);
                phase_packets[phase]++;
                loaded_packets++;
            end else if (result != -1) begin
                $fatal(1, "malformed traffic record in %s", traffic_path);
            end
        end
        $fclose(traffic_file);

        if (!$value$plusargs("NOC_STATS_FILE=%s", stats_path))
            stats_path = "hybrid_noc_stats.csv";
        stats_file = $fopen(stats_path, "w");
        if (stats_file == 0) $fatal(1, "cannot open %s", stats_path);
        $fdisplay(stats_file,
            "scope,node,x,y,direction,offered_cycles,accepted_flits,stall_cycles,packets,state_flits,partial_flits,epoch_done_flits,other_flits,window_utilization,backpressure_fraction");
        if ($value$plusargs("NOC_TIMELINE_FILE=%s", timeline_path)) begin
            timeline_file = $fopen(timeline_path, "w");
            if (timeline_file == 0) $fatal(1, "cannot open %s", timeline_path);
            $fdisplay(timeline_file,
                "interval_start,interval_end,scope,node,x,y,direction,offered,accepted,stall_cycles,packets,state_flits,partial_flits,epoch_done_flits,other_flits,utilization,backpressure,inflight_end,peak_inflight");
        end
        if ($value$plusargs("NOC_EVENT_FILE=%s", event_path)) begin
            event_file = $fopen(event_path, "w");
            if (event_file == 0) $fatal(1, "cannot open %s", event_path);
            $fdisplay(event_file,
                "cycle,event,scope,node,x,y,direction,packet_type,source_id,epoch,block_id,dest_x,dest_y,last,valid,ready,inflight");
        end
        $display("hybrid NoC model active_mesh=%0dx%0d packets=%0d traffic=%s",
                 active_x, active_y, loaded_packets, traffic_path);
        repeat (5) @(negedge clk);
        rst = 1'b0;
        wait (model_done);
        @(negedge clk);
        write_timeline(cycle_count);
        write_aggregate();
        $fclose(stats_file);
        if (timeline_file != 0) $fclose(timeline_file);
        if (event_file != 0) $fclose(event_file);
        $display("HYBRID NOC PASS cycles=%0d peak_inflight=%0d",
                 cycle_count, peak_inflight);
        $finish;
    end
endmodule
