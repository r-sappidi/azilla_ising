// Shared architectural constants and packet types for the Azilla RTL.
//
// A state bit encodes a binary spin: 1 is +1 and 0 is -1. The accelerator
// operates on 32-spin blocks. Consequently, a 32x32 signed-int8 J block is
// 1,024 bytes (32 DATA_W beats), and a 32-lane accumulator partial is
// 1,024 bits (four DATA_W flits).
package ising_pkg;
    parameter int SPIN_COUNT = 32;
    parameter int WEIGHT_W   = 8;
    parameter int DATA_W     = 256;
    parameter int ACC_W      = 32;
    parameter int COEFF_W    = 16;

    // Packet types carried by the top-level mesh. NOC_PARTIAL is the only
    // multi-flit packet; its final flit is identified by the separate `last`
    // field transported with the packet metadata.
    typedef enum logic [1:0] {
        NOC_STATE      = 2'b00,
        NOC_PARTIAL    = 2'b01,
        NOC_EPOCH_DONE = 2'b10
    } noc_packet_type_t;
endpackage
