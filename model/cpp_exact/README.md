# C++ exact-event model

Standalone C++20 port of the Python timing-only exact-event model
(`azilla_cycle_model.exact_events`), one module per Python module. The
Python model is the oracle; this port is verified byte-for-byte against it and
against stored VCS traces. Full description, build, run and coverage:
[`docs/cpp_exact_model.md`](../../docs/cpp_exact_model.md).

Targets (same sources):

- `azilla_exact`: unified binary, `cir` and `cores-only` (streaming or
  buffered v3 endpoint policy). Use this one.
- `azilla_exact_cir`, `azilla_exact_cir_dense`: the stage-1 CIR binaries kept
  so recorded evidence hashes stay reproducible.

```bash
cmake -S model/cpp_exact -B build/cpp_exact -DCMAKE_BUILD_TYPE=Release
cmake --build build/cpp_exact --target azilla_exact -j4
```

Runtime dependency: `build/cycle_model_ramulator/libazilla_ramulator.so`
(built in the top-level README, step 4), loaded with `dlopen`.
