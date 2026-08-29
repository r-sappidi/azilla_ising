# Azilla Ising accelerator research model

This repository contains the Azilla RTL, FlooNoC integration, Ramulator-backed
memory timing, cycle-stepped Python model, accelerated exact-event timing
model, and sparse graph-mapping interface.

The commands below assume a Linux machine and are run from the repository
root. The current reference environment uses Ubuntu 24.04, Python 3.13,
Verilator 5.020, CMake 3.28, and a C++20 compiler. Python 3.10 or newer and
CMake 3.14 or newer are required by the code and Ramulator2.

## 1. Install system tools

On Ubuntu or Debian:

```bash
sudo apt update
sudo apt install -y \
  git build-essential cmake make verilator \
  python3 python3-pip python3-venv
```

GNU Octave is optional and is used only for the independent MATLAB-compatible
arithmetic audit:

```bash
sudo apt install -y octave
```

## 2. Clone the repository

```bash
git clone --recurse-submodules https://github.com/r-sappidi/azilla_ising.git
cd azilla_ising
git submodule update --init --recursive
```

Fetch the pinned external RTL dependencies used by FlooNoC tests:

```bash
scripts/fetch_floo_deps.sh
```

## 3. Create the Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The single `requirements.txt` installs NumPy, PyTorch, and Matplotlib for the
complete Python toolset. A CUDA-enabled PyTorch build is strongly recommended
for million-spin mapping. On a CUDA machine, use the
[official PyTorch selector](https://pytorch.org/get-started/locally/) to install
the appropriate wheel before running `pip install -r requirements.txt`; pip
will retain that compatible installation. CPU PyTorch is sufficient for small
tests and for consuming an artifact produced elsewhere.

The Python package is currently used directly from the source tree, so prefix
commands with `PYTHONPATH=model` rather than installing it into the environment.

## 4. Build Ramulator2 and the Python timing bridge

The performance models use Ramulator's C++ shared library; they do not require
Ramulator's Python bindings. Disabling those bindings also avoids regenerating
source files inside the submodule:

```bash
cmake -S third_party/ramulator2 -B third_party/ramulator2/build \
  -DCMAKE_BUILD_TYPE=Release \
  -DRAMULATOR_PYTHON_BINDINGS=OFF
cmake --build third_party/ramulator2/build -j
make -C tb cycle-model-ramulator-library
```

This produces:

```text
third_party/ramulator2/libramulator.so
build/cycle_model_ramulator/libazilla_ramulator.so
```

The checked-in `tb/ramulator_128x32.yaml` is sufficient for the Python and RTL
timing workflows. Regenerating that YAML through `tb/ramulator_config.py`
requires a separate Ramulator build with its Python bindings enabled.

## 5. Verify the installation

Run the Python unit suite:

```bash
PYTHONPATH=model python -m unittest discover -s model/tests -v
```

Run the component RTL differential tests, rebuilding their Verilator models:

```bash
python scripts/check_cycle_model.py --rebuild
```

Run a small Ramulator-backed exact-event timing smoke test:

```bash
PYTHONPATH=model python -m azilla_cycle_model.cli simulate-exact-events \
  --dataset tb/datasets/g256_smoke.txt \
  --mesh-x 2 --mesh-y 1 --h0-per-h1 2 --cores-per-h0 2 \
  --h0-mvms 1 --h1-mvms 1 --cross-mvms 1 \
  --ramulator-library build/cycle_model_ramulator/libazilla_ramulator.so \
  --ramulator-config tb/ramulator_128x32.yaml \
  --idle-refresh-period-ticks 7600
```

The certified smoke case reports 267 initialization cycles, 179 iteration
cycles, and 446 total cycles with `accuracy=rtl-differential`.

## Mapping experiments

See [MAPPING_EXPERIMENTS.md](MAPPING_EXPERIMENTS.md) for the mapper artifact
format, built-in and external mapping interfaces, validation, fast screening,
and exact-event performance testing. A typical experiment creates a mapping
artifact and consistently permuted dataset, validates their schedule, screens
many candidates with `simulate-mapped-events`, and runs finalists with
`simulate-mapped-exact-events`.

## RTL workflows and generated files

See [tb/README.md](tb/README.md) for RTL testbench parameters, full hierarchy
runs, Ramulator-backed RTL simulation, and NoC trace analysis. Build products,
waveforms, mapping artifacts, logs, and run-specific CSV files are ignored by
Git and should be written under `build/`, `logs/`, `mappings/`, or `results/`.

If a dataset header does not equal
`mesh_x * mesh_y * h0_per_h1 * cores_per_h0 * 32`, the model will reject the
configuration. A mapped schedule must always be paired with the dataset to
which the same vertex permutation has been applied.

## Troubleshooting

- `ModuleNotFoundError: azilla_cycle_model`: run from the repository root and
  include `PYTHONPATH=model`.
- `libramulator.so` cannot be loaded: rebuild Ramulator and the timing bridge
  using step 4; do not move the submodule afterward without rebuilding.
- Missing FlooNoC includes during RTL compilation: rerun
  `scripts/fetch_floo_deps.sh`.
- A CUDA mapping request selects CPU: verify `python -c "import torch; print(torch.cuda.is_available())"`
  and reinstall the correct PyTorch/CUDA wheel if it prints `False`.
- A pull is blocked by generated files: preserve real work with
  `git stash push -u`, remove regenerable caches, then retry the pull. Do not
  force-push or discard unreviewed RTL changes.
