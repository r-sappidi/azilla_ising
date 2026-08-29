"""ctypes binding to the exact Ramulator2 backend used by the RTL testbench."""

from __future__ import annotations

import ctypes
from pathlib import Path

from .memory import MemoryResponse


class RamulatorBackend:
    """Use ``tb/ramulator_dpi.cpp`` without reimplementing DRAM timing.

    The C++ bridge owns process-global state, matching the DPI implementation;
    instantiate it once per Python process and call :meth:`finalize` when done.
    """

    def __init__(self, library: str | Path, config: str | Path,
                 dataset: str | Path, system_count: int, block_count: int,
                 *, timing_only: bool = False):
        self.library = ctypes.CDLL(str(Path(library).resolve()))
        init_name = "az_dram_init_timing" if timing_only else "az_dram_init"
        init = getattr(self.library, init_name)
        init.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int
        ]
        init.restype = None
        self.library.az_dram_send.argtypes = [
            ctypes.c_int, ctypes.c_uint64, ctypes.c_int
        ]
        self.library.az_dram_send.restype = ctypes.c_int
        self.library.az_dram_tick.argtypes = [ctypes.c_int]
        self.library.az_dram_tick.restype = None
        self.library.az_dram_pop.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_uint64),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self.library.az_dram_pop.restype = ctypes.c_int
        self.library.az_dram_report.argtypes = []
        self.library.az_dram_finalize.argtypes = []
        init(
            str(Path(config).resolve()).encode(),
            str(Path(dataset).resolve()).encode(),
            system_count,
            block_count,
        )
        self.system_count = system_count
        self.closed = False

    def send(self, system: int, address: int, tag: int) -> bool:
        return bool(self.library.az_dram_send(system, address, tag))

    def tick(self, count: int) -> None:
        self.library.az_dram_tick(count)

    def pop(self, system: int) -> MemoryResponse | None:
        address = ctypes.c_uint64()
        tag = ctypes.c_int()
        words = (ctypes.c_uint32 * 8)()
        if not self.library.az_dram_pop(
            system, ctypes.byref(address), ctypes.byref(tag), words
        ):
            return None
        data = sum(int(word) << (32 * index) for index, word in enumerate(words))
        return MemoryResponse(data=data, tag=tag.value)

    def report(self) -> None:
        self.library.az_dram_report()

    def finalize(self) -> None:
        if not self.closed:
            self.library.az_dram_finalize()
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.finalize()
        return False
