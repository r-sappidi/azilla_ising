VERILATOR ?= verilator
RTL_DIR := ../rtl
BUILD_DIR := ../build/spin_core_tb
TOP := spin_core_tb

.PHONY: all run clean

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

clean:
	rm -rf $(BUILD_DIR)
