#include "metrics.hpp"

#include "json.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <variant>
#include <vector>

namespace azilla {

// ------------------------------------------------------------ float repr

std::string python_float_repr(double value) {
    if (std::isnan(value)) return "NaN";
    if (std::isinf(value)) return value > 0 ? "Infinity" : "-Infinity";
    if (value == 0.0) return std::signbit(value) ? "-0.0" : "0.0";
    char buffer[64];
    auto res = std::to_chars(buffer, buffer + sizeof(buffer), value, std::chars_format::scientific);
    std::string text(buffer, res.ptr);
    bool negative = false;
    if (text[0] == '-') {
        negative = true;
        text.erase(0, 1);
    }
    size_t epos = text.find('e');
    std::string mantissa = text.substr(0, epos);
    int exponent = std::stoi(text.substr(epos + 1));
    std::string digits;
    for (char c : mantissa)
        if (c != '.') digits.push_back(c);
    // Strip trailing zeros (to_chars shortest should not emit them, but be safe).
    while (digits.size() > 1 && digits.back() == '0') digits.pop_back();
    int n = int(digits.size());
    int decpt = exponent + 1;
    std::string out;
    if (decpt <= -4 || decpt > 16) {
        out = digits.substr(0, 1);
        if (n > 1) out += "." + digits.substr(1);
        int e = decpt - 1;
        char ebuf[16];
        std::snprintf(ebuf, sizeof(ebuf), "e%c%02d", e < 0 ? '-' : '+', std::abs(e));
        out += ebuf;
    } else if (decpt <= 0) {
        out = "0." + std::string(size_t(-decpt), '0') + digits;
    } else if (decpt >= n) {
        out = digits + std::string(size_t(decpt - n), '0') + ".0";
    } else {
        out = digits.substr(0, size_t(decpt)) + "." + digits.substr(size_t(decpt));
    }
    return negative ? "-" + out : out;
}

// ------------------------------------------------------------------ JSON
// (Json value type: json.hpp)

namespace {

void dump_string(std::string& out, const std::string& s) {
    out.push_back('"');
    for (unsigned char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20 || c >= 0x7f) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out.push_back(char(c));
                }
        }
    }
    out.push_back('"');
}

void dump(std::string& out, const Json& json, int indent) {
    std::visit(
        [&](const auto& v) {
            using T = std::decay_t<decltype(v)>;
            if constexpr (std::is_same_v<T, std::nullptr_t>) {
                out += "null";
            } else if constexpr (std::is_same_v<T, bool>) {
                out += v ? "true" : "false";
            } else if constexpr (std::is_same_v<T, long long>) {
                out += std::to_string(v);
            } else if constexpr (std::is_same_v<T, double>) {
                out += python_float_repr(v);
            } else if constexpr (std::is_same_v<T, std::string>) {
                dump_string(out, v);
            } else if constexpr (std::is_same_v<T, JsonOrdered>) {
                if (v.empty()) {
                    out += "{}";
                    return;
                }
                out += "{\n";
                for (size_t i = 0; i < v.size(); ++i) {
                    out += std::string(size_t(indent + 2), ' ');
                    dump_string(out, v[i].first);
                    out += ": ";
                    dump(out, v[i].second, indent + 2);
                    if (i + 1 < v.size()) out += ",";
                    out += "\n";
                }
                out += std::string(size_t(indent), ' ') + "}";
            } else if constexpr (std::is_same_v<T, JsonArray>) {
                if (v.empty()) {
                    out += "[]";
                    return;
                }
                out += "[\n";
                for (size_t i = 0; i < v.size(); ++i) {
                    out += std::string(size_t(indent + 2), ' ');
                    dump(out, v[i], indent + 2);
                    if (i + 1 < v.size()) out += ",";
                    out += "\n";
                }
                out += std::string(size_t(indent), ' ') + "]";
            } else {
                if (v.empty()) {
                    out += "{}";
                    return;
                }
                out += "{\n";
                size_t i = 0;
                for (const auto& [key, item] : v) {
                    out += std::string(size_t(indent + 2), ' ');
                    dump_string(out, key);
                    out += ": ";
                    dump(out, item, indent + 2);
                    if (++i < v.size()) out += ",";
                    out += "\n";
                }
                out += std::string(size_t(indent), ' ') + "}";
            }
        },
        json.value);
}

std::string csv_cell(const Json& json) {
    std::string out;
    std::visit(
        [&](const auto& v) {
            using T = std::decay_t<decltype(v)>;
            if constexpr (std::is_same_v<T, bool>) out = v ? "True" : "False";
            else if constexpr (std::is_same_v<T, long long>) out = std::to_string(v);
            else if constexpr (std::is_same_v<T, double>) out = python_float_repr(v);
            else if constexpr (std::is_same_v<T, std::string>) out = v;
            else out = "";
        },
        json.value);
    // csv excel dialect quoting (QUOTE_MINIMAL)
    if (out.find_first_of(",\"\r\n") != std::string::npos) {
        std::string quoted = "\"";
        for (char c : out) {
            if (c == '"') quoted += "\"\"";
            else quoted.push_back(c);
        }
        quoted += "\"";
        return quoted;
    }
    return out;
}

using Row = std::vector<std::pair<std::string, Json>>;  // ordered like Python dict

void write_csv(const std::string& path, const std::vector<Row>& rows) {
    if (rows.empty()) throw std::runtime_error("cannot write empty metrics table " + path);
    std::ofstream handle(path, std::ios::binary);
    std::string line;
    for (size_t i = 0; i < rows[0].size(); ++i) {
        if (i) line += ",";
        line += csv_cell(Json(rows[0][i].first));
    }
    handle << line << "\r\n";
    for (const auto& row : rows) {
        line.clear();
        for (size_t i = 0; i < row.size(); ++i) {
            if (i) line += ",";
            line += csv_cell(row[i].second);
        }
        handle << line << "\r\n";
    }
}

JsonObject object_from_row(const Row& row) {
    JsonObject object;
    for (const auto& [key, value] : row) object[key] = value;
    return object;
}

std::string output_path(const std::string& prefix, const std::string& suffix) {
    std::filesystem::path p(prefix);
    if (p.has_parent_path()) std::filesystem::create_directories(p.parent_path());
    return (p.parent_path() / (p.filename().string() + suffix)).string();
}

}  // namespace

std::map<std::string, std::string> write_exact_event_metrics(const std::string& prefix, const ExactEventResult& result,
                                                            const Geometry& geometry) {
    std::vector<Row> noc_rows;
    for (const auto& r : result.noc_resources) {
        Row row = {{"scope", r.scope}, {"node", r.node}, {"x", r.x}, {"y", r.y}, {"direction", r.direction},
                   {"offered_cycles", r.offered_cycles}, {"accepted_flits", r.accepted_flits},
                   {"stall_cycles", r.stall_cycles}, {"packets", r.packets}, {"state_flits", r.state_flits},
                   {"partial_flits", r.partial_flits}, {"epoch_done_flits", r.epoch_done_flits},
                   {"other_flits", r.other_flits}};
        row.emplace_back("window_utilization", result.iteration_cycles
                                                   ? double(r.accepted_flits) / double(result.iteration_cycles)
                                                   : 0.0);
        row.emplace_back("acceptance_fraction", r.acceptance_fraction());
        row.emplace_back("backpressure", r.backpressure());
        noc_rows.push_back(row);
    }
    std::vector<Row> node_rows;
    for (const auto& n : result.nodes)
        node_rows.push_back({{"node", n.node}, {"x", n.x}, {"y", n.y}, {"h0_jobs", n.h0_jobs}, {"h1_jobs", n.h1_jobs},
                             {"cross_jobs", n.cross_jobs}, {"total_jobs", n.total_jobs},
                             {"state_publications_sent", n.state_publications_sent},
                             {"state_publications_received", n.state_publications_received},
                             {"h1_completion_cycle", n.h1_completion_cycle},
                             {"cross_completion_cycle", n.cross_completion_cycle},
                             {"completion_cycle", n.completion_cycle}});
    std::vector<Row> dram_rows;
    for (const auto& d : result.dram) {
        Row row = {{"system_id", d.system_id}, {"level", d.level}, {"node", d.node}, {"index", d.index},
                   {"engines", d.engines}, {"scheduled_jobs", d.scheduled_jobs},
                   {"accepted_requests", d.accepted_requests}, {"rejected_requests", d.rejected_requests},
                   {"completed_requests", d.completed_requests}, {"outstanding_requests", d.outstanding_requests},
                   {"average_latency_ticks", d.average_latency_ticks}, {"maximum_latency_ticks", d.maximum_latency_ticks},
                   {"average_latency_cycles", d.average_latency_cycles},
                   {"maximum_latency_cycles", d.maximum_latency_cycles}};
        uint64_t attempts = d.accepted_requests + d.rejected_requests;
        row.emplace_back("request_rejection_fraction",
                         attempts ? double(d.rejected_requests) / double(attempts) : 0.0);
        dram_rows.push_back(row);
    }

    const auto& counters = result.counters;
    JsonObject effective;
    if (result.execution_mode == "cores-only") {
        effective = {{"per_h0_destination_cores", geometry.cores_per_h0}, {"h1", 0}, {"cross", 0},
                     {"system_total", geometry.total_blocks()}};
    } else {
        int per_h0 = 0, per_h1_local = 0, per_h1_cross = 0;
        bool have_h0 = false, have_h1 = false, have_cross = false;
        long total = 0;
        for (const auto& d : result.dram) {
            total += d.engines;
            if (d.level == "h0" && !have_h0) { per_h0 = d.engines; have_h0 = true; }
            if (d.level == "h1" && !have_h1) { per_h1_local = d.engines; have_h1 = true; }
            if (d.level == "cross" && !have_cross) { per_h1_cross = d.engines; have_cross = true; }
        }
        effective = {{"per_h0", per_h0}, {"per_h1_local", per_h1_local}, {"per_h1_cross", per_h1_cross},
                     {"system_total", total}};
    }
    JsonObject hop_histogram;
    for (const auto& [hops, flits] : result.hop_histogram) hop_histogram[std::to_string(hops)] = flits;
    // shared_fetch_events.run_full_cores: ``... if counters.injected_flits else 0`` (int),
    // while the cir path uses the float 0.0.
    Json average_hops = (result.execution_mode == "cores-only" && counters.injected_flits == 0)
                            ? Json(0LL)
                            : Json(result.average_hops);

    JsonObject summary = {
        {"schema_version", 1},
        {"accuracy", result.accuracy},
        {"execution_mode", result.execution_mode},
        {"cores_only_ablation",
         JsonObject{{"unordered_interaction_blocks", result.unordered_interaction_blocks},
                    {"directed_core_jobs", result.directed_core_jobs},
                    {"weight_block_reads", result.weight_block_reads},
                    {"logical_weight_blocks_stored", result.logical_weight_blocks_stored},
                    {"remote_state_packets", result.remote_state_packets},
                    {"core_weight_buffers", result.core_weight_buffers},
                    {"pipeline_contract", result.core_pipeline_contract},
                    {"top_level_partial_packets", counters.type_flits[1] / 4}}},
        {"effective_mvm_engines", effective},
        {"geometry", JsonObject{{"mesh_x", geometry.mesh_x}, {"mesh_y", geometry.mesh_y},
                                {"h0_per_h1", geometry.h0_per_h1}, {"cores_per_h0", geometry.cores_per_h0},
                                {"spins_per_core", 32}, {"spin_count", geometry.spin_count()}}},
        {"timing_cycles", JsonObject{{"initialization", result.initialization_cycles},
                                     {"iteration", result.iteration_cycles}, {"total", result.total_cycles}}},
        {"package", JsonObject{{"chiplet_link_latency_cycles", result.chiplet_link_latency_cycles}}},
        {"interconnect", JsonObject{{"forward_latency_cycles", result.interconnect.forward_latency_cycles},
                                    {"flit_interval_cycles", result.interconnect.flit_interval_cycles},
                                    {"max_inflight_flits", result.interconnect.max_inflight_flits},
                                    {"credit_return_latency_cycles", result.interconnect.credit_return_latency_cycles}}},
        {"scheduled_jobs", JsonObject{{"h0", result.scheduled_h0}, {"h1", result.scheduled_h1},
                                      {"cross", result.scheduled_cross},
                                      {"total", result.scheduled_h0 + result.scheduled_h1 + result.scheduled_cross}}},
        {"noc", JsonObject{{"injected_flits", counters.injected_flits}, {"ejected_flits", counters.ejected_flits},
                           {"physical_link_flits", counters.physical_link_flits},
                           {"injection_stalls", counters.injection_stalls},
                           {"ejection_stalls", counters.ejection_stalls}, {"link_stalls", counters.link_stalls},
                           {"state_flits", counters.type_flits[0]}, {"partial_flits", counters.type_flits[1]},
                           {"epoch_done_flits", counters.type_flits[2]}, {"other_flits", counters.type_flits[3]},
                           {"average_hops", average_hops}, {"hop_histogram", hop_histogram}}},
    };
    JsonArray nodes_json, dram_json, noc_json;
    for (const auto& row : node_rows) nodes_json.emplace_back(object_from_row(row));
    for (const auto& row : dram_rows) dram_json.emplace_back(object_from_row(row));
    for (const auto& row : noc_rows) noc_json.emplace_back(object_from_row(row));
    summary["nodes"] = nodes_json;
    summary["dram_systems"] = dram_json;
    summary["noc_resources"] = noc_json;

    std::map<std::string, std::string> paths = {{"summary", output_path(prefix, "_summary.json")},
                                                {"noc", output_path(prefix, "_noc.csv")},
                                                {"nodes", output_path(prefix, "_nodes.csv")},
                                                {"dram", output_path(prefix, "_dram.csv")}};
    {
        std::string text;
        dump(text, Json(summary), 0);
        std::ofstream handle(paths["summary"], std::ios::binary);
        handle << text << "\n";
    }
    write_csv(paths["noc"], noc_rows);
    write_csv(paths["nodes"], node_rows);
    write_csv(paths["dram"], dram_rows);
    return paths;
}

const char* transfer_trace_header() {
    return "cycle,event,scope,node,x,y,direction,packet_type,source_id,epoch,block_id,dest_x,dest_y,last,valid,ready,inflight\r\n";
}

std::string format_transfer_line(const Transfer& t, const Geometry& geometry) {
    const auto& [cycle, scope, node, direction, packet_type, source_id, epoch, block_id, dest_x, dest_y, last] = t;
    std::string line;
    line += std::to_string(cycle) + ",accept," + scope + "," + std::to_string(node) + "," +
            std::to_string(node % geometry.mesh_x) + "," + std::to_string(node / geometry.mesh_x) + "," + direction +
            "," + std::to_string(packet_type) + "," + std::to_string(source_id) + "," + std::to_string(epoch) + "," +
            std::to_string(block_id) + "," + std::to_string(dest_x) + "," + std::to_string(dest_y) + "," +
            std::to_string(last) + ",1,1,\r\n";
    return line;
}

std::string write_transfer_trace(const std::string& path, std::vector<Transfer> transfers, const Geometry& geometry) {
    std::filesystem::path p(path);
    if (p.has_parent_path()) std::filesystem::create_directories(p.parent_path());
    std::sort(transfers.begin(), transfers.end());
    std::ofstream handle(path, std::ios::binary);
    handle << transfer_trace_header();
    for (const auto& t : transfers) handle << format_transfer_line(t, geometry);
    return path;
}

}  // namespace azilla
