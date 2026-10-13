# APIA-generated C++ library skeleton (CMake + GoogleTest via FetchContent).
#
# A library target + public headers + a GoogleTest suite. Feature logic is
# written live by Copilot; this is the buildable+testable stub.
cmake_minimum_required(VERSION 3.14)
project(stringkit CXX)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(stringkit_lib src/stringkit_lib.cpp)
target_include_directories(stringkit_lib PUBLIC include)

include(FetchContent)
FetchContent_Declare(
  googletest
  GIT_REPOSITORY https://github.com/google/googletest.git
  GIT_TAG v1.14.0
)
set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
FetchContent_MakeAvailable(googletest)

enable_testing()
# The smoke suite is the emitted skeleton test; test_stringkit.cpp is the
# core-feature suite (models the live-generated tests). Both compile into one
# GoogleTest binary registered with CTest.
add_executable(test_smoke tests/test_smoke.cpp tests/test_stringkit.cpp)
target_link_libraries(test_smoke PRIVATE stringkit_lib GTest::gtest_main)
include(GoogleTest)
gtest_discover_tests(test_smoke)
