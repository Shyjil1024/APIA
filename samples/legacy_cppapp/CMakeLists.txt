# Portable CMake + GoogleTest sample for APIA (Phase-2 C++ legacy / no-tests path).
#
# This library shipped originally WITHOUT tests (the characterization-test
# target, the C++ counterpart of samples/legacy_pyapp). The committed
# GoogleTest suites below are the worked example: a characterization suite that
# pins the current behavior, plus a new-behavior test for an added function.
# GoogleTest is pulled in via FetchContent (pinned v1.14.0) so the sample is
# portable and `ctest` is green.
cmake_minimum_required(VERSION 3.14)
project(legacy_cppapp CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Library under characterization.
add_library(inventory src/inventory.cpp)
target_include_directories(inventory PUBLIC include)

# --- Tests -----------------------------------------------------------------
include(FetchContent)
FetchContent_Declare(
  googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG v1.14.0
)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

enable_testing()

add_executable(test_inventory
  tests/test_inventory_characterization.cpp
  tests/test_reorder.cpp)
target_link_libraries(test_inventory PRIVATE inventory GTest::gtest_main)

include(GoogleTest)
gtest_discover_tests(test_inventory)
