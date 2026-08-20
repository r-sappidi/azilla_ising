# Azilla Ising Accelerator: RTL Module Reference

## 1. Architectural contract

Azilla evaluates a synchronous binary Ising update over a distributed state
vector. A stored state bit of one represents +1 and zero represents -1. The
intended update is:

```text
field[i]  = a*x[i] + b*sum_j(J[i,j]*x[j]) + noise[i]
x_next[i] = +1 when field[i] >= 0, otherwise -1
```

The global state is divided into 32-spin blocks and the signed-int8 coupling
matrix J into 32x32 blocks. With the standard 256-bit streaming datapath, one J
block is 1,024 bytes and arrives as 32 beats; one 32-lane partial vector is
1,024 bits and leaves as four beats.

Each interaction is computed at the lowest level containing both endpoints:

| Endpoints | Compute owner |
|---|---|
| Same 32-spin block | `spin_core` |
| Different cores in one H0 | H0 `hierarchy_node` |
| Different H0s in one H1 | H1 `hierarchy_node` |
| Different H1s | mesh `cross_h1_node` |

For symmetric J, each off-diagonal block fetch generates both endpoint
contributions:

```text
partial_A = J_AB * x_B
partial_B = transpose(J_AB) * x_A
```

Because each state is +1 or -1, the arithmetic uses signed add/subtract rather
than general multiplication. All levels operate on a frozen `state_current`;
only a system-wide commit advances it to `state_next`.

The present RTL intentionally excludes autonomous schedule generation and a
DRAM controller. A testbench supplies block commands and bandwidth-limited
weight streams. This preserves arithmetic, buffering, routing, contention and
backpressure behavior while postponing scheduler/DMA implementation.

## 2. Common conventions

Shared widths and NoC types reside in `ising_pkg.sv`:

| Name | Default | Meaning |
|---|---:|---|
| `SPIN_COUNT` | 32 | Spins per state block/core |
| `WEIGHT_W` | 8 | Signed J-element width |
| `DATA_W` | 256 | Weight, partial and NoC payload width |
| `ACC_W` | 32 | Partial width per spin |
| `COEFF_W` | 16 | Coefficient width |
| `NOC_STATE` | `00` | One-flit state publication |
| `NOC_PARTIAL` | `01` | Four-flit accumulated contribution |
| `NOC_EPOCH_DONE` | `10` | One-flit parent-level completion |

Every stream uses ready/valid: transfer occurs only on a rising clock edge
where both are high. A blocked producer must hold valid, data and metadata
stable. Multi-flit partial packets cannot be interleaved.

Names ending in `_i` are module inputs and `_o` are outputs. Array dimensions
usually proceed from coarse to fine: node, H0, engine/core, then datapath bits.

## 3. `ising_mesh`: full-system top level

### Purpose and organization

`ising_mesh` is the testbench-facing accelerator top. It instantiates a
rectangular array of `mesh_h1_tile` instances. Node number and coordinate map
as:

```text
node = y*MESH_X_COUNT + x
x    = node % MESH_X_COUNT
y    = node / MESH_X_COUNT
```

For every tile, east connects to the next tile's west port and south connects
to the next row's north port. Boundary inputs are invalid and boundary outputs
are ready. Correct XY routing must never send a valid packet out of a boundary.

Structural parameters specify the mesh dimensions, H0/core counts, engine
counts, identifier widths, FIFO depth, blocks per H1 and size of the global
cross-H1 state table. The leading dimension of almost every port is
`NODE_COUNT`.

### I/O

| Signal | Meaning |
|---|---|
| `clk`, `rst` | Shared synchronous clock and active-high reset. |
| `init_start_i[node]` | Starts initialization of every core under that H1. |
| `init_done_o[node]` | All cores in that H1 have loaded their resident block. |
| `core_weight_valid_i[node][h0][core]` | Valid for a core-local J beat. |
| `core_weight_ready_o[...]` | Selected core can accept that beat. |
| `core_weight_data_i[...]` | 256-bit core-local J row/beat. |
| `init_state_i[node][h0][core]` | Initial 32-spin state vector. |
| `noise_seed_i[...]` | Initial per-core 32-bit LFSR state. |
| `coeff_a_i`, `coeff_b_i`, `coeff_c_i` | Per-H1 update coefficients distributed to its cores. |
| `noise_amplitude_i` | Per-H1 already-scaled signed noise magnitude. |
| `iter_start_i[node]` | Begins one synchronous iteration below that H1. |
| `iter_done_o[node]` | Every core under that H1 has formed `state_next`. |
| `commit_i[node]` | Copies every local `state_next` to `state_current`. |
| `done_i[node]` | Ends the core lifecycle after final commit; retained from the core controller interface. |
| `noise_decay_i[node]` | Iteration decay input; currently exposed but not used by the core arithmetic. |
| `epoch_i[node]` | Epoch tagged on outgoing packets and used to reject stale incoming traffic. |
| `h0_schedule_done_i[node][h0]` | No more cross-core blocks will be issued to that H0 this epoch. |
| `h0_dma_cmd_valid/ready_*` | Per-H0, per-engine command handshake. |
| `h0_dma_state_a/b_index_i` | Indices of the two states in that H0 node's local state table. |
| `h0_dma_block_a/b_id_i` | Global destination block IDs for the two results. |
| `h0_dma_weight_valid/ready_*`, `h0_dma_weight_data_i` | Per-engine J-block streams. |
| `h1_schedule_done_i[node]` | No more cross-H0 blocks for that H1. |
| `h1_dma_cmd_valid/ready_*` | H1-node command handshakes. |
| `h1_dma_state_a/b_index_i` | Indices in the H1's flattened H0/core state table. |
| `h1_dma_block_a/b_id_i` | Global destinations of both H1 results. |
| `h1_dma_weight_valid/ready_*`, `h1_dma_weight_data_i` | H1 J-block streams. |
| `state_publish_valid/ready_*` | Handshake requesting publication of one local state block. |
| `state_publish_local_index_i` | Selects the source state inside the local H1. |
| `state_publish_block_id_i` | Global identity attached to that state. |
| `state_publish_dest_x/y_i` | Cross-H1 compute location receiving the state. |
| `done_publish_valid/ready_*` | Handshake requesting one `NOC_EPOCH_DONE` packet. |
| `done_publish_dest_x/y_i` | H1 mesh endpoint receiving that completion packet. |
| `cross_iter_start_i[node]` | Starts the cross-H1 hierarchy node at a mesh location. |
| `cross_iter_done_o[node]` | That compute node has accepted and emitted all scheduled results. It does not prove the whole mesh is empty. |
| `cross_schedule_done_i[node]` | No further cross-H1 commands will arrive there. |
| `cross_dma_cmd_valid/ready_*` | Cross-H1 per-engine command handshakes. |
| `cross_dma_state_a/b_index_i` | Indices in the receiving top node's state table; normally global block indices. |
| `cross_dma_block_a/b_id_i` | Global result destinations. |
| `cross_dma_weight_valid/ready_*`, `cross_dma_weight_data_i` | Cross-H1 J-block streams. |
| `state_current_o[node][h0][core]` | Complete distributed frozen input state. |
| `state_next_o[...]` | Complete candidate output state for checking and commit. |

The testbench must coordinate initialization, publish states, issue all three
schedule levels, wait for computation/network drain, inject completion, wait
for `iter_done_o`, and finally commit.

## 4. `mesh_h1_tile`: one mesh location

### Purpose and structure

This wrapper integrates everything physically associated with one H1:

```text
mesh_h1_tile
├── h1_tile
├── h1_noc_adapter
└── top_node
    ├── azilla_floo_router
    └── cross_h1_node
```

The H1 tile's parent-partial port connects to the adapter. The adapter's packet
port connects to the top node's local H1 endpoint. The wrapper converts a
flattened publication index to H0/core indices and reads the selected frozen
state directly from `state_current_o`.

### I/O

Initialization, coefficient, iteration, H0/H1/cross schedule and state output
signals have the same meaning as one node slice of `ising_mesh`. Additional
physical-link signals are:

| Signal | Meaning |
|---|---|
| `link_in_valid_i[4]`, `link_in_ready_o[4]` | Incoming cardinal-link handshake. |
| `link_in_data_i[4]` | Incoming 256-bit flits. |
| `link_in_type_i`, `link_in_dest_x/y_i` | Packet type and route coordinates. |
| `link_in_source_id_i`, `link_in_epoch_i` | Origin and iteration metadata. |
| `link_in_block_id_i`, `link_in_last_i` | Destination spin block and packet-tail marker. |
| `link_out_*` | Corresponding outbound link signals. |

Link index zero is north, one south, two east and three west. `NODE_ID`,
`NODE_X`, `NODE_Y` and `BASE_BLOCK_ID` establish this instance's physical and
logical identity.

## 5. `top_node`: router plus cross-H1 compute endpoint

### Purpose and operation

`top_node` combines one five-port router with one cross-H1 compute node. The
router local injection port is shared between locally published H1 traffic and
cross-H1 result traffic. Cross-H1 results receive priority when arbitration is
unlocked; ownership is retained through the tail flit.

On local ejection, `NOC_STATE` packets go to the cross-H1 state table. Other
types go to the H1 adapter. This separation lets the compute endpoint and the
local H1 share one router without understanding each other's internal buses.

### I/O

| Signal group | Meaning |
|---|---|
| `link_in_*`, `link_out_*` | Four cardinal packet links described above. |
| `h1_tx_valid/ready`, `h1_tx_data` | Complete flit injected by the H1 adapter. |
| `h1_tx_type`, `dest_x/y`, `source_id`, `epoch`, `block_id`, `last` | Metadata for that injected flit. |
| `h1_rx_valid/ready`, `h1_rx_data` | Locally ejected non-state flit returned to H1. |
| `h1_rx_type`, `source_id`, `epoch`, `block_id`, `last` | Ejected flit metadata. Destination coordinates are unnecessary after local delivery. |
| `cross_iter_start_i`, `cross_iter_done_o` | Cross-H1 compute lifecycle. |
| `epoch_i` | Epoch stamped on generated partial packets. |
| `cross_schedule_done_i` | Final-command indication for the local cross node. |
| `cross_dma_cmd_*`, `cross_dma_state_*`, `cross_dma_block_*` | Per-engine cross-H1 work descriptors. |
| `cross_dma_weight_*` | Per-engine top-level J streams. |

## 6. `azilla_floo_router`: FlooNoC router wrapper

### Purpose and microarchitecture

The wrapper adapts Azilla's five logical ports (local, north, south, east and
west) and packet metadata to the vendored FlooNoC router request/response
types. FlooNoC performs deterministic XY routing and provides the per-input
buffering configured by `FIFO_DEPTH`.

FlooNoC performs output arbitration and wormhole locking. If an accepted flit
is not the packet tail, an output remains assigned to that packet until its
accepted `last` flit. Backpressure propagates through the wrapper without
changing Azilla's ready/valid contract.

### I/O

| Signal | Meaning |
|---|---|
| `in_valid_i[5]`, `in_ready_o[5]` | Input flit handshakes. |
| `in_data_i[5]` | 256-bit payloads. |
| `in_type_i[5]` | State, partial or epoch-done type. |
| `in_dest_x/y_i[5]` | XY route target. |
| `in_source_id_i[5]` | Producer identity for observability/protocol use. |
| `in_epoch_i[5]` | Iteration identity. |
| `in_block_id_i[5]` | State identity or partial destination. |
| `in_last_i[5]` | Final flit of a packet. |
| `out_*[5]` | The same fields after routing and arbitration. |

`ROUTER_X/Y` are constant coordinates. The wrapper currently exposes no idle,
occupancy or performance-counter port; a testbench must use hierarchical
monitoring until those are added.

## 7. `cross_h1_node`: top-level block evaluator

### Purpose and operation

This module wraps a generic `hierarchy_node` and adapts it to the mesh. Incoming
current-epoch `NOC_STATE` packets write their low 32 payload bits into a state
table indexed by global block ID. Commands and weights supplied externally
then evaluate assigned cross-H1 block pairs.

The node arbitrates its independent engine results onto one packet stream and
locks the selected engine through all four partial flits. For each result:

```text
destination_h1 = block_id / BLOCKS_PER_H1
dest_x         = destination_h1 % MESH_X_COUNT
dest_y         = destination_h1 / MESH_X_COUNT
```

### I/O

| Signal | Meaning |
|---|---|
| `iter_start_i`, `iter_done_o` | Wrapped hierarchy-node lifecycle. |
| `epoch_i` | Current accepted state epoch and outgoing partial epoch. |
| `state_valid_i`, `state_ready_o` | Incoming local-ejection state handshake. |
| `state_data_i`, `state_type_i`, `state_epoch_i`, `state_block_id_i` | State packet fields; stale or inappropriate packets are consumed rather than wedging ejection. |
| `schedule_done_i` | No further cross-H1 commands. |
| `dma_cmd_valid/ready_*` | Per-engine work descriptor handshake. |
| `dma_state_a/b_index_i` | State-table addresses for the block endpoints. |
| `dma_block_a/b_id_i` | Global result destinations. |
| `dma_weight_valid/ready_*`, `dma_weight_data_i` | Per-engine J streams. |
| `tx_valid_o`, `tx_ready_i`, `tx_data_o` | Generated partial packet stream. |
| `tx_type_o`, `tx_dest_x/y_o`, `tx_source_id_o`, `tx_epoch_o`, `tx_block_id_o`, `tx_last_o` | Generated NoC metadata. |

## 8. `h1_noc_adapter`: H1/packet protocol bridge

### Purpose and operation

The adapter does not schedule communication. A testbench or future schedule
player selects one local state and destination. The adapter formats it as a
single-flit `NOC_STATE` packet. A completion request similarly becomes a
single-flit `NOC_EPOCH_DONE`; completion has transmit priority because it is
expected only after publication finishes.

On receive, a matching-epoch `NOC_PARTIAL` flit passes to the H1 parent stream
with ready/valid preserved. An accepted matching-epoch `NOC_EPOCH_DONE` tail
generates a one-cycle `parent_partials_done_o`. Stale or unsupported packets
are consumed without affecting arithmetic.

### I/O

| Signal | Meaning |
|---|---|
| `current_epoch_i` | Epoch used for transmit tags and receive filtering. |
| `state_publish_valid/ready_*` | Publication-request handshake. |
| `state_publish_data_i` | Selected 32-bit state. |
| `state_publish_block_id_i`, `state_publish_dest_x/y_i` | State identity and target cross node. |
| `done_publish_valid/ready_*`, `done_publish_dest_x/y_i` | Completion-packet request and destination. |
| `noc_tx_*` | Complete outgoing flit interface to `top_node`. |
| `noc_rx_*` | Complete incoming flit interface from `top_node`. |
| `parent_partial_valid_o`, `parent_partial_ready_i` | H1 parent-stream handshake. |
| `parent_partial_data_o`, `parent_partial_block_id_o` | Partial payload and destination. |
| `parent_partials_done_o` | One-cycle accepted completion pulse. |

## 9. `h1_tile`: H1-local compute hierarchy

### Purpose and structure

An H1 tile instantiates `H0_COUNT` complete H0 tiles, one generic hierarchy
node, and one H1 child adapter. Its hierarchy node owns interactions between
different H0 children. Its state table is flattened as:

```text
state_index = h0_index*CORES_PER_H0 + core_index
```

After `iter_start`, the tile sequentially copies every child's frozen state
into the H1 node, starts that node, and accepts the externally supplied H1
schedule. H1-local and parent results merge in the child adapter. The H1 marks
external work complete only when both its own node and the parent are done.

### I/O

| Signal group | Meaning |
|---|---|
| `init_start`, `init_done`, `core_weight_*`, `init_state_i`, `noise_seed_i` | Initialization broadcast and per-core data. |
| `coeff_a/b/c_i`, `noise_amplitude_i` | Configuration distributed to all cores. |
| `iter_start`, `iter_done`, `commit`, `done`, `noise_decay_i` | H1 lifecycle and synchronous state update. |
| `h0_schedule_done_i`, `h0_dma_*` | Separate schedule/weight interfaces for every child H0 node. |
| `h1_schedule_done_i`, `h1_dma_*` | Schedule/weight interface for this H1 node. |
| `parent_partial_valid/ready_*` | One cross-H1 partial flit stream. |
| `parent_partial_block_id_i`, `parent_partial_data_i` | Its global destination and payload. |
| `parent_partials_done_i` | Latched indication that no more parent partials will arrive. |
| `state_current_o`, `state_next_o` | All child-core states. |

`iter_done` is the AND reduction of child H0 completion. `commit` is broadcast
so the whole H1 advances atomically.

## 10. `h1_child_adapter`: H1-to-H0 result routing

### Purpose and operation

This adapter merges local H1 engine outputs and the single parent partial
stream, then routes each packet to the H0 whose contiguous block-ID range
contains the destination. Every child H0 has an independent output, allowing
simultaneous traffic to different H0s.

Local H1 results have fixed priority over parent traffic. Arbitration locks for
the complete packet. Local streams provide `last`; the parent interface has
implicit fixed-length framing, so the adapter maintains a modulo-four accepted
beat counter.

### I/O

| Signal group | Meaning |
|---|---|
| `node_partial_valid/ready_*` | One input stream per H1 MVM engine. |
| `node_partial_data_i`, `node_partial_block_id_i`, `node_partial_last_i` | Local result fields. |
| `parent_partial_valid/ready_*` | Single top-level input stream. |
| `parent_partial_data_i`, `parent_partial_block_id_i` | Parent result fields. |
| `child_partial_valid_o`, `child_partial_ready_i` | One output handshake per H0. |
| `child_partial_data_o`, `child_partial_block_id_o` | Routed result fields. |

## 11. `h0_tile`: cores plus H0 compute

### Purpose and structure

An H0 tile instantiates `CORE_COUNT` spin cores, one generic hierarchy node and
one H0 adapter. Cores evaluate diagonal blocks, while the node evaluates blocks
between different cores in the H0.

On iteration start, all cores begin local computation while the tile copies
their frozen states sequentially into the H0 node. Higher-level partials can
arrive concurrently and are demultiplexed directly to the addressed core's
external accumulator. Once both H0-local and higher-level work are complete,
the tile asserts `partials_done` to all cores.

### I/O

| Signal group | Meaning |
|---|---|
| `init_start`, `init_done`, `core_weight_*`, `init_state_i`, `noise_seed_i` | Core initialization. |
| `coeff_a/b/c_i`, `noise_amplitude_i` | Shared core configuration. |
| `iter_start`, `iter_done`, `commit`, `done`, `noise_decay_i` | H0 lifecycle. |
| `schedule_done_i`, `dma_cmd_valid/ready_*` | H0 command completion and per-engine descriptors. |
| `dma_state_a/b_index_i` | Core-state indices in the H0 table. |
| `dma_block_a/b_id_i` | Global result destinations. |
| `dma_weight_valid/ready_*`, `dma_weight_data_i` | Per-engine H0 J streams. |
| `ext_partial_valid/ready_*` | Single higher-level partial stream. |
| `ext_partial_block_id_i`, `ext_partial_data_i` | External destination and payload. |
| `ext_partials_done_i` | No more higher-level results this epoch. |
| `state_current_o`, `state_next_o` | All core states. |

## 12. `h0_adapter`: H0-engine-to-core crossbar

### Purpose and operation

Each engine result's block ID maps to one local core by subtracting
`BASE_BLOCK_ID`. Each core is an independent output bank. Fixed-priority
arbitration resolves multiple engines targeting one core and locks through the
four-flit packet; engines targeting different cores proceed concurrently.

### I/O

| Signal group | Meaning |
|---|---|
| `partial_valid/ready_*` | One source handshake per H0 MVM engine. |
| `partial_data_i`, `partial_block_id_i`, `partial_last_i` | Source result fields. |
| `core_partial_valid_o`, `core_partial_ready_i` | One destination handshake per core. |
| `core_partial_data_o` | Routed 256-bit partial beats. |

The block ID is consumed by the adapter and is not needed at the core because
each output wire physically identifies its destination.

## 13. `hierarchy_node`: reusable off-diagonal compute node

### Purpose and microarchitecture

The node is hierarchy-level agnostic. `STATE_ENTRY_COUNT` determines its local
state capacity and `MVM_COUNT` its symmetric engines. A controller first loads
a frozen state table. Each accepted engine command reserves one of two J slots;
the following 32 accepted weight beats fill that slot.

Each engine has independent compute and output controllers and two result
slots. Thus the next block can compute while the previous two directional
results serialize. A new compute launches only when a complete J slot and free
result slot exist. `iter_done` is asserted after `schedule_done_i` has been
latched and all loads, J slots, engines, result slots and output packets drain.

### I/O

| Signal | Meaning |
|---|---|
| `iter_start` | Clears per-iteration completion and enables the new schedule. |
| `iter_done` | Sticky completion until the next start. |
| `state_valid_i`, `state_ready_o` | State-table write handshake. |
| `state_index_i`, `state_data_i` | Destination table index and 32-bit state. |
| `schedule_done_i` | Final-command marker, not immediate completion. |
| `dma_cmd_valid/ready_*` | One command channel per engine. |
| `dma_state_a/b_index_i` | Addresses of x_A and x_B in the local state table. |
| `dma_block_a_i`, `dma_block_b_i` | Global destinations retained with results. |
| `dma_weight_valid/ready_*`, `dma_weight_data_i` | J-block loading channels. |
| `partial_valid/ready_*` | One result stream per engine. |
| `partial_data_o`, `partial_block_id_o`, `partial_last_o` | Four-beat result payload, destination and tail. |

Command acceptance must precede the corresponding weight stream. An engine can
load only one slot at a time, although its other slot may compute.

## 14. `symmetric_mvm`: fused two-direction MVM

### Purpose and operation

The hierarchy engine consumes one row of J_AB per cycle. That row completes one
element of `J_AB*x_B` and simultaneously contributes to all elements of
`transpose(J_AB)*x_A`. State bits choose signed addition or subtraction. A
synchronous SRAM request/response pipeline separates requested and processed
rows.

### I/O

| Signal | Meaning |
|---|---|
| `clk`, `rst` | Sequential control. |
| `start` | Begins a block if idle. |
| `state_a`, `state_b` | Frozen endpoint state blocks. |
| `weight_row_o` | SRAM row requested for the active block. |
| `weight_data_i` | Returned row containing 32 signed weights. |
| `result_a[32]` | `J_AB*x_B`. |
| `result_b[32]` | `transpose(J_AB)*x_A`. |
| `done` | Completion pulse after all rows are accumulated. |

## 15. `spin_core`: architectural state endpoint

### Purpose and microarchitecture

One core owns 32 spins, their diagonal 32x32 J block, current/next state, a
local MVM, three accumulator banks and LFSR state. At iteration start it clears
H0/external accumulators and launches the local MVM. Four-beat H0 and external
packets accumulate independently and may arrive concurrently.

After the local MVM completes, `partials_done` has been observed, and both
stream beat counters are on packet boundaries, the core forms the final field,
takes its sign and asserts `iter_done`. `commit` advances state atomically.

### I/O

| Signal | Meaning |
|---|---|
| `clk`, `rst` | Core clock and reset. |
| `init_start`, `init_done` | Initialization lifecycle. |
| `weight_init_valid`, `weight_init_ready`, `weight_init_data` | 32-beat resident diagonal-J load. |
| `noise_seed` | Initial 32-bit LFSR value. |
| `coeff_a`, `coeff_b`, `coeff_c` | Update coefficients latched at initialization. |
| `noise_amplitude` | Signed magnitude chosen by each LFSR bit. |
| `init_state` | Initial 32-spin state. |
| `iter_start` | Launches local computation and clears streamed accumulators. |
| `partials_done` | No more H0 or higher-level partial packets will arrive. |
| `commit` | Copies next state to current state. |
| `noise_decay` | Present interface input; currently unused. |
| `iter_done` | Next state is ready and held. |
| `done` | Ends operation after the final committed iteration. |
| `h0_partial_valid/ready`, `h0_partial_data` | H0-local contribution stream. |
| `ext_partial_valid/ready`, `ext_partial_data` | Combined H1/cross-H1 contribution stream. |
| `state_current` | Frozen 32-spin input visible to hierarchy nodes. |
| `state_next` | Candidate updated state. |

Current arithmetic intent is:

```text
total = a*x + b*(local + h0 + external) + signed_noise
```

The present implementation does not internally apply `coeff_c` or
`noise_decay`; the controller must provide the final scaled `noise_amplitude`.
Also, although `noise_amplitude_reg` exists, finalization currently reads the
live `noise_amplitude` input rather than that register, so the system must hold
the input stable for the iteration. This should be corrected before freezing
the interface. Accumulator overflow behavior is ordinary fixed-width RTL
wrapping and still requires an architectural range decision.

## 16. `mvm`: core-local diagonal evaluator

### Purpose and I/O

This engine evaluates the core-resident diagonal block in one direction. For
each row it adds a weight when the corresponding state bit is one and subtracts
it when zero.

| Signal | Meaning |
|---|---|
| `clk`, `rst` | Sequential control. |
| `start` | Starts one 32-row operation. |
| `weight_row_o` | Requested SRAM row. |
| `weight_data_i` | Returned 32-weight row. |
| `state` | Frozen 32-spin vector. |
| `result[32]` | One accumulated dot product per row/spin. |
| `done` | Operation completion pulse. |

Unlike `symmetric_mvm`, it generates only one result because a diagonal block
already represents both endpoints within the same state group.

## 17. `j_block_sram`: J-buffer storage

### Purpose and operation

This inference-oriented 1R1W memory stores two 32-row J blocks for a hierarchy
engine. The slot bit is part of the address, allowing one slot to be written
while the other is read. At defaults it stores 2 KiB. A core uses the same
wrapper for its resident block, while hierarchy nodes use both slots for
double buffering. An ASIC implementation should map it to a technology SRAM
macro with matching synchronous-read and read-during-write semantics.

### I/O

| Signal | Meaning |
|---|---|
| `clk` | Synchronous memory clock. |
| `write_enable_i` | Writes one row on the active edge. |
| `write_slot_i`, `write_row_i` | Write block slot and row. |
| `write_data_i` | Full J row, normally 256 bits. |
| `read_slot_i`, `read_row_i` | Requested block slot and row. |
| `read_data_o` | Synchronously returned row. |

## 18. End-to-end iteration sequence

1. The testbench supplies initial states, coefficients, noise seeds and each
   core's diagonal J block, then waits for all `init_done_o` bits.
2. It asserts H1 and cross-H1 iteration starts. Every core begins its local
   MVM; H0 and H1 tiles copy frozen child states into their node tables.
3. It publishes required state blocks through the mesh to the cross-H1 nodes
   assigned by the external schedule.
4. It streams all H0 blocks, all H1 blocks and all cross-H1 blocks into their
   selected engines, respecting ready/valid and the modeled memory bandwidth.
5. Symmetric engines compute both directions. H0 results route directly to
   cores; H1 results route through H0; cross-H1 results packetize and traverse
   the mesh before following the H1/H0 path.
6. The testbench asserts each `schedule_done` only after the last associated
   command and complete J block have transferred.
7. After all cross compute and network traffic drain, it injects an epoch-done
   packet to each H1. The current RTL does not expose a rigorous mesh-idle bit,
   so a full test must temporarily use hierarchical observation or a proven
   conservative drain interval.
8. Completion propagates H1 to H0 to core. Every core combines local, H0 and
   external accumulators and forms `state_next`.
9. After all `iter_done_o` bits assert, the testbench checks `state_next_o`
   against a golden model and pulses `commit_i` to begin the next epoch.

## 19. Implemented boundary and outstanding work

The RTL currently implements the full hierarchical arithmetic datapath,
double-buffered block engines, result routing, mesh packet transport, epoch
filtering and distributed state commit. The environment still owns:

- schedule construction and per-engine issue order;
- J address generation and external-memory timing;
- state-publication destination selection;
- global network-drain/completion generation;
- performance-counter collection;
- CDC and physical chiplet/memory interfaces.

Consequently, the design is ready for testbench-driven functional and timing
experiments, but it is not yet an autonomous accelerator subsystem. The most
important integration addition for rigorous testing is a mesh-idle or formal
distributed-completion mechanism so epoch completion cannot overtake a late
partial.
