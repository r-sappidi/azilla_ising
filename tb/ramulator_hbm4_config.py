"""Exportable HBM4 Ramulator configuration for one weight-owning node.

Companion to ``ramulator_config.py`` (board GDDR6 projection).  Models the
slice of co-packaged HBM4 one H0 would own when two HBM4 stacks serve the
eight H0 of an H1: 32 channels x 2 stacks / 8 H0 = 8 x 64-bit channels per
H0, each with two 32-bit pseudo channels.  At the JEDEC 8 Gb/s preset that is
512 data pins x 8 Gb/s = 512 GB/s per node, the same peak as the 128-pin
32 Gb/s GDDR6 projection, so a swap isolates memory-technology effects
(latency, bank structure, refresh) from raw bandwidth.

Environment variables:
  AZILLA_HBM_CHANNELS   64-bit channels per node (default 8)
  AZILLA_HBM_TIMING     timing preset (default HBM4_8000Mbps; HBM4_16000Mbps
                        doubles the pin rate)
  AZILLA_ADDR_MAPPER    RoBaRaCoCh (default, as the GDDR6 projection) or
                        MOP4CLXOR (128-byte bank interleave with row/column
                        XOR; needed for a sequential 32-byte weight stream to
                        approach channel peak when the accelerator cycle is
                        only 4 ticks)

Ramulator's HBM4 model ticks its controller at twice the 500 ps command
clock (tick_multiplier 2), so the exported 8000 Mbps timing has tCK = 250 ps
and a 10 ns accelerator cycle stays 40 ticks, the same ratio as the GDDR6
projection.  The 16000 Mbps preset exports at 125 ps (80 ticks per cycle).
Export with
  PYTHONPATH=third_party/ramulator2/python python3 -m ramulator export \
      tb/ramulator_hbm4_config.py -o tb/ramulator_hbm4_512x8.yaml
"""

import os

import ramulator


CHANNEL_COUNT = int(os.environ.get("AZILLA_HBM_CHANNELS", "8"))
TIMING_PRESET = os.environ.get("AZILLA_HBM_TIMING", "HBM4_8000Mbps")
ADDR_MAPPER = os.environ.get("AZILLA_ADDR_MAPPER", "RoBaRaCoCh")

if CHANNEL_COUNT <= 0:
    raise ValueError("AZILLA_HBM_CHANNELS must be positive")


def make_controller():
    dram = ramulator.dram.HBM4(org_preset="HBM4_32Gb_4Hi", timing_preset=TIMING_PRESET)
    return ramulator.controller.GenericDDR(
        dram=dram,
        scheduler=ramulator.scheduler.FRFCFS(),
        refresh_manager=ramulator.refresh_manager.AllBank(),
        row_policy=ramulator.row_policy.Open(),
        addr_mapper=getattr(ramulator.addr_mapper, ADDR_MAPPER)(),
    )


frontend = ramulator.frontend.External(clock_ratio=1)
memory_system = ramulator.memory_system.GenericDRAM(
    clock_ratio=1,
    controllers=[make_controller() for _ in range(CHANNEL_COUNT)],
    channel_mapper=ramulator.channel_mapper.CacheLineInterleave(),
)

sim = ramulator.Simulation(frontend, memory_system)
