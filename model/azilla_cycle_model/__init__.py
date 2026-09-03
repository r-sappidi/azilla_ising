"""Cycle-stepped executable model of the Azilla Ising accelerator RTL.

The model deliberately exposes registered state and ready/valid transfers.  A
call to ``tick`` represents one rising clock edge; combinational outputs are
read from ``outputs`` immediately before that edge.
"""

from .config import ArchitectureConfig
from .compute import MVM, SpinCore, SymmetricMVM, SynchronousBlockSram
from .noc import Flit, FlooRouter, InterconnectConfig, Mesh
from .hierarchy import DmaCommand, HierarchyNode, PartialOutput
from .memory import DramWeightStreamer, MemoryRequest, MemoryResponse
from .adapters import H0Adapter, H1ChildAdapter, H1NocAdapter
from .tiles import H0Tile, H1Tile
from .cross import CrossH1Node, LocalInjectionArbiter
from .ramulator import RamulatorBackend, RamulatorSystemStats
from .workload import BlockOccupancyDataset, Geometry, IsingDataset, ScheduledBlock
from .system import IsingMeshSystem
from .scheduler import (
    CompiledSchedule, ConcurrentDispatcher, DirectDispatcher, DispatchPort,
    WorkItem, WorkTarget, allocate_h1_pairs, compile_schedule,
)
from .performance import (
    DirectPerformanceModel, RamulatorPerformanceModel, IterationResult, PerformanceConfig,
    PerformanceCounters, SimulationResult,
)
from .events import (
    EventCompressedMesh, EventCompressedPerformanceModel,
    EventNodeStats, EventNocResourceStats, EventPerformanceConfig,
    EventPerformanceResult, EventTimingProfile, EventWorkResourceStats,
    EventLoop, NetworkReplayResult, PacketRelease,
)
from .exact_events import (
    DramPerformanceStats, ExactEventResult, NocResourceStats,
    NodePerformanceStats, RamulatorEventPerformanceModel,
)
from .metrics import (
    write_event_performance_metrics, write_exact_event_metrics,
    write_transfer_trace,
)
from .mapping_adapter import (
    MappingArtifact, artifact_from_mapping, assign_cross_owners,
    load_sparse_dataset_arrays, map_sparse_dataset, write_permuted_dataset,
)

__all__ = [
    "ArchitectureConfig",
    "Flit",
    "FlooRouter",
    "InterconnectConfig",
    "DmaCommand",
    "HierarchyNode",
    "H0Adapter",
    "H1ChildAdapter",
    "H1NocAdapter",
    "H0Tile",
    "H1Tile",
    "CrossH1Node",
    "LocalInjectionArbiter",
    "RamulatorBackend",
    "RamulatorSystemStats",
    "Geometry",
    "IsingDataset",
    "BlockOccupancyDataset",
    "ScheduledBlock",
    "IsingMeshSystem",
    "DramWeightStreamer",
    "MemoryRequest",
    "MemoryResponse",
    "MVM",
    "Mesh",
    "SpinCore",
    "PartialOutput",
    "SymmetricMVM",
    "SynchronousBlockSram",
    "CompiledSchedule",
    "ConcurrentDispatcher",
    "DirectDispatcher",
    "DispatchPort",
    "WorkItem",
    "WorkTarget",
    "allocate_h1_pairs",
    "compile_schedule",
    "DirectPerformanceModel",
    "RamulatorPerformanceModel",
    "IterationResult",
    "PerformanceConfig",
    "PerformanceCounters",
    "SimulationResult",
    "EventCompressedMesh",
    "EventCompressedPerformanceModel",
    "EventPerformanceConfig",
    "EventPerformanceResult",
    "EventNocResourceStats",
    "EventNodeStats",
    "EventWorkResourceStats",
    "EventTimingProfile",
    "EventLoop",
    "NetworkReplayResult",
    "PacketRelease",
    "ExactEventResult",
    "NocResourceStats",
    "NodePerformanceStats",
    "DramPerformanceStats",
    "RamulatorEventPerformanceModel",
    "write_exact_event_metrics",
    "write_event_performance_metrics",
    "write_transfer_trace",
    "MappingArtifact",
    "artifact_from_mapping",
    "assign_cross_owners",
    "load_sparse_dataset_arrays",
    "map_sparse_dataset",
    "write_permuted_dataset",
]
