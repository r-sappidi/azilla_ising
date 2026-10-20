cmake_minimum_required(VERSION 3.16)
project(azilla_cpp_exact CXX)

# C++ port of the Python exact-event timing-only model (execution mode cir).
# Build:  cmake -S model/cpp_exact -B build/cpp_exact -DCMAKE_BUILD_TYPE=Release
#         cmake --build build/cpp_exact -j4
# Ramulator timing comes from build/cycle_model_ramulator/libazilla_ramulator.so
# (loaded with dlopen at run time; pass it with --ramulator-library).

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_executable(azilla_exact_cir
  main.cpp
  workload.cpp
  scheduler.cpp
  noc.cpp
  events.cpp
  hierarchy.cpp
  memory.cpp
  ramulator.cpp
  cross.cpp
  adapters.cpp
  exact_events.cpp
  metrics.cpp
  core_iteration.cpp core_state_bank.cpp shared_fetch.cpp endpoint_pipeline.cpp cores_only_events.cpp
)
target_compile_options(azilla_exact_cir PRIVATE -Wall -Wextra -O2)
target_link_libraries(azilla_exact_cir PRIVATE ${CMAKE_DL_LIBS})

# Second binary from the same sources.  The stage-1 binary ``azilla_exact_cir``
# was hash-pinned by results/cpp_exact_port_20261006; dense campaigns build
# this target only (``cmake --build build/cpp_exact --target azilla_exact_cir_dense``)
# so that file and its recorded SHA-256 stay untouched.
add_executable(azilla_exact_cir_dense
  main.cpp workload.cpp scheduler.cpp noc.cpp events.cpp hierarchy.cpp memory.cpp
  ramulator.cpp cross.cpp adapters.cpp exact_events.cpp metrics.cpp
  core_iteration.cpp core_state_bank.cpp shared_fetch.cpp endpoint_pipeline.cpp cores_only_events.cpp
)
target_compile_options(azilla_exact_cir_dense PRIVATE -Wall -Wextra -O2)
target_link_libraries(azilla_exact_cir_dense PRIVATE ${CMAKE_DL_LIBS})

# Stage 3: unified binary (execution modes cir and cores-only with the
# streaming / buffered-v3 endpoint policies, mapping-artifact input).  Built as
# its own target so the hash-pinned stage-1 binaries above stay untouched:
#   cmake --build <build dir> --target azilla_exact
add_executable(azilla_exact
  main.cpp workload.cpp scheduler.cpp noc.cpp events.cpp hierarchy.cpp memory.cpp
  ramulator.cpp cross.cpp adapters.cpp exact_events.cpp metrics.cpp
  core_iteration.cpp core_state_bank.cpp shared_fetch.cpp endpoint_pipeline.cpp cores_only_events.cpp anchored.cpp
)
target_compile_options(azilla_exact PRIVATE -Wall -Wextra -O2)
target_link_libraries(azilla_exact PRIVATE ${CMAKE_DL_LIBS})

# Threaded DRAM ticking.  The unified binary links the thread library; the
# multi-thread-capable Ramulator bridge is a separate shared library
# (libazilla_ramulator_mt.so, same C ABI as tb/ramulator_dpi.cpp plus
# az_dram_tick_system / az_dram_advance_clock), built on request only:
#   cmake --build <build dir> --target azilla_ramulator_mt
# It links the existing third_party/ramulator2/libramulator.so and never
# replaces build/cycle_model_ramulator/libazilla_ramulator.so.
find_package(Threads REQUIRED)
target_link_libraries(azilla_exact PRIVATE Threads::Threads)
target_link_libraries(azilla_exact_cir PRIVATE Threads::Threads)
target_link_libraries(azilla_exact_cir_dense PRIVATE Threads::Threads)

set(AZILLA_RAMULATOR_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/ramulator2"
    CACHE PATH "Ramulator2 checkout providing src/ headers and libramulator.so")
get_filename_component(AZILLA_RAMULATOR_DIR "${AZILLA_RAMULATOR_DIR}" ABSOLUTE)
add_library(azilla_ramulator_mt SHARED EXCLUDE_FROM_ALL ramulator_bridge_mt.cpp)
target_include_directories(azilla_ramulator_mt PRIVATE "${AZILLA_RAMULATOR_DIR}/src")
target_compile_options(azilla_ramulator_mt PRIVATE -Wall -Wextra -O2)
target_link_directories(azilla_ramulator_mt PRIVATE "${AZILLA_RAMULATOR_DIR}")
target_link_libraries(azilla_ramulator_mt PRIVATE ramulator)
set_target_properties(azilla_ramulator_mt PROPERTIES
  OUTPUT_NAME azilla_ramulator_mt
  BUILD_RPATH "${AZILLA_RAMULATOR_DIR}"
  INSTALL_RPATH "${AZILLA_RAMULATOR_DIR}")
