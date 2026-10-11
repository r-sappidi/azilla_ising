# 3. Architecture Overview

Azilla is a hierarchical accelerator for sparse Ising iterations. Its datapath
implements a programmable synchronous stochastic Ising update: it multiplies a
signed coupling matrix by a binary spin state, accumulates local fields, adds
controller-selected inertia and pseudo-random perturbation terms, and updates
all spins between iterations. It distributes state, coupling storage,
matrix–vector computation, and communication across a parameterized hierarchy
while exposing graph placement and work assignment to software.

For every spin, the implemented update combines three terms: the signed
current spin scaled by coefficient A, the accumulated coupling sum scaled by
coefficient B, and a programmable noise amplitude. Each spin core holds a
32-bit Fibonacci LFSR with polynomial `x^32 + x^22 + x^2 + x + 1`. The LFSR
advances once per iteration, and its 32 bits select positive or negative noise
for the core’s 32 spins. A nonnegative resulting field maps to `+1`; a negative
field maps to `−1`. All cores commit their new states synchronously only after
the iteration’s partial contributions have arrived.

Azilla partitions the spin vector into **state blocks** of 32 spins. Each state
block is permanently assigned to a fixed-function **spin core**, which stores
state, accumulates partials, and applies the update rule. The same partitioning
divides the coupling matrix into 32×32 **coupling blocks**. A coupling block
with a nonzero interaction is occupied and defines one **block job**. Empty
coupling
blocks are omitted in sparse execution, so their weights are neither fetched
nor processed.

The coupling matrix is symmetric. Consequently, Azilla schedules each occupied
off-diagonal coupling block only once rather than independently processing both
halves of the matrix. A **symmetric block-MVM engine** reads one 32×32 block of
signed 8-bit weights and the two associated state blocks. It produces one
partial vector for each endpoint: the contribution of the second state block
to the first and the transposed contribution of the first state block to the
second. One block job therefore performs useful work for both participating
spin cores while reading the coupling weights once. The implemented engine is
row-serial, consumes 32 arithmetic cycles per block, and returns two
32-element, 32-bit partial vectors.

The hardware organizes block jobs into three locality domains. Multiple spin
cores form an **H0 tile**, and multiple H0 tiles form an **H1 domain**. H1
domains connect through a rectangular 2-D mesh. A block job whose state blocks
reside in one H0 executes on an H0-local engine. A job spanning two H0 tiles
within one H1 executes on an H1-local engine. A job spanning H1 domains
executes on a cross-H1 engine attached to a software-selected H1 **compute
owner**. This lowest-common-ancestor placement keeps local interactions off the
global network while retaining a distributed execution path for arbitrary
graph edges.

Cross-H1 execution separates state movement from weight computation. At each
iteration, source H1 domains publish required 32-spin state blocks to the
selected compute owners, where frozen snapshots can be reused by multiple
block jobs. A cross-H1 engine returns one partial vector to each state-owning
H1. The network therefore carries state publications, partial-vector returns,
and completion notifications—not weights or global accumulator updates.
Routers use deterministic XY routing, registered input queues, wormhole packet
locking, fair arbitration, and ready/valid backpressure.

Each compute endpoint has a modeled memory interface shared by its engines. A
ready-aware dispatcher assigns block jobs to available engines, while a tagged,
double-buffered streamer fetches and reorders weights. Compute, memory, and
network bandwidth are independent provisioning dimensions; adding engines
cannot overcome a saturated memory interface or mesh link.

Software controls **vertex placement**, which determines hierarchy locality,
and **cross-job ownership**, which selects the H1 that executes each cross-H1
interaction. Engine selection remains dynamic in hardware. The mapper supplies
a validated vertex permutation, occupied coupling-block pairs, owners, and
schedule. Placement reduces block count and communication, while ownership
balances work and traffic across the mesh.

An iteration completes only after all local and remote block jobs have
finished, their partial vectors have reached the owning spin cores, and the
network has drained. Spin cores then apply the update rule and atomically
commit the next state, preventing an iteration from mixing old and new spins.

The hierarchy is fully parameterized by the mesh dimensions, H0 tiles per H1,
cores per H0, and engine counts at each compute level. Its resident spin
capacity is the product of the H1 count, H0s per H1, cores per H0, and 32 spins
per core. In the implemented RTL, an H1 is a logical hierarchy and routing
domain. Our physical scaling model interprets each H1 as one package containing
H0 compute chiplets, H1-local and cross-H1 computation, routing, and attached
memory. The inter-package mesh and nonzero link costs are system projections,
not a completed physical package design. Scalability therefore rests on sparse
placement, hierarchical scheduling, distributed memory, and explicit
communication—not spin capacity or MVM count alone.
