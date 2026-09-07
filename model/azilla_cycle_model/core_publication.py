"""Explicit cores-only frozen-state publication, preserving live router state.

Contract: one resident word/H1/cycle is gathered, remote single-flit states
are released serially every two cycles, then one filtered H0 cache write per
H1/cycle. No multicast or free globally replicated operand cache is assumed.
The independent diagonal MVM may overlap this phase. Off-diagonal reads must
wait for ``end_cycle``. This helper does not reset a supplied quiescent mesh.
"""
from dataclasses import dataclass
from .core_state_publication import core_state_publication_plan
from .events import NetworkReplayResult
from .noc import Mesh, Flit, LOCAL, NORTH, SOUTH, EAST, WEST


@dataclass
class CorePublicationResult:
    start_cycle: int
    end_cycle: int
    gather_cycles: int
    remote_cycles: int
    local_fill_cycles: int
    gathered_words: int
    cache_write_words: int
    network: NetworkReplayResult
    cache_contents: tuple
    mesh: Mesh

    @property
    def elapsed_cycles(self):
        return self.end_cycle - self.start_cycle


def run_core_state_publication(geometry, records, *, states=None, epoch=0,
                               start_cycle=0, mesh=None, fifo_depth=4,
                               interconnect=None, observer=None,
                               local_observer=None, resource_observer=None):
    """Run a publication phase; observers receive absolute, pre-edge cycles.

    ``states`` maps global block IDs to 32-bit frozen words (None: timing-only
    zero payloads). Local observer signature is (cycle,event,h1,global_h0,
    block,data), with global_h0=-1 for gather. No RTL timestamps are inputs.
    Network observer follows EventCompressedMesh's accepted-transfer API.
    """
    g = geometry
    plan = core_state_publication_plan(g, records)
    mesh = mesh if mesh is not None else Mesh(g.mesh_x, g.mesh_y, fifo_depth, interconnect)
    def empty():
        return (not mesh.has_link_data and
                not any(f.queue for r in mesh.routers for f in r.fifos))
    if not empty():
        raise ValueError("publication requires a quiescent mesh, not a reset mesh")
    tables = [dict() for _ in range(g.node_count)]
    for offset in range(plan.gather_cycles):
        for node in range(g.node_count):
            block = node * g.blocks_per_h1 + offset
            data = 0 if states is None else int(states[block]) & 0xffffffff
            tables[node][block] = data
            if local_observer:
                local_observer(start_cycle+offset, "gather", node, -1, block, data)
    mesh.advance_idle(plan.gather_cycles)
    net_start = now = start_cycle + plan.gather_cycles
    releases = list(enumerate(plan.remote_publications))
    queues = [[] for _ in range(g.node_count)]
    next_release = quiet = injected = ejected = links = inj_stalls = link_stalls = 0
    names = {NORTH:"north", SOUTH:"south", EAST:"east", WEST:"west"}
    while next_release < len(releases) or any(queues) or not empty() or quiet < 4:
        while next_release < len(releases) and 2*next_release <= now-net_start:
            _, (block, destination) = releases[next_release]
            source = block // g.blocks_per_h1
            queues[source].append(Flit(data=tables[source][block], packet_type=0,
                dest_x=destination % g.mesh_x, dest_y=destination // g.mesh_x,
                source_id=source, epoch=epoch, block_id=block, last=True))
            next_release += 1
        comb = [r.outputs() for r in mesh.routers]
        injections = {n:q[0] for n,q in enumerate(queues) if q}
        activity = bool(injections)
        for node, flit in injections.items():
            accepted = comb[node].input_ready[LOCAL]
            if resource_observer: resource_observer(now,"inject",node,"local",flit,accepted)
            if accepted:
                injected += 1
                queues[node].pop(0)
                if observer: observer(now,"inject",node,"local",flit)
            else: inj_stalls += 1
        for node, output in enumerate(comb):
            flit = output.output_flits[LOCAL]
            if flit is not None:
                activity = True
                ejected += 1
                tables[node][flit.block_id] = flit.data
                if observer: observer(now,"eject",node,"local",flit)
                if resource_observer: resource_observer(now,"eject",node,"local",flit,True)
            for port, name in names.items():
                flit = output.output_flits[port]
                if flit is None: continue
                activity = True
                accepted = mesh.link_ready(node,port,comb)
                if resource_observer: resource_observer(now,"link",node,name,flit,accepted)
                if accepted:
                    links += 1
                    if observer: observer(now,"link",node,name,flit)
                else: link_stalls += 1
        mesh.tick(injections,{n:True for n in range(g.node_count)})
        now += 1
        quiet = quiet+1 if next_release == len(releases) and not any(queues) and empty() and not activity else 0
    network = NetworkReplayResult(net_start,now,now-net_start,now-net_start,0,
                                  injected,ejected,links,inj_stalls,link_stalls)
    caches = [dict() for _ in range(g.node_count*g.h0_per_h1)]
    words = 0
    for offset in range(plan.local_fill_cycles):
        for node,writes in enumerate(plan.local_writes_by_h1):
            if offset >= len(writes): continue
            local_h0,block = writes[offset]
            h0 = node*g.h0_per_h1+local_h0
            data = tables[node][block]  # missing publication is a hard error
            caches[h0][block] = data
            words += 1
            if local_observer: local_observer(now+offset,"fill",node,h0,block,data)
    mesh.advance_idle(plan.local_fill_cycles)
    return CorePublicationResult(start_cycle, now+plan.local_fill_cycles,
        plan.gather_cycles,network.elapsed_cycles,plan.local_fill_cycles,
        plan.gather_cycles*g.node_count,words,network,tuple(caches),mesh)
