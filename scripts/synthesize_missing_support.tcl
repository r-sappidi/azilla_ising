# Component-only GF12 estimate. Library is read in place, never copied.
set out $::env(AZILLA_OUT)
file mkdir $out
set_host_options -max_cores 2
set target_library $::env(AZILLA_GF12_DB)
set link_library "* $target_library"
define_design_lib WORK -path "$out/work"
analyze -format sverilog -define SYNTHESIS $::env(AZILLA_RTL_FILES)
elaborate $::env(AZILLA_TOP) -parameters $::env(AZILLA_PARAMETERS)
link
redirect "$out/units.txt" {report_units}
create_clock -name clk -period $::env(AZILLA_PERIOD_PS) [get_ports clk]
set_clock_uncertainty 50 [get_clocks clk]
set_input_delay 100 -clock clk [remove_from_collection [all_inputs] [get_ports clk]]
set_output_delay 100 -clock clk [all_outputs]
set_input_transition 20 [remove_from_collection [all_inputs] [get_ports clk]]
set_load 1 [all_outputs]
set_max_fanout 16 [current_design]
set_fix_hold [get_clocks clk]
compile_ultra
redirect "$out/area.txt" {report_area -hierarchy}
redirect "$out/setup.txt" {report_timing -delay_type max -max_paths 20}
redirect "$out/hold.txt" {report_timing -delay_type min -max_paths 20}
redirect "$out/constraints.txt" {report_constraint -all_violators}
redirect "$out/check_design.txt" {check_design}
redirect "$out/check_timing.txt" {check_timing}
write -format ddc -hierarchy -output "$out/mapped.ddc"
write_sdc "$out/constraints.sdc"
set f [open "$out/status.txt" w]
puts $f "setup_slack_ps [get_attribute [get_timing_paths -delay_type max -max_paths 1] slack]"
puts $f "hold_slack_ps [get_attribute [get_timing_paths -delay_type min -max_paths 1] slack]"
puts $f "period_ps $::env(AZILLA_PERIOD_PS)"
puts $f "scope component_only_register_mapped_payload_no_physical_closure"
close $f
exit
