# Azilla Hierarchical Ising Accelerator RTL Architecture

## 1. Purpose and scope

Azilla is a block-oriented, hierarchical Ising accelerator. The state of each
spin is binary:

```text
stored bit 1 = spin +1
stored bit 0 = spin -1
```

For every iteration, the intended update is:

```text
field[i]  = a*x[i] + b*sum(J[i,j]*x[j]) + noise[i]
x_next[i] = +1 when field[i] >= 0, otherwise -1
```

The RTL factors the global matrix-vector product into non-overlapping 32x32
weight blocks. Computation is placed at the lowest hierarchy level containing
both endpoint blocks. Each off-diagonal interaction block is read once and
evaluated in both directions, avoiding redundant reads of a symmetric J
matrix.

This document describes the RTL currently present in `rtl/`. It also separates
implemented behavior from components that still belong to the future system
integration, such as the memory streamer, schedule store, and top-level mesh
adapter.

## 2. Default configuration

Global datapath parameters are defined in `rtl/ising_pkg.sv`:

| Parameter | Default | Meaning |
|---|---:|---|
| `SPIN_COUNT` | 32 | Spins represented by one state block and spin core |
| `WEIGHT_W` | 8 | Signed weight width |
| `DATA_W` | 256 | Weight and partial streaming width |
| `ACC_W` | 32 | Width of each partial accumulator lane |
| `COEFF_W` | 16 | Width of Ising coefficients |

The default structural hierarchy is:

```text
H1 tile
  16 H0 tiles
    32 spin cores per H0
      32 spins per core
```

Therefore:

```text
one core =       32 spins
one H0   =    1,024 spins
one H1   =   16,384 spins
```

The hierarchy sizes and engine counts are parameters and are not hardwired
into the compute datapaths.

## 3. Block ownership

Each 32-spin vector has a global block ID. A block ID identifies a destination
spin core anywhere in the complete system. With the default organization:

```text
global spin index = global_block_id*32 + lane_index
```

The interaction matrix is divided into 32x32 blocks. A command for blocks A
and B describes the weight block `J_ab`, where A supplies matrix rows and B
supplies matrix columns.

Interaction ownership is divided as follows:

1. A spin core owns its diagonal 32x32 block. This block describes interactions
   among the 32 spins resident in that core.
2. An H0 hierarchy node owns blocks crossing two different cores in the same
   H0 tile.
3. An H1 hierarchy node owns blocks crossing two different H0 tiles in the same
   H1 tile.
4. A future parent level owns interactions crossing different H1 tiles.

The compiler or scheduler must emit every required block exactly once at the
appropriate level. The RTL does not dynamically discover ownership.

For sparse graphs, the same datapath remains usable:

- Completely empty 32x32 blocks are omitted from the schedule.
- Partially populated blocks are stored with absent edges represented by zero
  weights.
- Locally dense placement should be produced by compile-time graph
  partitioning so that as many useful interactions as possible remain at lower
  hierarchy levels.

## 4. Symmetric bidirectional block evaluation

For an off-diagonal block `J_ab`, one weight fetch produces two result vectors:

```text
partial_a = J_ab * x_b
partial_b = transpose(J_ab) * x_a
```

This works because J is assumed symmetric. The block is stored only in the
`J_ab` orientation; a transposed copy is not stored.

Spin values are +1 or -1, so multiplication by a spin is implemented as signed
addition or subtraction:

```text
weight * (+1) = +weight
weight * (-1) = -weight
```

No general-purpose multiplier is required inside either MVM datapath.

## 5. Spin core

The `spin_core` module in `rtl/core.sv` is the architectural endpoint for 32
spins. It owns:

- The current 32-bit state vector.
- The next 32-bit state vector.
- One resident 32x32 signed-int8 diagonal weight block.
- A local MVM engine.
- Separate accumulator banks for local, H0, and higher-level contributions.
- Coefficient registers and pseudo-random noise state.

### 5.1 Initialization

`init_start` causes the core to latch:

- `init_state`
- `coeff_a`
- `coeff_b`
- `noise_seed`

The resident weight block is then streamed through the `weight_init_*`
ready/valid interface. With default parameters:

```text
weight block size = 32*32*8 = 8,192 bits = 1,024 bytes
stream beat size  = 256 bits = 32 bytes
weight beats      = 8,192/256 = 32 beats
```

Each accepted beat contains one complete row of 32 int8 weights. After all 32
rows have been accepted, `init_done` is asserted and the core enters idle.

### 5.2 Iteration accumulation

On `iter_start`, three sources of field contribution operate concurrently:

1. The local MVM computes the core-resident diagonal block against
   `state_current` and writes `accumulator_local`.
2. The H0 input stream adds cross-core results into `accumulator_h0`.
3. The external input stream adds H1 and higher-level results into
   `accumulator_ext`.

The two streamed accumulator banks are cleared at the beginning of every
iteration. The local MVM clears its own result vector when started.

### 5.3 Partial packet format

A partial packet contains one signed `ACC_W` value for each of the 32 spins:

```text
partial packet bits = SPIN_COUNT*ACC_W
                    = 32*32
                    = 1,024 bits
```

On a 256-bit bus, this is four accepted beats. Each beat carries eight signed
32-bit lanes. Beat zero updates accumulator lanes 0 through 7, beat one updates
8 through 15, and so on.

Packets do not carry explicit start or end bits at the spin-core interface.
The core maintains independent modulo-four beat counters for H0 and external
traffic. Valid may deassert between beats; the counter advances only on a
ready/valid handshake.

The sender must never interleave two packets on the same stream. Adapters lock
their selected source until a complete packet has transferred.

### 5.4 Completion and final update

`partials_done` tells the core that all hierarchy partial packets for the
iteration have been issued. The pulse is latched so it may arrive before the
local MVM completes.

The core leaves accumulation only when:

- The local MVM is done.
- `partials_done` has been observed.
- Both partial beat counters are at packet boundaries.

It then forms, per spin:

```text
accumulator_total = a_term
                  + b*(local_accumulator + h0_accumulator + ext_accumulator)
                  + signed_noise
```

`state_next` is one when `accumulator_total` is nonnegative and zero when it is
negative. `iter_done` remains asserted while the core waits for `commit`.
Commit copies `state_next` into `state_current`, ensuring that every hierarchy
level uses one frozen state vector for the entire iteration.

### 5.5 Current noise implementation

The current RTL advances a 32-bit LFSR once on each `iter_start`. Each bit of
the resulting LFSR word selects positive or negative `noise_amplitude` for one
spin.

Important current limitation: `coeff_c` and `noise_decay` are exposed at the
core interface but are not yet used in the final arithmetic. The controller
must currently provide the already-scaled amplitude through `noise_amplitude`.
If the intended hardware contract is instead `c*decay*noise_sign`, that scaling
must either be implemented in the core or explicitly assigned to the external
controller before the interface is frozen.

## 6. Local MVM engine

The `mvm` module evaluates one 32x32 resident block in the normal direction.
It consumes one SRAM row per cycle and produces one complete dot product per
row.

For every column in a row:

```text
state[column] == 1: add signed weight
state[column] == 0: subtract signed weight
```

The SRAM has a synchronous read, so the MVM separates requested row and
processed row. With the current implementation, one block requires the SRAM
pipeline fill followed by 32 row-processing cycles.

## 7. Symmetric MVM engine

The `symmetric_mvm` module is used by hierarchy nodes for off-diagonal blocks.
For every SRAM row it:

- Computes one complete element of `J_ab*x_b`.
- Adds or subtracts that row into every lane of
  `transpose(J_ab)*x_a`.

After all 32 rows, both 32-element partial vectors are complete. The hierarchy
node then serializes result A followed by result B.

## 8. Weight-block SRAM

`j_block_sram` stores two complete J blocks for one hierarchy MVM engine. The
slot bit and row index form the SRAM address.

The memory has:

- One synchronous read port used by the MVM.
- One write port used by the external weight streamer.
- Two 32-row slots, enabling block-level double buffering.

The streamer can fill one slot while the MVM reads the other. The RTL uses FPGA
block-RAM inference attributes; an ASIC implementation should replace or map
this module to an appropriate 1R1W SRAM macro.

One engine stores:

```text
2 slots * 32 rows * 256 bits = 16,384 bits = 2 KiB
```

## 9. Generic hierarchy node

`hierarchy_node` is independent of hierarchy depth. It does not know whether it
is operating at H0, H1, or a future higher level. Its behavior is determined by
its loaded state table and command stream.

### 9.1 Parameters

| Parameter | Meaning |
|---|---|
| `STATE_ENTRY_COUNT` | Number of frozen 32-spin state vectors visible to the node |
| `MVM_COUNT` | Number of parallel symmetric MVM engines |
| `GLOBAL_BLOCK_ID_W` | Width of global destination block IDs |

At H0, `STATE_ENTRY_COUNT` equals the number of spin cores. At H1, it equals
`H0_COUNT*CORES_PER_H0`.

### 9.2 State loading

Before the node starts an iteration, its controller writes every state vector
referenced by the schedule through `state_valid_i`, `state_ready_o`,
`state_index_i`, and `state_data_i`.

The node stores state vectors by local state-table index. DMA commands refer to
these local indices, while partial destinations use global block IDs. Keeping
these namespaces separate allows a compiler to choose any local state-table
layout without changing system-wide routing.

### 9.3 Per-engine command and weight interfaces

Each MVM engine has its own command and weight stream. A command supplies:

- State-table index A.
- State-table index B.
- Global destination block ID A.
- Global destination block ID B.

The accepted command reserves one of that engine's two J-buffer slots. The
following 32 accepted weight beats populate that slot. Only one slot may be in
the load process for a given engine at a time, while either completed slot may
be consumed by the MVM.

The node deliberately does not contain DMA address generation or schedule
storage. An external compile-time schedule player and memory streamer must
select an engine, issue its metadata command, and stream the corresponding
weight block.

### 9.4 Engine lifecycle

Each engine contains independent compute and output controllers:

```text
compute: IDLE -> START -> COMPUTE -> IDLE
output:  IDLE -> SEND_A -> SEND_B -> IDLE
```

Two result-buffer slots sit between these controllers. Before starting, a
compute reserves a free result slot. On completion it copies both 32-lane
vectors and their global destination IDs into that slot, releases the J-buffer
slot for refilling, and can begin the next buffered block. The output controller
independently selects completed result slots and emits A followed by B, with
four 256-bit beats per direction.

This permits computation of the next block while the previous block is being
serialized. Two slots make the overlap safe under output backpressure: one
result may be draining while the in-flight compute owns the other slot. If both
result slots remain occupied, the compute controller stops launching new work
until serialization frees one.

Every engine has an independent output stream. Engines targeting different
downstream endpoints can therefore transfer concurrently.

### 9.5 Node completion

The external scheduler pulses `schedule_done_i` after issuing the final block
command for the iteration. The node latches this indication and asserts
`iter_done` only after:

- No engine is loading a block.
- Both slots of every engine are empty.
- Every engine is idle.
- Every result packet has been accepted downstream.

Thus schedule completion means no more work will arrive; it does not terminate
work already buffered in the node.

## 10. H0 adapter

`h0_adapter` connects the independent hierarchy-node engine outputs to the
spin-core H0-partial inputs.

For H0, every child owns exactly one global block ID:

```text
destination core = partial_block_id - BASE_BLOCK_ID
```

Each core is an independent output bank. Engines targeting different cores can
transfer concurrently. When multiple engines target the same core, a
fixed-priority arbiter selects one. The selection is locked until
`partial_last_i` is accepted, preventing packet interleaving.

Higher-level partials do not pass through `h0_adapter`. They enter `h0_tile` on
its external stream and are demultiplexed directly to the addressed core's
external accumulator input.

## 11. H0 tile

`h0_tile` integrates:

- `CORE_COUNT` spin cores.
- One generic hierarchy node configured with one state entry per core.
- One H0 adapter.
- A demultiplexer for higher-level partials.
- Iteration sequencing and state-table loading.

### 11.1 H0 iteration sequence

1. `iter_start` starts all spin cores, which immediately begin their resident
   diagonal-block MVMs.
2. The tile sequentially copies every core's frozen `state_current` into the H0
   hierarchy node.
3. The tile pulses the hierarchy node's `iter_start`.
4. The external H0 schedule player supplies commands and J blocks for
   cross-core interactions.
5. H0 results pass through `h0_adapter` into each core's H0 accumulator.
6. Higher-level results may arrive concurrently on the external stream.
7. Once the H0 node is done and the higher-level completion indication has
   been observed, the tile asserts `partials_done` to every core.
8. Every core finalizes independently. Tile `iter_done` is the AND reduction of
   all core `iter_done` signals.
9. A global `commit` updates all 32-spin state vectors together.

## 12. H1 child adapter

`h1_child_adapter` routes two categories of traffic to child H0 tiles:

- Results produced by the local H1 hierarchy node.
- Results received from the parent hierarchy level.

Each H0 owns a contiguous range of block IDs:

```text
first = BASE_BLOCK_ID + child_index*BLOCKS_PER_CHILD
last  = first + BLOCKS_PER_CHILD - 1
```

The adapter maps the global block ID to the owning H0 range. Every H0 has one
external-partial stream, so local H1 engines and the parent stream arbitrate for
that child. H1-local results currently have fixed priority over parent traffic.

The H1 hierarchy node supplies `partial_last_i`. The parent stream has implicit
packet framing, so the adapter maintains a modulo-`PARTIAL_BEATS` parent beat
counter. In both cases, arbitration is locked for the complete packet.

The module name describes its hierarchy role rather than its physical topology;
the adapter implementation may later be replaced without changing the H1
compute node.

## 13. H1 tile

`h1_tile` integrates:

- `H0_COUNT` complete H0 tiles.
- One generic hierarchy node configured for all state blocks in the H1 tile.
- One H1 child adapter.
- Parent-partial completion tracking.
- Iteration sequencing and H1 state-table loading.

### 13.1 H1 state table

The H1 state-table index is flattened as:

```text
state_index = h0_index*CORES_PER_H0 + core_index
```

The tile sequentially copies each child's frozen state into the H1 hierarchy
node. With default parameters, this requires 512 accepted state-load cycles.
This loading occurs while child H0s can perform their local computation.

### 13.2 H1 iteration sequence

1. The shared `iter_start` begins all H0 tiles and their spin cores.
2. The H1 tile copies all current state blocks into its hierarchy node.
3. The H1 node starts and accepts scheduled blocks crossing different H0s.
4. H1-generated results are routed to the appropriate H0 external stream.
5. Parent-generated results may arrive concurrently and use the same adapter.
6. When both the H1 node and parent level are done, every child H0 receives its
   external-partials-done indication.
7. Each H0 waits for its own local node and then releases its cores to finalize.
8. H1 `iter_done` is the AND reduction of all child H0 completion signals.
9. `commit` atomically advances the distributed state vector.

## 14. Ready/valid rules

All streaming transfers occur only when both valid and ready are high on a
clock edge.

Required producer behavior:

- Hold valid, payload, block ID, and any last indication stable while ready is
  low.
- Do not begin the next packet until all beats of the current packet are
  accepted on that stream.
- Issue `schedule_done` only after the final command has been accepted and its
  complete weight block has been supplied.
- Issue the hierarchy `partials_done` indication only after the final partial
  packet has been accepted into the downstream hierarchy path.

The current adapters provide backpressure but do not contain deep traffic
FIFOs. Double-buffering exists for weight blocks inside each hierarchy engine.
Additional command, weight, or partial FIFOs can be added at adapter and memory
boundaries without changing the arithmetic contract.

## 15. External scheduler and memory streamer

The current RTL exposes DMA-like command and weight ports but does not contain a
complete DRAM controller. A future schedule player/streamer must:

1. Read a compile-time-generated block schedule.
2. Determine which MVM engine should receive each block.
3. Wait for that engine's `dma_cmd_ready_o`.
4. Send state-table indices and global destination block IDs.
5. Fetch the associated 1,024-byte J block from external memory.
6. Stream 32 256-bit beats to the selected engine while respecting ready.
7. Skip blocks omitted by the sparse-aware compiler.
8. Assert `schedule_done_i` after all scheduled blocks have been transferred.

Engine count should be chosen to consume blocks at approximately the aggregate
memory delivery rate. Engines share the node's physical memory interface;
adding engines must not be interpreted as adding independent DRAM bandwidth.

## 16. Parent network integration

The H1 tile currently exposes one topology-neutral parent partial stream. A
future mesh, ring, tree, or chiplet-link adapter should be placed outside the
tile and translate network packets into:

- `parent_partial_valid_i`
- `parent_partial_ready_o`
- `parent_partial_block_id_i`
- `parent_partial_data_i`
- `parent_partials_done_i`

The network packet header must carry enough information to recover the global
destination block ID and iteration ownership. Epoch checking may be performed
in the network adapter or global controller; the arithmetic endpoint does not
currently carry an epoch field.

## 17. Static scheduling and sparsity

The hardware datapath is deliberately schedule-driven. Compile-time software
is responsible for:

- Partitioning spins among cores and hierarchy nodes.
- Mapping global block IDs to physical endpoints.
- Assigning every nonempty interaction block exactly once.
- Balancing block ownership across MVM engines and higher-level nodes.
- Ordering transfers to reduce output contention and memory-bank conflicts.
- Omitting completely zero blocks.
- Padding partially sparse blocks with zero-valued weights.

The RTL remains dynamic at the flow-control level: ready/valid backpressure and
adapter arbitration handle timing variation. “Static schedule” therefore
means computation ownership and intended issue order are known at compile time,
not that every transfer must occur in a fixed cycle slot.

## 18. Top-level NoC implementation

The top-level network is divided into `noc_router`, `cross_h1_node`,
`top_node`, `h1_noc_adapter`, `mesh_h1_tile`, and `ising_mesh`.

### 18.1 Packet format

Every link transfers a 256-bit payload with ready/valid and the following
sideband metadata:

```text
type | destination X | destination Y | source ID | epoch | block ID | last
```

Defined packet types are `NOC_STATE`, `NOC_PARTIAL`, and `NOC_EPOCH_DONE`.
Metadata is repeated with every flit. A transfer occurs only on valid and ready.
The producer must hold the complete flit stable while stalled.

### 18.2 NoC router

`noc_router` has five ports: local, north, south, east, and west. Every input
owns a parameterizable FIFO, defaulting to eight flits. Routing is deterministic
XY: X displacement is resolved before Y displacement, and a matching coordinate
is delivered locally. Increasing Y travels south and decreasing Y travels
north.

Every output uses round-robin arbitration. Once a non-tail flit is accepted,
the output locks to that input until an accepted flit asserts `last`. This
prevents packets from being interleaved while allowing different outputs to
operate concurrently.

### 18.3 Cross-H1 node

`cross_h1_node` wraps a generic hierarchy node. Incoming single-flit STATE
packets write one 32-bit state vector into a table indexed by global block ID.
The external schedule/weight streamer supplies blocks assigned to this top
compute node. Its symmetric partial results are arbitrated from the independent
MVM streams onto one NoC stream with packet locking.

The destination H1 is derived from:

```text
destination_h1 = global_block_id / BLOCKS_PER_H1
destination_x  = destination_h1 % MESH_X_COUNT
destination_y  = destination_h1 / MESH_X_COUNT
```

### 18.4 Top node

`top_node` combines one router and one cross-H1 compute endpoint at a mesh
coordinate. Its four external link indices are north, south, east, and west.
The router local injection port is shared between cross-H1 partial traffic and
the co-located H1 endpoint; arbitration locks for the complete packet.

Locally ejected STATE packets populate the cross-H1 state table. PARTIAL and
EPOCH_DONE packets are passed to the co-located H1 endpoint. The current top
node exposes this H1 endpoint as a packet stream rather than directly
instantiating `h1_tile`, keeping network integration separate from the large
compute hierarchy.

### 18.5 H1 NoC adapter

`h1_noc_adapter` connects the topology-neutral parent interface of an H1 tile
to the local H1 packet endpoint exposed by `top_node`. State publication
selection remains external: a testbench or future schedule player supplies a
32-bit state, its global block ID, and destination mesh coordinate. The adapter
encodes this as a single-flit `NOC_STATE` packet.

In the receive direction, matching-epoch `NOC_PARTIAL` flits are passed to the
H1 tile's `parent_partial_*` interface with ready/valid backpressure intact. An
accepted matching-epoch `NOC_EPOCH_DONE` tail produces a one-cycle
`parent_partials_done` pulse. Stale-epoch and unsupported packet types are
consumed without modifying the H1 tile.

The adapter does not select publication destinations, count remote producers,
or generate completion packets. Those responsibilities remain in testbench
stimulus for the current performance model and can later be implemented by a
schedule player/global iteration controller.

### 18.6 Integrated mesh tile and mesh

`mesh_h1_tile` connects one complete `h1_tile`, one `h1_noc_adapter`, and one
`top_node`. A testbench selects a local H1 state-table index and destination;
the wrapper reads that state directly from `state_current_o` and presents it to
the adapter. H0, H1, and cross-H1 command/weight ports remain exposed.

`ising_mesh` instantiates a parameterized `MESH_X_COUNT` by `MESH_Y_COUNT`
array of these integrated locations. East/west and north/south ready/valid
links are connected directly between neighbors. Boundary inputs are inactive
and boundary outputs are ready; valid traffic should never reach a boundary
output under correct XY routing. All schedule, memory, publication, lifecycle,
and state ports have a leading node dimension for testbench control.

For the current testbench-controlled barrier, the adapter can also inject a
single-flit `NOC_EPOCH_DONE` packet to an explicitly selected destination.
This is transport support only: the testbench remains responsible for issuing
completion after all relevant cross-H1 work and data traffic have drained.

## 19. Verification status

The repository contains independent testbenches for:

- `spin_core`
- `hierarchy_node`
- `h0_adapter`
- `h0_tile`
- `h1_tile`
- `noc_router`
- `top_node`
- `h1_noc_adapter`

The H1 test uses a reduced two-H0 configuration. It streams a known all-ones
cross-H0 J block, verifies both directions of the symmetric computation, routes
the resulting partials into different child H0s, and checks the resulting spin
states.

The router test verifies XY direction selection, local delivery, output
contention, and packet locking. The top-node test receives state packets through
the router, performs a known cross-H1 symmetric block evaluation, delivers one
partial locally, and routes the other partial to the east link.

These tests validate functional composition. They are not yet substitutes for:

- Randomized protocol tests with backpressure on every interface.
- Multiple simultaneous engine collisions.
- Parent and H1 traffic colliding at the same H0.
- Multiple iterations and commit behavior.
- Sparse schedules containing omitted and partially zero blocks.
- Arithmetic overflow and coefficient-range tests.
- Formal checks that each accepted packet updates exactly 32 accumulator lanes.
- Full-system mesh and memory-controller verification.

## 20. Known implementation limitations and decisions still to freeze

The following items should be resolved before treating the interfaces as a
stable implementation specification:

1. **Noise scaling:** decide whether the core receives final
   `noise_amplitude`, or computes `coeff_c*noise_decay` internally. The current
   RTL uses only `noise_amplitude`.
2. **Accumulator arithmetic:** define whether coefficient multiplication may
   widen beyond `ACC_W`, whether results saturate or wrap, and the supported
   maximum problem size and weight sum.
3. **Completion protocol:** the current design uses level-completion pulses and
   fixed packet lengths. The system controller must guarantee completion cannot
   overtake data.
4. **Fairness:** adapters use fixed-priority arbitration. If starvation is
   possible under sustained traffic, replace it with round-robin arbitration.
5. **State loading bandwidth:** H0 and H1 state tables are filled sequentially.
   Banking or direct shared-state access may reduce iteration startup overhead.
6. **CDC:** all current modules assume one clock. Asynchronous memory or chiplet
   links require CDC FIFOs at those boundaries.
7. **Memory implementation:** inference-friendly SRAM wrappers must be mapped
   to actual technology macros and verified for read-during-write behavior.
8. **Completion generation:** the H1 NoC adapter consumes a matching-epoch
   completion packet, but the global controller or testbench must generate it
   only after every cross-H1 result has entered the destination network path.
9. **Schedule streamer:** DMA address generation, schedule storage, and DRAM
   command handling remain external to the current RTL.

## 21. File map

| File | Responsibility |
|---|---|
| `rtl/ising_pkg.sv` | Shared datapath parameters |
| `rtl/core.sv` | One 32-spin state and accumulation endpoint |
| `rtl/mvm.sv` | Core-resident normal-direction MVM |
| `rtl/symmetric_mvm.sv` | Bidirectional off-diagonal block evaluation |
| `rtl/j_block_sram.sv` | Double-buffered J-block storage |
| `rtl/hierarchy_node.sv` | Generic scheduled hierarchy compute node |
| `rtl/h0_adapter.sv` | H0-node result routing to spin cores |
| `rtl/h0_tile.sv` | Spin cores, H0 compute node, and H0 control integration |
| `rtl/h1_child_adapter.sv` | H1/parent result routing to child H0s |
| `rtl/h1_tile.sv` | H0 children, H1 compute node, and H1 control integration |
| `rtl/noc_router.sv` | Buffered five-port deterministic XY router |
| `rtl/cross_h1_node.sv` | State receiver and scheduled cross-H1 computation |
| `rtl/top_node.sv` | Router, cross-H1 endpoint, and local H1 packet interface |
| `rtl/h1_noc_adapter.sv` | Translation between H1 parent traffic and top-node packets |
| `rtl/mesh_h1_tile.sv` | Complete H1 hierarchy, NoC adapter, cross compute, and router location |
| `rtl/ising_mesh.sv` | Parameterized rectangular mesh with per-node testbench stimulus ports |
| `tb/*.sv` | Module and integration testbenches |
