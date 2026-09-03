VERILATOR ?= verilator
VERILATOR_JOBS ?= 1
VERILATOR_OUTPUT_SPLIT ?= 0
VERILATOR_ROOT ?= $(shell $(VERILATOR) -V 2>/dev/null | strings | sed -n 's/^[[:space:]]*VERILATOR_ROOT[[:space:]]*=[[:space:]]*//p' | head -1)
VERILATOR_INCLUDE ?= $(VERILATOR_ROOT)/include
RTL_DIR := ../rtl
BUILD_DIR := ../build/spin_core_tb
TOP := spin_core_tb

MESH_X_COUNT ?= 2
MESH_Y_COUNT ?= 1
H0_COUNT ?= 2
CORES_PER_H0 ?= 2
H0_MVM_COUNT ?= 1
H1_MVM_COUNT ?= 1
CROSS_MVM_COUNT ?= 1
FIFO_DEPTH ?= 4
SKIP_ZERO_BLOCKS ?= 0
USE_RAMULATOR ?= 0
MEM_PIN_COUNT ?= 128
MEM_PIN_GBPS ?= 32
MEM_LANES ?= 16
ITERATION_COUNT ?= 1
TIMING_ONLY ?= 0
REGENERATE_RAMULATOR_CONFIG ?= 0
COEFF_A_VALUE ?= 0
COEFF_B_VALUE ?= 1
NOISE_AMPLITUDE ?= 0
DATASET ?= g256_smoke.txt
NOC_STATS_FILE ?= noc_stats.csv
HYBRID_MAX_X ?= 4
HYBRID_MAX_Y ?= 4
HYBRID_FIFO_DEPTH ?= 4
HYBRID_TRAFFIC ?= logs/hybrid_noc.traffic
HYBRID_STATS_PREFIX ?= logs/hybrid_noc
HYBRID_STATS_INTERVAL ?= 100
HYBRID_DATASET ?= tb/datasets/g16384_kings.txt
HYBRID_SCHEDULE ?=
HYBRID_MESH_X ?= 4
HYBRID_MESH_Y ?= 4
HYBRID_H0_PER_H1 ?= 1
HYBRID_CROSS_ENGINES ?= 16
HYBRID_BLOCK_SERVICE_CYCLES ?= 64
HYBRID_CONFIG := $(HYBRID_MAX_X)x$(HYBRID_MAX_Y)_f$(HYBRID_FIFO_DEPTH)
HYBRID_BUILD_DIR := ../build/hybrid_noc_$(HYBRID_CONFIG)
HYBRID_SCHEDULE_ARG = $(if $(HYBRID_SCHEDULE),--schedule $(HYBRID_SCHEDULE),)
FLOO_CONFIG := $(MESH_X_COUNT)x$(MESH_Y_COUNT)_h0$(H0_COUNT)_c$(CORES_PER_H0)_e$(H0_MVM_COUNT)-$(H1_MVM_COUNT)-$(CROSS_MVM_COUNT)_f$(FIFO_DEPTH)_s$(SKIP_ZERO_BLOCKS)_r$(USE_RAMULATOR)_i$(ITERATION_COUNT)_t$(TIMING_ONLY)
FLOO_BUILD_DIR := ../build/ising_mesh_floo_$(FLOO_CONFIG)
TIMING_ONLY_FLAG := $(if $(filter 1,$(TIMING_ONLY)),-DAZILLA_TIMING_ONLY,)
VERILATOR_SPLIT_FLAGS := $(if $(filter-out 0,$(VERILATOR_OUTPUT_SPLIT)),--output-split $(VERILATOR_OUTPUT_SPLIT) --output-split-cfuncs $(VERILATOR_OUTPUT_SPLIT),)

.PHONY: all run core-local-baseline-256-test core-local-baseline-256-vcs-test static-hybrid-256-test static-hybrid-256-vcs-test floo-router-test floo-mesh-build floo-mesh-test floo-mesh-dram-test ramulator-config dram-streamer-test ramulator-node-perf-test hybrid-noc-build hybrid-noc-workload hybrid-noc-run hybrid-noc-test cycle-model-compute-trace-build cycle-model-spin-core-trace-build cycle-model-hierarchy-trace-build cycle-model-router-trace-build cycle-model-ramulator-library clean

static-hybrid-256-test:
	mkdir -p ../build/static_hybrid_256_tb
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		--top-module static_hybrid_256_tb --Mdir build/static_hybrid_256_tb \
		rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv \
		rtl/symmetric_mvm.sv rtl/hierarchy_node.sv \
		rtl/core_local_mvm_engine.sv tb/static_hybrid_256_tb.sv
	../build/static_hybrid_256_tb/Vstatic_hybrid_256_tb

static-hybrid-256-vcs-test:
	mkdir -p ../build/static_hybrid_256_vcs
	cd ../build/static_hybrid_256_vcs && \
		export VCS_HOME=$${VCS_HOME:-/home/hwpeng/vcs} && \
		$$VCS_HOME/bin/vcs -full64 -sverilog -timescale=1ns/1ps \
		-LDFLAGS '-no-pie' -top static_hybrid_256_tb \
		../../rtl/ising_pkg.sv ../../rtl/j_block_sram.sv ../../rtl/mvm.sv \
		../../rtl/symmetric_mvm.sv ../../rtl/hierarchy_node.sv \
		../../rtl/core_local_mvm_engine.sv ../../tb/static_hybrid_256_tb.sv \
		-o simv && ./simv

core-local-baseline-256-test:
	mkdir -p ../build/core_local_baseline_256_tb
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		--top-module core_local_baseline_256_tb \
		--Mdir build/core_local_baseline_256_tb \
		rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv \
		rtl/core_local_mvm_engine.sv tb/core_local_baseline_256_tb.sv
	../build/core_local_baseline_256_tb/Vcore_local_baseline_256_tb

core-local-baseline-256-vcs-test:
	mkdir -p ../build/core_local_baseline_256_vcs
	cd ../build/core_local_baseline_256_vcs && \
		export VCS_HOME=$${VCS_HOME:-/home/hwpeng/vcs} && \
		$$VCS_HOME/bin/vcs -full64 -sverilog \
		-timescale=1ns/1ps -LDFLAGS '-no-pie' \
		-top core_local_baseline_256_tb \
		../../rtl/ising_pkg.sv ../../rtl/j_block_sram.sv ../../rtl/mvm.sv \
		../../rtl/core_local_mvm_engine.sv \
		../../tb/core_local_baseline_256_tb.sv -o simv
	../build/core_local_baseline_256_vcs/simv

cycle-model-compute-trace-build:
	mkdir -p ../build/cycle_model_compute_trace_tb
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		--top-module cycle_model_compute_trace_tb \
		--Mdir build/cycle_model_compute_trace_tb \
		rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/symmetric_mvm.sv \
		tb/cycle_model_compute_trace_tb.sv

cycle-model-spin-core-trace-build:
	mkdir -p ../build/cycle_model_spin_core_trace_tb
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		--top-module cycle_model_spin_core_trace_tb \
		--Mdir build/cycle_model_spin_core_trace_tb \
		rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv rtl/core.sv \
		tb/cycle_model_spin_core_trace_tb.sv

cycle-model-hierarchy-trace-build:
	mkdir -p ../build/cycle_model_hierarchy_trace_tb
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		--top-module cycle_model_hierarchy_trace_tb \
		--Mdir build/cycle_model_hierarchy_trace_tb \
		rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/symmetric_mvm.sv \
		rtl/hierarchy_node.sv tb/cycle_model_hierarchy_trace_tb.sv

cycle-model-router-trace-build:
	mkdir -p ../build/cycle_model_router_trace_tb
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		--top-module cycle_model_router_trace_tb \
		--Mdir build/cycle_model_router_trace_tb \
		-f rtl/floo_router_files.f rtl/ising_pkg.sv rtl/azilla_floo_router.sv \
		tb/cycle_model_router_trace_tb.sv

cycle-model-ramulator-library:
	mkdir -p ../build/cycle_model_ramulator
	$(CXX) -std=c++20 -shared -fPIC \
		-I$(abspath $(RAMULATOR_DIR)/src) -I$(VERILATOR_INCLUDE) \
		-I$(VERILATOR_INCLUDE)/vltstd \
		$(abspath ramulator_dpi.cpp) \
		-L$(abspath $(RAMULATOR_DIR)) -lramulator \
		-Wl,-rpath,$(abspath $(RAMULATOR_DIR)) \
		-o ../build/cycle_model_ramulator/libazilla_ramulator.so

all: run

$(BUILD_DIR)/V$(TOP): \
	$(RTL_DIR)/ising_pkg.sv \
	$(RTL_DIR)/j_block_sram.sv \
	$(RTL_DIR)/mvm.sv \
	$(RTL_DIR)/core.sv \
	spin_core_tb.sv
	mkdir -p $(BUILD_DIR)
	$(VERILATOR) --binary --timing --assert -Wall --Wno-fatal \
		--top-module $(TOP) \
		--Mdir $(BUILD_DIR) \
		$(RTL_DIR)/ising_pkg.sv \
		$(RTL_DIR)/j_block_sram.sv \
		$(RTL_DIR)/mvm.sv \
		$(RTL_DIR)/core.sv \
		spin_core_tb.sv

run: $(BUILD_DIR)/V$(TOP)
	$(BUILD_DIR)/V$(TOP)

# Focused native-Azilla/FlooNoC transport test.
floo-router-test:
	mkdir -p ../build/azilla_floo_router_tb
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		--top-module azilla_floo_router_tb \
		--Mdir build/azilla_floo_router_tb \
		-f rtl/floo_router_files.f \
		rtl/ising_pkg.sv rtl/azilla_floo_router.sv \
		tb/azilla_floo_router_tb.sv
	../build/azilla_floo_router_tb/Vazilla_floo_router_tb

# Compile the maximum router fabric once. Runtime traffic files may select any
# rectangular active mesh that fits within HYBRID_MAX_X by HYBRID_MAX_Y.
$(HYBRID_BUILD_DIR)/Vhybrid_noc_perf_tb: \
	$(RTL_DIR)/floo_router_files.f \
	$(RTL_DIR)/ising_pkg.sv \
	$(RTL_DIR)/azilla_floo_router.sv \
	hybrid_noc_perf_tb.sv
	mkdir -p $(HYBRID_BUILD_DIR)
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		--top-module hybrid_noc_perf_tb \
		--Mdir build/$(notdir $(HYBRID_BUILD_DIR)) \
		-GMAX_MESH_X=$(HYBRID_MAX_X) \
		-GMAX_MESH_Y=$(HYBRID_MAX_Y) \
		-GFIFO_DEPTH=$(HYBRID_FIFO_DEPTH) \
		-f rtl/floo_router_files.f \
		rtl/ising_pkg.sv rtl/azilla_floo_router.sv \
		tb/hybrid_noc_perf_tb.sv

hybrid-noc-build: $(HYBRID_BUILD_DIR)/Vhybrid_noc_perf_tb

hybrid-noc-workload:
	cd .. && python3 scripts/generate_hybrid_noc_workload.py \
		--dataset $(HYBRID_DATASET) \
		--output $(HYBRID_TRAFFIC) \
		--mesh-x $(HYBRID_MESH_X) \
		--mesh-y $(HYBRID_MESH_Y) \
		--h0-per-h1 $(HYBRID_H0_PER_H1) \
		--cross-engines $(HYBRID_CROSS_ENGINES) \
		--block-service-cycles $(HYBRID_BLOCK_SERVICE_CYCLES) $(HYBRID_SCHEDULE_ARG)

# Replay a runtime-generated hierarchy workload through the real FlooNoC
# routers and produce CSV files accepted by the existing analysis scripts.
hybrid-noc-run: $(HYBRID_BUILD_DIR)/Vhybrid_noc_perf_tb
	cd .. && build/$(notdir $(HYBRID_BUILD_DIR))/Vhybrid_noc_perf_tb \
		+TRAFFIC=$(HYBRID_TRAFFIC) \
		+NOC_STATS_FILE=$(HYBRID_STATS_PREFIX).csv \
		+NOC_TIMELINE_FILE=$(HYBRID_STATS_PREFIX)_timeline.csv \
		+NOC_EVENT_FILE=$(HYBRID_STATS_PREFIX)_events.csv \
		+NOC_STATS_INTERVAL=$(HYBRID_STATS_INTERVAL)

hybrid-noc-test: hybrid-noc-workload hybrid-noc-run

RAMULATOR_DIR := ../third_party/ramulator2

# Tagged, out-of-order RTL weight-streamer test backed by Ramulator GDDR6.
dram-streamer-test:
	mkdir -p ../build/dram_weight_streamer_tb
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		--top-module dram_weight_streamer_tb \
		--Mdir build/dram_weight_streamer_tb \
		-CFLAGS "-std=c++20 -I$(abspath $(RAMULATOR_DIR))/src" \
		-LDFLAGS "-L$(abspath $(RAMULATOR_DIR)) -lramulator -Wl,-rpath,$(abspath $(RAMULATOR_DIR))" \
		rtl/ising_pkg.sv rtl/dram_weight_streamer.sv \
		tb/ramulator_dpi_bridge.sv tb/dram_weight_streamer_tb.sv \
		$(abspath ramulator_dpi.cpp)
	cd .. && build/dram_weight_streamer_tb/Vdram_weight_streamer_tb

# Single-node dense throughput microbenchmark used by the parameter sweep.
PERF_MVM_COUNT ?= 4
PERF_OUTPUT_LANES ?= 1
PERF_WORK_BLOCKS ?= 256
PERF_TOTAL_BLOCKS ?= 2048
PERF_STATE_ENTRIES ?= 64
PERF_MEM_LANES ?= 16
PERF_MAX_OUTSTANDING ?= 64
PERF_DATASET ?= g65536_kings.txt
PERF_CONFIG := m$(PERF_MVM_COUNT)_o$(PERF_OUTPUT_LANES)_b$(PERF_WORK_BLOCKS)_l$(PERF_MEM_LANES)

ramulator-node-perf-test:
	mkdir -p ../build/ramulator_node_perf_$(PERF_CONFIG)
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal -Wno-TIMESCALEMOD \
		--top-module ramulator_node_perf_tb \
		--Mdir build/ramulator_node_perf_$(PERF_CONFIG) \
		-GMVM_COUNT=$(PERF_MVM_COUNT) \
		-GOUTPUT_LANES=$(PERF_OUTPUT_LANES) \
		-GWORK_BLOCK_COUNT=$(PERF_WORK_BLOCKS) \
		-GTOTAL_BLOCK_COUNT=$(PERF_TOTAL_BLOCKS) \
		-GSTATE_ENTRY_COUNT=$(PERF_STATE_ENTRIES) \
		-GMEM_LANES=$(PERF_MEM_LANES) \
		-GMAX_OUTSTANDING=$(PERF_MAX_OUTSTANDING) \
		-CFLAGS "-std=c++20 -I$(abspath $(RAMULATOR_DIR))/src" \
		-LDFLAGS "-L$(abspath $(RAMULATOR_DIR)) -lramulator -Wl,-rpath,$(abspath $(RAMULATOR_DIR))" \
		rtl/ising_pkg.sv rtl/j_block_sram.sv rtl/mvm.sv rtl/symmetric_mvm.sv \
		rtl/hierarchy_node.sv rtl/dram_weight_streamer.sv \
		tb/ramulator_dpi_bridge.sv tb/ramulator_node_frontend.sv \
		tb/ramulator_node_perf_tb.sv $(abspath ramulator_dpi.cpp)
	cd .. && build/ramulator_node_perf_$(PERF_CONFIG)/Vramulator_node_perf_tb \
		+DATASET=$(PERF_DATASET)

# Full accelerator hierarchy elaboration using FlooNoC at every top-level
# mesh location. Keeping compilation on the binary target allows large scale
# sweeps to build without running and lets later simulations reuse the binary.
$(FLOO_BUILD_DIR)/Vising_mesh_tb: \
	$(RTL_DIR)/floo_router_files.f \
	$(RTL_DIR)/ising_pkg.sv \
	$(RTL_DIR)/j_block_sram.sv \
	$(RTL_DIR)/mvm.sv \
	$(RTL_DIR)/symmetric_mvm.sv \
	$(RTL_DIR)/core.sv \
	$(RTL_DIR)/hierarchy_node.sv \
	$(RTL_DIR)/h0_adapter.sv \
	$(RTL_DIR)/h0_tile.sv \
	$(RTL_DIR)/h1_child_adapter.sv \
	$(RTL_DIR)/h1_tile.sv \
	$(RTL_DIR)/h1_noc_adapter.sv \
	$(RTL_DIR)/cross_h1_node.sv \
	$(RTL_DIR)/azilla_floo_router.sv \
	$(RTL_DIR)/top_node.sv \
	$(RTL_DIR)/mesh_h1_tile.sv \
	$(RTL_DIR)/ising_mesh.sv \
	$(RTL_DIR)/dram_weight_streamer.sv \
	ramulator_dpi_bridge.sv \
	ramulator_node_frontend.sv \
	ising_mesh_tb.sv \
	ramulator_dpi.cpp
	mkdir -p $(FLOO_BUILD_DIR)
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
		-j $(VERILATOR_JOBS) $(VERILATOR_SPLIT_FLAGS) \
		$(TIMING_ONLY_FLAG) \
		--top-module ising_mesh_tb \
		--Mdir build/$(notdir $(FLOO_BUILD_DIR)) \
		-GMESH_X_COUNT=$(MESH_X_COUNT) \
		-GMESH_Y_COUNT=$(MESH_Y_COUNT) \
		-GH0_COUNT=$(H0_COUNT) \
		-GCORES_PER_H0=$(CORES_PER_H0) \
		-GH0_MVM_COUNT=$(H0_MVM_COUNT) \
		-GH1_MVM_COUNT=$(H1_MVM_COUNT) \
		-GCROSS_MVM_COUNT=$(CROSS_MVM_COUNT) \
		-GFIFO_DEPTH=$(FIFO_DEPTH) \
		-GSKIP_ZERO_BLOCKS=$(SKIP_ZERO_BLOCKS) \
		-GUSE_RAMULATOR=$(USE_RAMULATOR) \
		-GMEM_PIN_COUNT=$(MEM_PIN_COUNT) \
		-GMEM_PIN_GBPS=$(MEM_PIN_GBPS) \
		-GMEM_LANES=$(MEM_LANES) \
		-GITERATION_COUNT=$(ITERATION_COUNT) \
		-GCOEFF_A_VALUE=$(COEFF_A_VALUE) \
		-GCOEFF_B_VALUE=$(COEFF_B_VALUE) \
		-GNOISE_AMPLITUDE=$(NOISE_AMPLITUDE) \
		-CFLAGS "-std=c++20 -I$(abspath $(RAMULATOR_DIR))/src" \
		-LDFLAGS "-L$(abspath $(RAMULATOR_DIR)) -lramulator -Wl,-rpath,$(abspath $(RAMULATOR_DIR))" \
		-f rtl/floo_router_files.f \
		rtl/ising_pkg.sv rtl/j_block_sram.sv \
		rtl/mvm.sv rtl/symmetric_mvm.sv \
		rtl/core.sv rtl/hierarchy_node.sv \
		rtl/h0_adapter.sv rtl/h0_tile.sv \
		rtl/h1_child_adapter.sv rtl/h1_tile.sv \
		rtl/h1_noc_adapter.sv rtl/cross_h1_node.sv \
		rtl/azilla_floo_router.sv rtl/top_node.sv \
		rtl/mesh_h1_tile.sv rtl/ising_mesh.sv \
		rtl/dram_weight_streamer.sv tb/ramulator_dpi_bridge.sv \
		tb/ramulator_node_frontend.sv tb/ising_mesh_tb.sv \
		$(abspath ramulator_dpi.cpp)

# Elaborate and compile a full hierarchy without launching it.  This is the
# appropriate target for scale sweeps whose datasets or simulations will be
# supplied later.  The configuration-specific binary path remains identical
# to floo-mesh-test, so a later run reuses this build.
floo-mesh-build: $(FLOO_BUILD_DIR)/Vising_mesh_tb
	@echo "built ../build/$(notdir $(FLOO_BUILD_DIR))/Vising_mesh_tb"

floo-mesh-test: $(FLOO_BUILD_DIR)/Vising_mesh_tb
	cd .. && build/$(notdir $(FLOO_BUILD_DIR))/Vising_mesh_tb \
		+DATASET=$(DATASET) +NOC_STATS_FILE=$(NOC_STATS_FILE) $(SIM_ARGS)

# Regenerate the checked-in projected-memory configuration only when changing
# its physical pin/rate parameters. This target requires Ramulator's optional
# Python bindings; ordinary simulations consume the checked-in YAML directly.
ramulator-config:
	AZILLA_MEM_PINS=$(MEM_PIN_COUNT) AZILLA_MEM_PIN_GBPS=$(MEM_PIN_GBPS) \
		PYTHONPATH=$(abspath $(RAMULATOR_DIR))/python \
		python3 -m ramulator export ramulator_config.py -o ramulator_128x32.yaml

RAMULATOR_CONFIG_PREREQ := $(if $(filter 1,$(REGENERATE_RAMULATOR_CONFIG)),ramulator-config,)

# Full hierarchy with node-local Ramulator-backed projected GDDR interfaces.
floo-mesh-dram-test: $(RAMULATOR_CONFIG_PREREQ)
	$(MAKE) floo-mesh-test USE_RAMULATOR=1

clean:
	rm -rf $(BUILD_DIR)
