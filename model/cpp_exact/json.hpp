// Minimal JSON value used by metrics.cpp to reproduce Python
// ``json.dump(indent=2, sort_keys=True)`` byte for byte.  Shared with the
// cores-only/streaming port, whose ``core_pipeline_contract`` is a nested
// dictionary in the Python result.
#pragma once

#include <cstddef>
#include <map>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace azilla {

struct Json;
using JsonObject = std::map<std::string, Json>;  // std::map order == Python sort_keys order for str keys
// Object emitted in insertion order: for Python dicts whose keys are ints
// (Counter audits), where sort_keys sorts numerically before json converts
// the keys to strings ("9" before "10").
using JsonOrdered = std::vector<std::pair<std::string, Json>>;
using JsonArray = std::vector<Json>;

struct Json {
    std::variant<std::nullptr_t, bool, long long, double, std::string, JsonArray, JsonObject, JsonOrdered> value;
    Json() : value(nullptr) {}
    Json(bool b) : value(b) {}
    Json(int i) : value(static_cast<long long>(i)) {}
    Json(long i) : value(static_cast<long long>(i)) {}
    Json(long long i) : value(i) {}
    Json(unsigned long i) : value(static_cast<long long>(i)) {}
    Json(unsigned long long i) : value(static_cast<long long>(i)) {}
    Json(double d) : value(d) {}
    Json(const char* s) : value(std::string(s)) {}
    Json(const std::string& s) : value(s) {}
    Json(const JsonArray& a) : value(a) {}
    Json(const JsonObject& o) : value(o) {}
    Json(const JsonOrdered& o) : value(o) {}
};

}  // namespace azilla
