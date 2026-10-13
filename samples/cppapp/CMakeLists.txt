# Portable CMake + GoogleTest sample for APIA (Phase-2 C++ green baseline).
#
# GoogleTest is pulled in via FetchContent (pinned to v1.14.0) so this sample is
# self-contained and portable: no system-wide GoogleTest install is required.
# Mirrors samples/pyapp (existing passing tests) for the C++ stack.
cmake_minimum_required(VERSION 3.14)
project(cppapp CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Library under test.
add_library(mathops src/mathops.cpp)
target_include_directories(mathops PUBLIC include)

# Deterministic Layer-1 artifact producer (APIA FEAT-004). Runs the real
# mathops arithmetic and emits a stable JSON report the self-correction loop
# grades against a committed golden (tests/golden/report.json). Built as part of
# the normal `cmake --build build`, so the e2e proof can run the real binary.
add_executable(report_main src/report_main.cpp)
target_link_libraries(report_main PRIVATE mathops)

# --- Tests -----------------------------------------------------------------
include(FetchContent)
FetchContent_Declare(
  googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG v1.14.0
)
# Keep GoogleTest from clobbering parent project settings on Windows.
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

enable_testing()

add_executable(test_mathops tests/test_mathops.cpp)
target_link_libraries(test_mathops PRIVATE mathops GTest::gtest_main)

include(GoogleTest)
gtest_discover_tests(test_mathops)
