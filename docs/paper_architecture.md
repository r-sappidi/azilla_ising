# 3. Azilla Architecture

Azilla is a distributed accelerator for sparse Ising iterations. Its central
design goal is to scale the state and coupling matrix beyond a single die or
package without forcing every iteration through a centralized matrix engine or
a global reduction. Azilla achieves this goal through three choices. First, it
partitions the Ising state into fixed 32-spin blocks, which are both the unit of
storage and the unit of computation. Second, it places computation at three
levels of a physical hierarchy so that an interaction is evaluated at the
smallest scope containing its endpoints. Third, it exposes vertex placement
and cross-package work ownership to software, allowing graph structure to
reduce communication rather than treating the coupling matrix as uniformly
dense.

The architecture overview figure should show the complete hierarchy and the
dataflow for one interaction: spin cores within H0 tiles, H0 tiles and local
engines within an H1 package, and H1 packages connected by a 2-D mesh. The
figure should distinguish state publication from returned partial vectors and
should place the memory interface beside each compute endpoint rather than
depicting a shared global memory.

## 3.1 Ising iteration and block decomposition

For an Ising state `s` containing values in `{−1, +1}` and signed coupling
matrix `J`, an iteration evaluates a local field for every spin and applies
the selected update rule. Azilla partitions both dimensions of `J` into 32×32
blocks. Core `i` owns the corresponding 32-element state block `s_i`. A
nonzero off-diagonal block `J_ij` defines one block job. Because the modeled
coupling matrix is symmetric, a single symmetric matrix–vector engine reads
`J_ij` once and produces contributions for both endpoints: `J_ij × s_j` and
`transpose(J_ij) × s_i`. Thus, each occupied unordered block pair is scheduled
exactly once.

The 32-spin block is immutable across the hierarchy. One block job contains
1,024 signed 8-bit weights, requires 32 row-serial arithmetic cycles, and
produces two 32-element signed partial vectors. The RTL transports these
results as eight 256-bit partial flits in total. Fixing this granularity avoids
repacking state and weights at hierarchy boundaries and gives the mapper a
stable unit for placement and scheduling.

Azilla represents sparsity at block granularity. A sparse workload schedules
only occupied off-diagonal blocks; missing blocks generate neither arithmetic
nor weight traffic. Diagonal and local update terms remain at the owning spin
core. This organization is especially effective when vertex placement clusters
interacting spins into the same core, H0 tile, or H1 package. Dense execution
is also supported by scheduling every off-diagonal block, but it is a stress
case rather than the principal target workload.

## 3.2 Three-level compute hierarchy

Azilla classifies every block job according to the lowest common ancestor of
its two state blocks. This classification determines both where the job may
execute and how far its operands and results travel.

**Spin core.** A spin core stores 32 state bits, accumulates incoming partial
vectors using fixed-width arithmetic, advances the per-block noise generator,
and commits the next state. State remains resident across iterations. The core
therefore communicates block state and partial sums rather than repeatedly
reading and writing a global spin vector.

**H0 tile.** An H0 tile groups multiple spin cores with a configurable set of
H0 MVM engines. An interaction whose endpoints reside under the same H0 is
executed locally. H0-local engines obtain both state blocks without using the
top-level network, read the associated weight block through the H0 memory
streamer, and return both partial vectors directly to their resident cores.

**H1 package.** An H1 groups multiple H0 tiles and adds H1-local MVM engines,
cross-H1 MVM engines, adapters, and a top-level router endpoint. An interaction
between different H0s in the same H1 is assigned to an H1-local engine. The
endpoint collects the two state blocks, streams the weights, and returns the
two results through the package-local hierarchy. An interaction spanning two
H1s is assigned to a cross-H1 engine at a software-selected owner H1. The two
source H1s publish their state blocks to that owner; after computation, the
owner sends one partial vector to each destination H1.

The architecture is parameterized by the mesh dimensions, H0s per H1, cores
per H0, and the number of engines at each hierarchy level. Its spin capacity
is:

`spin capacity = mesh width × mesh height × H0s per H1 × cores per H0 × 32`

The current million-spin capacity point uses a 4×4 H1 mesh, eight H0s per H1,
and 256 cores per H0, yielding 1,048,576 resident spins. The evaluated
provisioning candidate instantiates four MVM engines per H0, two H1-local
engines per H1, and four cross-H1 engines per H1.
These counts are design parameters rather than workload-independent optima.

## 3.3 Endpoint organization and memory streaming

Each H0-local, H1-local, and cross-H1 compute endpoint has an independent
modeled memory interface. A descriptor identifies the endpoint blocks, weight
location, and destinations. A ready-aware round-robin dispatcher assigns a
descriptor to an available local engine; static mappings therefore select a
compute owner but do not encode engine IDs. This separation lets the runtime
absorb variable memory service and downstream backpressure without requiring
the mapper to predict the exact cycle on which an engine becomes free.

The memory streamer translates a block descriptor into 32 reads, reassembles
tagged responses that may return out of order, and presents complete rows to
the row-serial MVM. Command, weight, and result paths are double buffered so
that memory service, arithmetic, and result delivery can overlap across jobs.
Backpressure is local: a blocked streamer or result port prevents dispatch to
that resource but does not globally stop unrelated endpoints.

Adding engines does not proportionally increase memory bandwidth. Engines at
one endpoint share its modeled interface, and their partial results ultimately
share hierarchy and network links. Provisioning is consequently chosen at the
saturation knee of compute, memory, and communication rather than by maximizing
the number of arithmetic units. This balance is important for sparse graphs:
depending on placement, the limiting resource may be an H0 engine, a
cross-package owner, one memory endpoint, or a small set of mesh links.

## 3.4 Inter-H1 communication

H1 endpoints are connected by a rectangular 2-D mesh. Each router uses
deterministic XY routing, registered input FIFOs, wormhole packet locks,
ready/valid backpressure, and fair arbitration. Links are directional and
full-duplex. The network carries three architecturally distinct message
classes: state publications, partial-vector returns, and iteration-completion
notifications. State blocks are frozen for the duration of an iteration, so a
source publishes a required block at most once to a given remote compute owner;
multiple cross-H1 jobs at that owner reuse the snapshot.

Communication cost depends on both interaction placement and compute
ownership. Assigning a cross-H1 block to one of its endpoint packages avoids
moving one operand but can concentrate work and returned traffic. Assigning it
to a third package may improve engine balance while increasing hop count.
Azilla therefore treats the owner as a software decision and records injected
flits, physical link traversals, hop distribution, link utilization, and
backpressure for each mapping. These counters distinguish a placement that
genuinely removes communication from one that merely moves congestion to a
different link or endpoint.

The implemented RTL directly connects adjacent H1 routers. In the projected
physical organization, one H1 corresponds to a package containing H0 compute
chiplets, H1-local and cross-H1 compute, routing, and attached GDDR-style
memory. Package-local and inter-package link latency, serialization, and credit
return must therefore be evaluated separately from the direct-wire RTL. These
parameters describe a system projection; they do not constitute a completed
chiplet package or physical-link implementation.

## 3.5 Mapping and scheduling interface

Azilla separates three decisions that are often conflated in sparse
accelerators. **Vertex placement** assigns problem vertices to 32-spin blocks
and hence to cores and hierarchy domains. **Cross-job ownership** chooses the
H1 that computes each cross-package block. **Engine dispatch** dynamically
selects a ready engine at that owner. Local jobs are not freely movable: an
H0-local or H1-local interaction executes at its resident hierarchy resource.
Only cross-H1 ownership is programmable.

The mapper communicates with the architecture through a versioned artifact
containing a vertex permutation, its inverse, unique occupied unordered block
pairs, cross-H1 owners, and a textual schedule. The permutation convention is
`permutation[new vertex] = old vertex`. Functional
runs consume the correspondingly permuted dataset; pairing mapped block
coordinates with the original vertex ordering would change the represented
Ising problem. Before execution, the runtime validates permutation consistency,
block uniqueness, schedule completeness, legal locality, and the presence of
an owner for every cross-H1 job.

This interface allows graph partitioners and owner allocators to evolve
without changing the RTL or timing model. More importantly, it enables
controlled experiments that attribute an improvement to reduced block count,
improved hierarchy locality, balanced cross-job ownership, or dynamic engine
utilization.

## 3.6 Iteration protocol

An iteration proceeds in four logical phases. First, each H1 snapshots its
resident state and publishes blocks required by scheduled remote owners.
Second, H0-local, H1-local, and cross-H1 descriptors execute concurrently as
their operands, memory interfaces, and engines become ready. Third, partial
vectors return to the destination cores and accumulate into per-spin fields.
Finally, completion notifications establish that all scheduled work and
network traffic have drained; each core applies the local update rule and
commits its next state. The next iteration cannot observe a mixture of old and
new state.

This protocol avoids a centralized barrier controller or global reduction.
Synchronization follows the same hierarchy as computation, while explicit
completion packets make the tail effects of load imbalance and network
backpressure visible. The resulting execution time is determined by the
critical endpoint and communication path, not simply by the total number of
occupied blocks.

## 3.7 Scalability properties

Azilla can increase capacity by adding H1 packages or by placing more H0s and
cores behind each package. These choices have different consequences. Adding
H1s increases aggregate compute and memory interfaces but may convert local
interactions into network traffic. Increasing cores within a fixed mesh raises
work per package without increasing top-level bisection bandwidth. Similarly,
adding MVM engines cannot overcome a saturated memory interface or congested
link and may worsen burst synchronization.

The architecture therefore does not assume that spin count alone determines
the correct configuration. Software placement determines the counts and
spatial distribution of H0-local, H1-local, and cross-H1 jobs; hardware
provisioning determines the service capacity at each level. Azilla's
co-design loop uses those mapped job counts together with endpoint memory and
NoC counters to select candidate geometries, then evaluates finalists with the
full hierarchy and memory timing. This combination of fixed block semantics,
hierarchical execution, distributed state, and explicit mapping control is the
mechanism by which Azilla targets sparse Ising problems at million-spin
capacity.
