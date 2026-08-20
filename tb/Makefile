VERILATOR ?= verilator
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
DATASET ?= g256_smoke.txt
FLOO_CONFIG := $(MESH_X_COUNT)x$(MESH_Y_COUNT)_h0$(H0_COUNT)_c$(CORES_PER_H0)_e$(H0_MVM_COUNT)-$(H1_MVM_COUNT)-$(CROSS_MVM_COUNT)_f$(FIFO_DEPTH)_s$(SKIP_ZERO_BLOCKS)_r$(USE_RAMULATOR)_i$(ITERATION_COUNT)
FLOO_BUILD_DIR := ../build/ising_mesh_floo_$(FLOO_CONFIG)

.PHONY: all run floo-router-test floo-mesh-test floo-mesh-dram-test dram-streamer-test clean

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

# Full accelerator hierarchy elaboration/reset smoke test using FlooNoC at
# every top-level mesh location.
floo-mesh-test:
	mkdir -p $(FLOO_BUILD_DIR)
	cd .. && $(VERILATOR) --binary --timing --assert -Wno-fatal \
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
	cd .. && build/$(notdir $(FLOO_BUILD_DIR))/Vising_mesh_tb +DATASET=$(DATASET)

# Full hierarchy with node-local Ramulator-backed projected GDDR interfaces.
floo-mesh-dram-test:
	AZILLA_MEM_PINS=$(MEM_PIN_COUNT) AZILLA_MEM_PIN_GBPS=$(MEM_PIN_GBPS) \
		PYTHONPATH=$(abspath $(RAMULATOR_DIR))/python \
		python3 -m ramulator export ramulator_config.py -o ramulator_128x32.yaml
	$(MAKE) floo-mesh-test USE_RAMULATOR=1

clean:
	rm -rf $(BUILD_DIR)
