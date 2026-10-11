// Command-line front end mirroring ``python3 -m azilla_cycle_model.cli
// simulate-exact-events`` (execution mode ``cir``) and, for execution mode
// ``cores-only``, ``python3 -m azilla_cycle_model.streaming_cli`` (policy
// streaming) / ``shared_fetch_double_cli`` (policy buffered-v3), plus
// ``simulate-mapped-exact-events --artifact DIR`` for both.
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <optional>
#include <regex>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include "exact_events.hpp"
#include "metrics.hpp"

using namespace azilla;

namespace {

struct Args {
    std::map<std::string, std::string> values;
    bool has(const std::string& key) const { return values.count(key) != 0; }
    std::string str(const std::string& key, const std::string& fallback = "") const {
        auto it = values.find(key);
        return it == values.end() ? fallback : it->second;
    }
    long num(const std::string& key, long fallback) const {
        auto it = values.find(key);
        return it == values.end() ? fallback : std::stol(it->second);
    }
};

void usage() {
    std::fprintf(stderr,
                 "usage: azilla_exact simulate-exact-events --dataset PATH --mesh-x N --mesh-y N\n"
                 "       --h0-per-h1 N --cores-per-h0 N --ramulator-library PATH --ramulator-config PATH\n"
                 "       [--schedule PATH] [--h0-mvms N] [--h1-mvms N] [--cross-mvms N] [--fifo-depth N]\n"
                 "       [--mem-lanes N] [--ticks-per-cycle N] [--idle-refresh-period-ticks N] [--max-cycles N]\n"
                 "       [--metrics-prefix PREFIX] [--transfer-trace PATH] [--execution-mode cir|cores-only]\n"
                 "       [--link-latency-cycles N] [--link-flit-interval-cycles N] [--link-max-inflight-flits N]\n"
                 "       [--link-credit-return-cycles N] [--chiplet-link-latency-cycles N]\n"
                 "       [--dense-all-blocks 1] [--transfer-trace-stream PATH]\n"
                 "       [--endpoint-policy streaming|buffered-v3] [--stall-period N] [--core-events PATH]\n"
                 "   or: azilla_exact simulate-mapped-exact-events --artifact DIR --dataset PATH ... (same options,\n"
                 "       geometry/schedule/occupancy from the mapping artifact)\n"
                 "  --dense-all-blocks 1     occupy every unordered off-diagonal block pair (header-only dataset),\n"
                 "                           as the Python dense campaigns' implicit BlockOccupancyDataset(n, 0, pairs)\n"
                 "  --transfer-trace-stream  write accepted transfers as they occur (generation order, unsorted;\n"
                 "                           same rows as --transfer-trace) without retaining them in memory\n"
                 "  --endpoint-policy        cores-only only: streaming (azilla_cycle_model.streaming_cli, default)\n"
                 "                           or buffered-v3 (azilla_cycle_model.shared_fetch_double_cli)\n"
                 "  --stall-period N         cores-only only: test-only weight stall period of the RTL checkers\n"
                 "  --core-events PATH       cores-only only: dump the core/memory/state observer streams as CSV\n");
}

// Minimal reader for the fields of a mapping artifact manifest.json that
// MappingArtifact.load needs (schema, geometry, spin_count, known_cut,
// tile_size, scheduled_blocks, text schedule file).  The schedule is read
// from the text file the artifact writes next to its .npy arrays; it carries
// the same (block_a, block_b, owner) records as block_pairs.npy / owners.npy.
struct MappingArtifactText {
    Geometry geometry;
    long spin_count = 0;
    long known_cut = 0;
    std::vector<ScheduledBlock> schedule;
};

long manifest_int(const std::string& text, const std::string& key) {
    std::regex pattern("\"" + key + "\"\\s*:\\s*(-?[0-9]+)");
    std::smatch match;
    if (!std::regex_search(text, match, pattern)) throw std::runtime_error("manifest lacks integer field " + key);
    return std::stol(match[1]);
}

std::string manifest_string(const std::string& text, const std::string& key) {
    std::regex pattern("\"" + key + "\"\\s*:\\s*\"([^\"]*)\"");
    std::smatch match;
    if (!std::regex_search(text, match, pattern)) throw std::runtime_error("manifest lacks string field " + key);
    return match[1];
}

MappingArtifactText load_mapping_artifact(const std::string& directory) {
    std::filesystem::path dir(directory);
    std::ifstream input(dir / "manifest.json");
    if (!input) throw std::runtime_error("cannot open " + (dir / "manifest.json").string());
    std::stringstream buffer;
    buffer << input.rdbuf();
    std::string text = buffer.str();
    if (manifest_string(text, "schema") != "azilla-sparse-mapping-v1")
        throw std::runtime_error("unsupported mapping artifact schema");
    if (manifest_int(text, "tile_size") != 32) throw std::runtime_error("Azilla RTL requires 32-spin blocks");
    MappingArtifactText artifact;
    artifact.geometry = Geometry{int(manifest_int(text, "mesh_x")), int(manifest_int(text, "mesh_y")),
                                 int(manifest_int(text, "h0_per_h1")), int(manifest_int(text, "cores_per_h0"))};
    artifact.spin_count = manifest_int(text, "spin_count");
    std::regex known_cut("\"known_cut\"\\s*:\\s*(-?[0-9]+)");
    std::smatch match;
    artifact.known_cut = std::regex_search(text, match, known_cut) ? std::stol(match[1]) : 0;
    std::string schedule_file = manifest_string(text, "text_schedule");
    artifact.schedule = load_schedule((dir / schedule_file).string());
    long scheduled_blocks = manifest_int(text, "scheduled_blocks");
    if (long(artifact.schedule.size()) != scheduled_blocks)
        throw std::runtime_error("mapping artifact text schedule has " + std::to_string(artifact.schedule.size()) +
                                 " records; manifest says " + std::to_string(scheduled_blocks));
    if (artifact.spin_count != artifact.geometry.spin_count())
        throw std::runtime_error("mapping artifact spin_count does not match its geometry");
    const int blocks_per_h1 = artifact.geometry.blocks_per_h1();
    std::set<BlockPair> seen;
    for (const auto& record : artifact.schedule) {
        if (!(0 <= record.block_a && record.block_a < record.block_b && record.block_b < artifact.geometry.total_blocks()))
            throw std::runtime_error("block pair outside geometry or not strictly upper triangular");
        if (!seen.insert({record.block_a, record.block_b}).second) throw std::runtime_error("duplicate block pair");
        bool cross = record.block_a / blocks_per_h1 != record.block_b / blocks_per_h1;
        if (cross && !(0 <= record.owner && record.owner < artifact.geometry.node_count()))
            throw std::runtime_error("cross-H1 pair has no owner");
        if (!cross && record.owner != -1) throw std::runtime_error("local pair cannot move");
    }
    return artifact;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 2 || (std::string(argv[1]) != "simulate-exact-events" &&
                         std::string(argv[1]) != "simulate-mapped-exact-events")) {
            usage();
            return 2;
        }
        const std::string command = argv[1];
        const bool mapped = command == "simulate-mapped-exact-events";
        Args args;
        for (int i = 2; i < argc; ++i) {
            std::string key = argv[i];
            if (key.rfind("--", 0) != 0 || i + 1 >= argc) {
                usage();
                return 2;
            }
            args.values[key.substr(2)] = argv[++i];
        }
        std::vector<const char*> required = {"dataset", "ramulator-library", "ramulator-config"};
        if (mapped) required.push_back("artifact");
        else for (const char* r : {"mesh-x", "mesh-y", "h0-per-h1", "cores-per-h0"}) required.push_back(r);
        for (const char* r : required)
            if (!args.has(r)) {
                std::fprintf(stderr, "missing --%s\n", r);
                usage();
                return 2;
            }
        std::string execution_mode = args.str("execution-mode", "cir");
        if (execution_mode != "cir" && execution_mode != "cores-only") {
            std::fprintf(stderr, "the C++ port supports --execution-mode cir or cores-only\n");
            return 2;
        }
        std::string endpoint_policy = args.str("endpoint-policy", "streaming");
        if (execution_mode == "cir" && (args.has("endpoint-policy") || args.has("stall-period") || args.has("core-events"))) {
            std::fprintf(stderr, "--endpoint-policy/--stall-period/--core-events apply to --execution-mode cores-only only\n");
            return 2;
        }
        const bool dense_all_blocks = args.has("dense-all-blocks") && args.str("dense-all-blocks") != "0";
        if (args.has("transfer-trace") && args.has("transfer-trace-stream")) {
            std::fprintf(stderr, "use either --transfer-trace or --transfer-trace-stream, not both\n");
            return 2;
        }
        Geometry geometry;
        BlockOccupancyDataset dataset;
        std::optional<std::vector<ScheduledBlock>> schedule;
        std::optional<MappingArtifactText> artifact;
        if (mapped) {
            if (dense_all_blocks || args.has("schedule")) {
                std::fprintf(stderr, "simulate-mapped-exact-events takes geometry, occupancy and schedule from --artifact\n");
                return 2;
            }
            artifact = load_mapping_artifact(args.str("artifact"));
            geometry = artifact->geometry;
            dataset.spin_count = artifact->spin_count;
            dataset.known_cut = artifact->known_cut;
            for (const auto& record : artifact->schedule) dataset.active_pairs.emplace_back(record.block_a, record.block_b);
            std::sort(dataset.active_pairs.begin(), dataset.active_pairs.end());
            schedule = artifact->schedule;
            validate_schedule(dataset, geometry, *schedule);
        } else {
            geometry = Geometry{int(args.num("mesh-x", 1)), int(args.num("mesh-y", 1)), int(args.num("h0-per-h1", 1)),
                                int(args.num("cores-per-h0", 1))};
            dataset = dense_all_blocks ? BlockOccupancyDataset::dense_all_blocks(args.str("dataset"))
                                       : BlockOccupancyDataset::load(args.str("dataset"));
            if (args.has("schedule")) {
                schedule = load_schedule(args.str("schedule"));
                validate_schedule(dataset, geometry, *schedule);
            }
        }
        PerformanceConfig config;
        config.h0_mvm_count = int(args.num("h0-mvms", 1));
        config.h1_mvm_count = int(args.num("h1-mvms", 1));
        config.cross_mvm_count = int(args.num("cross-mvms", 1));
        config.fifo_depth = int(args.num("fifo-depth", 4));
        config.max_cycles = args.num("max-cycles", 1000000000L);
        config.chiplet_link_latency_cycles = int(args.num("chiplet-link-latency-cycles", 0));
        config.interconnect.forward_latency_cycles = int(args.num("link-latency-cycles", 0));
        config.interconnect.flit_interval_cycles = int(args.num("link-flit-interval-cycles", 1));
        config.interconnect.max_inflight_flits = int(args.num("link-max-inflight-flits", 4));
        config.interconnect.credit_return_latency_cycles = int(args.num("link-credit-return-cycles", 0));
        config.execution_mode = execution_mode;
        config.endpoint_policy = endpoint_policy;
        config.stall_period = int(args.num("stall-period", 0));
        long idle_refresh = args.num("idle-refresh-period-ticks", 0);

        RamulatorEventPerformanceModel model(geometry, dataset, args.str("dataset"), args.str("ramulator-library"),
                                             args.str("ramulator-config"), int(args.num("mem-lanes", 16)),
                                             int(args.num("ticks-per-cycle", 40)), idle_refresh > 0 ? idle_refresh : 0,
                                             config);
        if (args.has("transfer-trace-stream")) model.open_transfer_stream(args.str("transfer-trace-stream"));
        if (args.has("core-events")) model.open_event_stream(args.str("core-events"));
        ExactEventResult result;
        try {
            result = model.run(schedule ? &*schedule : nullptr);
        } catch (...) {
            model.close();
            throw;
        }
        model.close();
        model.close_transfer_stream();
        model.close_event_stream();

        std::map<std::string, std::string> metric_paths;
        if (args.has("metrics-prefix"))
            metric_paths = write_exact_event_metrics(args.str("metrics-prefix"), result, geometry);
        std::string transfer_path;
        if (args.has("transfer-trace"))
            transfer_path = write_transfer_trace(args.str("transfer-trace"), model.transfers, geometry);

        std::printf("PASS mode=%s accuracy=%s spins=%ld initialization_cycles=%ld iteration_cycles=%ld "
                    "total_cycles=%ld\n",
                    mapped ? "mapped-exact-events" : "exact-events", result.accuracy.c_str(), geometry.spin_count(),
                    result.initialization_cycles, result.iteration_cycles, result.total_cycles);
        std::printf("interconnect latency_cycles=%d flit_interval_cycles=%d max_inflight_flits=%d credit_return_cycles=%d\n",
                    config.interconnect.forward_latency_cycles, config.interconnect.flit_interval_cycles,
                    config.interconnect.max_inflight_flits, config.interconnect.credit_return_latency_cycles);
        std::printf("package chiplet_link_latency_cycles=%d\n", config.chiplet_link_latency_cycles);
        std::printf("jobs h0=%ld h1=%ld cross=%ld\n", result.scheduled_h0, result.scheduled_h1, result.scheduled_cross);
        const auto& c = result.counters;
        std::printf("noc injected_flits=%ld ejected_flits=%ld physical_link_flits=%ld injection_stalls=%ld "
                    "ejection_stalls=%ld link_stalls=%ld\n",
                    c.injected_flits, c.ejected_flits, c.physical_link_flits, c.injection_stalls, c.ejection_stalls,
                    c.link_stalls);
        std::string histogram = "{";
        for (size_t i = 0; i < result.hop_histogram.size(); ++i) {
            if (i) histogram += ", ";
            histogram += std::to_string(result.hop_histogram[i].first) + ": " +
                         std::to_string(result.hop_histogram[i].second);
        }
        histogram += "}";
        std::printf("noc average_hops=%.6f hop_histogram=%s\n", result.average_hops, histogram.c_str());
        if (!metric_paths.empty()) {
            std::printf("metrics summary=%s noc=%s nodes=%s dram=%s\n", metric_paths["summary"].c_str(),
                        metric_paths["noc"].c_str(), metric_paths["nodes"].c_str(), metric_paths["dram"].c_str());
        }
        if (!transfer_path.empty()) std::printf("transfer_trace=%s\n", transfer_path.c_str());
        if (args.has("transfer-trace-stream"))
            std::printf("transfer_trace_stream=%s streamed_transfers=%ld\n", args.str("transfer-trace-stream").c_str(),
                        model.streamed_transfers());
        if (args.has("core-events")) std::printf("core_events=%s\n", args.str("core-events").c_str());
        if (dense_all_blocks)
            std::printf("dense_all_blocks=1 unordered_block_pairs=%zu\n", dataset.active_pairs.size());
        if (execution_mode == "cores-only")
            std::printf("cores_only directed_jobs=%ld endpoint_policy=%s stall_period=%d\n", result.directed_core_jobs,
                        endpoint_policy.c_str(), config.stall_period);
        std::printf("implementation=cpp_exact stage=3 execution_mode=%s\n", execution_mode.c_str());
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "error: %s\n", error.what());
        return 1;
    }
}
