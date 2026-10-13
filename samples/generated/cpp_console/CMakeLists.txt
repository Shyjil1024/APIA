# APIA-generated C++ console skeleton (CMake + GoogleTest via FetchContent).
#
# Mirrors samples/cppapp: a library target holds the logic, a console executable
# links it, and GoogleTest is fetched so the skeleton is self-contained. Feature
# logic is written live by Copilot; this is the buildable+testable stub.
cmake_minimum_required(VERSION 3.14)
project(calc_console CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(calc_console_lib src/calc_console_lib.cpp)
target_include_directories(calc_console_lib PUBLIC include)

add_executable(calc_console src/main.cpp)
target_link_libraries(calc_console PRIVATE calc_console_lib)

include(FetchContent)
FetchContent_Declare(
  googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG v1.14.0
)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

enable_testing()
# The smoke suite is the emitted skeleton test; test_calc.cpp is the core-feature
# suite (models the live-generated tests). Both are compiled into one GoogleTest
# binary and registered with CTest.
add_executable(test_smoke tests/test_smoke.cpp tests/test_calc.cpp)
target_link_libraries(test_smoke PRIVATE calc_console_lib GTest::gtest_main)
include(GoogleTest)
gtest_discover_tests(test_smoke)
