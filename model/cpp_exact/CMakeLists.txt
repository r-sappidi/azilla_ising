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
  core_iteration.cpp core_state_bank.cpp shared_fetch.cpp endpoint_pipeline.cpp cores_only_events.cpp
)
target_compile_options(azilla_exact PRIVATE -Wall -Wextra -O2)
target_link_libraries(azilla_exact PRIVATE ${CMAKE_DL_LIBS})
