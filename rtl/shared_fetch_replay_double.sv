// Two forwarding slots per source lane, matching the two downstream weight
// slots of CIR. Input fill and output replay may overlap on distinct slots.
// Endpoint deliveries remain ordered, two unicasts, no hierarchy arithmetic.
module shared_fetch_replay_double #(
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
    output logic [255:0] out_weight_data,
    output logic idle_o
);
    logic fill_slot, drain_slot;
    logic [4:0] fill_row;
    logic [5:0] drain_beat;
    logic [1:0] icr, iwr, ocv, owv;
    logic [1:0][BLOCK_W-1:0] oa, ob;
    logic [1:0][255:0] od;
    for (genvar s=0;s<2;s++) begin: slots
        shared_fetch_replay #(.BLOCK_W(BLOCK_W)) replay (
            .clk,.rst,
            .in_cmd_valid(in_cmd_valid && fill_slot==s),
            .in_cmd_ready(icr[s]),.in_a,.in_b,
            .in_weight_valid(in_weight_valid && fill_slot==s),
            .in_weight_ready(iwr[s]),.in_weight_data,
            .out_cmd_valid(ocv[s]),
            .out_cmd_ready(out_cmd_ready && drain_slot==s),
            .out_a(oa[s]),.out_b(ob[s]),
            .out_weight_valid(owv[s]),
            .out_weight_ready(out_weight_ready && drain_slot==s),
            .out_weight_data(od[s]));
    end
    assign in_cmd_ready=icr[fill_slot];
    assign in_weight_ready=iwr[fill_slot];
    assign out_cmd_valid=ocv[drain_slot];
    assign out_a=oa[drain_slot];
    assign out_b=ob[drain_slot];
    assign out_weight_valid=owv[drain_slot];
    assign out_weight_data=od[drain_slot];
    assign idle_o=&icr;
    always_ff @(posedge clk) begin
        if (rst) begin
            fill_slot<=0;drain_slot<=0;fill_row<=0;drain_beat<=0;
        end else begin
            if (in_weight_valid && in_weight_ready) begin
                fill_row<=fill_row+1'b1;
                if (fill_row==31) fill_slot<=~fill_slot;
            end
            if (out_weight_valid && out_weight_ready) begin
                drain_beat<=drain_beat+1'b1;
                if (drain_beat==63) drain_slot<=~drain_slot;
            end
        end
    end
endmodule
