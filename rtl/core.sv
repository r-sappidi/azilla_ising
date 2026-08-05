import ising_pkg::*;
module spin_core (
    input  logic                         clk,
    input  logic                         rst,

    // Initial resident state.

    // Resident 32x32 signed-int8 weight block.
    input logic                          init_start,
    output logic                         init_done,
    input  logic                         weight_init_valid,
    output logic                         weight_init_ready,
    input  logic [DATA_W-1:0]            weight_init_data,

    // Static problem configuration.
    input  logic [31:0]                  noise_seed,
    input  logic signed [COEFF_W-1:0]    coeff_a,
    input  logic signed [COEFF_W-1:0]    coeff_b,
    input  logic signed [COEFF_W-1:0]    coeff_c,
    input logic signed [COEFF_W-1:0]     noise_amplitude,
    input logic [SPIN_COUNT-1:0]         init_state,

    // Iteration control.
    input  logic                         iter_start,
    input  logic                         partials_done,
    input  logic                         commit,
    input  logic [16:0]                  noise_decay,
    output logic                         iter_done,
    input logic                          done,

    // Cross-core partials produced within H0.
    input  logic                         h0_partial_valid,
    output logic                         h0_partial_ready,
    input  logic signed [DATA_W-1:0]     h0_partial_data,

    // Partials produced by H1/H2.
    input  logic                         ext_partial_valid,
    output logic                         ext_partial_ready,
    input  logic signed [DATA_W-1:0]     ext_partial_data,

    // Frozen state for the current iteration.
    output logic [SPIN_COUNT-1:0]        state_next, state_current
);

    localparam int WEIGHT_BLOCK_BITS = SPIN_COUNT * SPIN_COUNT * WEIGHT_W;
    localparam int WEIGHT_BEATS      = WEIGHT_BLOCK_BITS / DATA_W;
    localparam int WEIGHT_BEAT_W     = (WEIGHT_BEATS > 1) ? $clog2(WEIGHT_BEATS) : 1;
    localparam int PARTIAL_BITS      = SPIN_COUNT * ACC_W;
    localparam int PARTIAL_BEATS     = PARTIAL_BITS / DATA_W;
    localparam int PARTIAL_BEAT_W    = (PARTIAL_BEATS > 1) ? $clog2(PARTIAL_BEATS) : 1;
    
    typedef enum logic [2:0] {
        CORE_RESET,
        CORE_INIT,
        CORE_IDLE,
        CORE_ACCUMULATE,
        CORE_FINALIZE,
        CORE_WAIT_COMMIT,
        CORE_COMMIT,
        CORE_DONE
    } core_state_t;

    core_state_t core_state, core_state_n;


    logic signed [ACC_W-1:0] accumulator_local [0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] accumulator_h0 [0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] accumulator_ext [0:SPIN_COUNT-1];
    logic signed [ACC_W-1:0] accumulator_total [0:SPIN_COUNT-1];
    logic signed [COEFF_W-1:0] coeff_a_reg;
    logic signed [COEFF_W-1:0] coeff_b_reg;
    logic signed [COEFF_W-1:0] noise_amplitude_reg;

    logic [31:0] lfsr_state;
    logic [31:0] noise_seed_reg;

    logic [WEIGHT_BEAT_W:0] weight_beat_count;
    logic [WEIGHT_BEAT_W-1:0] local_weight_read_row;
    logic [WEIGHT_W*SPIN_COUNT-1:0] local_weight_read_data;
    logic local_weight_write_enable;

    logic [PARTIAL_BEAT_W-1:0] h0_partial_beat_count;
    logic [PARTIAL_BEAT_W-1:0] ext_partial_beat_count;
    logic                      partials_done_pending;
    logic                      local_compute_done;
    logic done_latched;

    assign weight_init_ready =
      (core_state == CORE_INIT) &&
      (weight_beat_count < WEIGHT_BEATS);
    assign h0_partial_ready = core_state == CORE_ACCUMULATE;
    assign ext_partial_ready = core_state == CORE_ACCUMULATE;
    assign iter_done = (core_state == CORE_WAIT_COMMIT);
    assign local_weight_write_enable = weight_init_valid && weight_init_ready;
    always_comb begin
        for (int i = 0; i < SPIN_COUNT; i++) begin
            state_next[i] = ~accumulator_total[i][ACC_W-1];
        end
    end
    logic feedback;
    assign feedback = lfsr_state[31] ^
               lfsr_state[21] ^
               lfsr_state[1]  ^
               lfsr_state[0];

    // The core's diagonal 32x32 block is resident in SRAM slot zero.
    j_block_sram #(
        .ROW_COUNT(SPIN_COUNT),
        .ROW_W(SPIN_COUNT*WEIGHT_W)
    ) local_weight_sram (
        .clk,
        .write_enable_i(local_weight_write_enable),
        .write_slot_i(1'b0),
        .write_row_i(weight_beat_count[WEIGHT_BEAT_W-1:0]),
        .write_data_i(weight_init_data),
        .read_slot_i(1'b0),
        .read_row_i(local_weight_read_row),
        .read_data_o(local_weight_read_data)
    );

    //MVM Engine
    mvm mvm_local(
        .clk,
        .rst, 
        .start(iter_start&&core_state==CORE_IDLE), 
        .weight_row_o(local_weight_read_row),
        .weight_data_i(local_weight_read_data),
        .result(accumulator_local),
        .state(state_current),
        .done(local_compute_done)
    );

   
    always_ff @(posedge clk) begin
        if (rst) begin
            core_state             <= CORE_RESET;
            state_current          <= '0;
            coeff_a_reg            <= '0;
            coeff_b_reg            <= '0;
            noise_seed_reg         <= '0;
            noise_amplitude_reg    <= '0;
            weight_beat_count      <= '0;
            h0_partial_beat_count  <= '0;
            ext_partial_beat_count <= '0;
            partials_done_pending  <= 1'b0;
            init_done              <= 1'b0;

            accumulator_h0 <= '{default: '0};
            accumulator_ext <= '{default: '0};
            done_latched<=1'b0;
            lfsr_state  <= '{default: '0};
        end
        else begin
            done_latched<=done?1:done_latched;
            core_state<=core_state_n;
            if (iter_start) begin
                lfsr_state <= {lfsr_state[30:0], feedback};
                partials_done_pending <= 1'b0;
            end
            else if (partials_done)
                partials_done_pending <= 1'b1;

            if(init_start)begin
                state_current<=init_state;
                coeff_a_reg<=coeff_a;
                coeff_b_reg<=coeff_b;
                
                lfsr_state<=noise_seed;
            end
            if(core_state==CORE_INIT) begin
                if(weight_init_valid&&weight_init_ready)begin
                    weight_beat_count<=weight_beat_count+1;
                end
            end
            if(core_state_n==CORE_IDLE) begin
                init_done<=1'b1;
                noise_amplitude_reg <= noise_amplitude;
            end             
            
            if(core_state==CORE_IDLE && iter_start) begin
                accumulator_h0 <= '{default: '0};
                accumulator_ext <= '{default: '0};
                
                h0_partial_beat_count<='0;
                ext_partial_beat_count<='0;
                partials_done_pending<='0;
            end
            if(core_state==CORE_ACCUMULATE) begin
                if(h0_partial_valid&&h0_partial_ready) begin
                    for(int i=0;i<DATA_W/ACC_W;i+=1)begin
                        accumulator_h0[h0_partial_beat_count*(DATA_W/ACC_W)+i]<=accumulator_h0[h0_partial_beat_count*(DATA_W/ACC_W)+i]+$signed(h0_partial_data[i*ACC_W +: ACC_W]);
                    end

                    if(h0_partial_beat_count==PARTIAL_BEATS-1)begin
                        h0_partial_beat_count<='0;
                    end

                    else begin
                        h0_partial_beat_count<=h0_partial_beat_count+1;
                    end
                end
                if(ext_partial_valid&&ext_partial_ready) begin
                    for(int i=0;i<DATA_W/ACC_W;i+=1)begin
                        accumulator_ext[ext_partial_beat_count*(DATA_W/ACC_W)+i]<=accumulator_ext[ext_partial_beat_count*(DATA_W/ACC_W)+i]+$signed(ext_partial_data[i*ACC_W +: ACC_W]);
                    end
                    if(ext_partial_beat_count==PARTIAL_BEATS-1)begin
                        ext_partial_beat_count<='0;
                    end
                    else begin
                        ext_partial_beat_count<=ext_partial_beat_count+1;
                    end
                end
            end
            
            if (core_state == CORE_FINALIZE) begin
                for (int i = 0; i < SPIN_COUNT; i+=1) begin
                    logic signed [COEFF_W-1:0] a_term;
                    logic signed [COEFF_W-1:0] noise;
                    a_term=state_current[i]?+coeff_a_reg:-coeff_a_reg;
                    noise=lfsr_state[i]?+noise_amplitude:-noise_amplitude;
                    accumulator_total[i] <=
                    a_term+coeff_b_reg*(accumulator_local[i] +
                    accumulator_h0[i] +
                    accumulator_ext[i])+noise;
                    
                end
            end
            if(core_state==CORE_COMMIT)begin
                state_current<=state_next;
            end
        end
    end
    
    always_comb begin
        core_state_n=core_state;
        unique case (core_state)
            CORE_RESET: begin
                if(init_start) core_state_n=CORE_INIT;
            end
            CORE_INIT: begin
                if(~weight_init_ready) begin
                    core_state_n=CORE_IDLE;
                end
            end
            CORE_IDLE: begin
                if(iter_start) core_state_n=CORE_ACCUMULATE;
            end
            CORE_ACCUMULATE:begin
                if(local_compute_done&&partials_done_pending&&(h0_partial_beat_count==0)&&(ext_partial_beat_count==0)) begin
                     core_state_n=CORE_FINALIZE;
                end
            end
            CORE_FINALIZE:begin
                core_state_n=CORE_WAIT_COMMIT;
            end
            CORE_WAIT_COMMIT: begin
                if(iter_done&&commit) core_state_n=CORE_COMMIT;
            end 
            CORE_COMMIT: begin
                if(done_latched||done) core_state_n=CORE_DONE;
                else core_state_n=CORE_IDLE;
            end
            CORE_DONE: begin
                core_state_n=CORE_DONE;
            end
        endcase
    end 
endmodule
