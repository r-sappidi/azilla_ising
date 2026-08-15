package ising_pkg;
    parameter int SPIN_COUNT = 32;
    parameter int WEIGHT_W   = 8;
    parameter int DATA_W     = 256;
    parameter int ACC_W      = 32;
    parameter int COEFF_W    = 16;

    typedef enum logic [1:0] {
        NOC_STATE      = 2'b00,
        NOC_PARTIAL    = 2'b01,
        NOC_EPOCH_DONE = 2'b10
    } noc_packet_type_t;
endpackage
