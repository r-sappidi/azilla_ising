# C++ exact-event model

Standalone C++20 port of the Python timing-only exact-event model
(`azilla_cycle_model.exact_events`), one module per Python module. The
Python model is the oracle; this port is verified byte-for-byte against it and
against stored VCS traces. Full description, build, run and coverage:
[`docs/cpp_exact_model.md`](../../docs/cpp_exact_model.md).

Targets (same sources):

- `azilla_exact`: unified binary, `cir`, `cores-only` (streaming or
  buffered v3 endpoint policy), `flat-stationary` (weight-stationary
  state-exchange baseline) and `cir-h0-anchored` (symmetric fetch with
  endpoint-H0 ownership); the last two are C++ only and
  cycle-structured-unverified. Use this one.
- `azilla_exact_cir`, `azilla_exact_cir_dense`: the stage-1 CIR binaries kept
  so recorded evidence hashes stay reproducible.

```bash
cmake -S model/cpp_exact -B build/cpp_exact -DCMAKE_BUILD_TYPE=Release
cmake --build build/cpp_exact --target azilla_exact -j4
```

Runtime dependency: `build/cycle_model_ramulator/libazilla_ramulator.so`
(built in the top-level README, step 4), loaded with `dlopen`.

Optional `--dram-threads N` (default 1, serial): tick the independent Ramulator
DRAM systems on N threads; outputs are bit-identical for every N. Needs the
multi-thread-capable bridge `libazilla_ramulator_mt.so`
(`ramulator_bridge_mt.cpp`, target `azilla_ramulator_mt`, built on request):

```bash
cmake --build build/cpp_exact --target azilla_ramulator_mt -j4
build/cpp_exact/azilla_exact ... --ramulator-library build/cpp_exact/libazilla_ramulator_mt.so --dram-threads 8
```

See "Threaded DRAM ticking" in `docs/cpp_exact_model.md`.

Provisioning options shared by every mode (defaults reproduce the RTL-verified
configuration): `--ticks-per-cycle N` (Ramulator 250 ps ticks per accelerator
cycle; 40 = 10 ns/100 MHz default, 4 = 1 ns/1 GHz), `--max-outstanding N`
(32-byte weight requests in flight per H0 streamer, RTL `MAX_OUTSTANDING`,
default 64), `--mem-lanes N` (request/response lanes, default 16) and
`--ramulator-config` (GDDR6 projection `tb/ramulator_128x32.yaml`, HBM4
`tb/ramulator_hbm4_512x8.yaml`, and the `*_mop4.yaml` variants with the
MOP4CLXOR bank interleave). See "Clock ratio, front end and memory
technology" in `docs/cpp_exact_model.md`.
