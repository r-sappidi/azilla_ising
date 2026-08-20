"""Exportable Ramulator configuration for the Azilla RTL testbench.

Environment variables:
  AZILLA_MEM_PINS       aggregate data pins per hierarchy node (default 128)
  AZILLA_MEM_PIN_GBPS   projected per-pin transfer rate (default 32)

Ramulator's GDDR6 organization is 16 bits wide with a 16n prefetch, so one
native transaction is 32 bytes.  Pin count must therefore be a multiple of 16
and is represented by that many independent controllers/channels.
"""

import math
import os

import ramulator
from ramulator.dram.gddr6 import GDDR6


PIN_COUNT = int(os.environ.get("AZILLA_MEM_PINS", "128"))
PIN_GBPS = float(os.environ.get("AZILLA_MEM_PIN_GBPS", "32"))
CHANNEL_WIDTH = 16

if PIN_COUNT <= 0 or PIN_COUNT % CHANNEL_WIDTH:
    raise ValueError("AZILLA_MEM_PINS must be a positive multiple of 16")
if PIN_GBPS <= 0:
    raise ValueError("AZILLA_MEM_PIN_GBPS must be positive")

channel_count = PIN_COUNT // CHANNEL_WIDTH

# The public GDDR6 preset is characterized at 14 Gb/s.  For the projected
# interface, retain its absolute-time latency constraints while deriving a new
# clock period.  Burst length and short column-to-column spacing remain the
# protocol minima needed to expose the requested data rate.  This is a
# technology-projection model, not a vendor-qualified 32-Gb/s GDDR6 device.
base = GDDR6.timing_presets["GDDR6_14000_1250mV_double"]
new_tck_ps = round(
    GDDR6.internal_prefetch_size * 1_000_000 /
    (base["nBL"] * PIN_GBPS * 1000)
)

do_not_scale = {"rate", "nBL", "nCCDS", "nPPD", "tCK_ps"}
timing_overrides = {
    name: math.ceil(value * base["tCK_ps"] / new_tck_ps)
    for name, value in base.items()
    if name not in do_not_scale
}
timing_overrides.update({
    "rate": round(PIN_GBPS * 1000),
    "nBL": base["nBL"],
    "nCCDS": base["nCCDS"],
    "nPPD": base["nPPD"],
    "tCK_ps": new_tck_ps,
})


def make_controller():
    dram = ramulator.dram.GDDR6(
        org_preset="GDDR6_8Gb_x16",
        timing_preset="GDDR6_14000_1250mV_double",
        **timing_overrides,
    )
    return ramulator.controller.GenericDDR(
        dram=dram,
        scheduler=ramulator.scheduler.FRFCFS(),
        refresh_manager=ramulator.refresh_manager.AllBank(),
        row_policy=ramulator.row_policy.Open(),
        addr_mapper=ramulator.addr_mapper.RoBaRaCoCh(),
    )


frontend = ramulator.frontend.External(clock_ratio=1)
memory_system = ramulator.memory_system.GenericDRAM(
    clock_ratio=1,
    controllers=[make_controller() for _ in range(channel_count)],
    channel_mapper=ramulator.channel_mapper.CacheLineInterleave(),
)

sim = ramulator.Simulation(frontend, memory_system)
