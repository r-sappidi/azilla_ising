// Port of write_exact_event_metrics / write_transfer_trace from
// model/azilla_cycle_model/metrics.py.  Output matches Python's json.dump
// (indent=2, sort_keys=True) and csv.DictWriter (CRLF rows) byte for byte,
// including Python float repr.
#pragma once

#include <map>
#include <string>

#include "exact_events.hpp"

namespace azilla {

std::string python_float_repr(double value);

std::map<std::string, std::string> write_exact_event_metrics(const std::string& prefix, const ExactEventResult& result,
                                                            const Geometry& geometry);
std::string write_transfer_trace(const std::string& path, std::vector<Transfer> transfers, const Geometry& geometry);
// Shared by the sorted writer above and the opt-in streaming sink so both
// produce byte-identical rows.
const char* transfer_trace_header();
std::string format_transfer_line(const Transfer& transfer, const Geometry& geometry);

}  // namespace azilla
