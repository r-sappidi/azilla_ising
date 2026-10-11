// Experimental physical organization: two independent single-RW slot arrays
// plus one row-zero shadow register per slot. Not a GF12 macro binding.
// Same-slot load/read overlap is supported only for row zero. Other overlaps
// are rejected, not silently serialized. Output remains one-cycle registered.
module j_block_sram #(parameter int ROW_COUNT=32, ROW_W=256)(
 input logic clk, write_enable_i, write_slot_i,
 input logic [((ROW_COUNT>1)?$clog2(ROW_COUNT):1)-1:0] write_row_i,
 input logic [ROW_W-1:0] write_data_i,
 input logic read_slot_i,
 input logic [((ROW_COUNT>1)?$clog2(ROW_COUNT):1)-1:0] read_row_i,
 output logic [ROW_W-1:0] read_data_o
);
 logic [ROW_W-1:0] row_zero[2];
 logic [ROW_W-1:0] q[2];
 logic selected;
 for(genvar s=0;s<2;s++) begin: slots
   logic [ROW_W-1:0] memory[ROW_COUNT];
   always_ff @(posedge clk) begin
     if(write_enable_i && write_slot_i==s) begin
       memory[write_row_i] <= write_data_i;
       if(write_row_i==0) row_zero[s] <= write_data_i;
       // Old shadow value preserves read-before-write on row zero.
       if(read_slot_i==s) begin
         q[s] <= row_zero[s];
         assert(read_row_i==0) else $fatal(1,"unsupported same-slot single-RW overlap");
       end
     end else if(read_slot_i==s) q[s] <= memory[read_row_i];
   end
 end
 always_ff @(posedge clk) selected <= read_slot_i;
 assign read_data_o=q[selected];
endmodule
