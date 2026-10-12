// GoogleTest suite for cppapp mathops (APIA Phase-2 green baseline).
//
// Intentionally small and passing so it serves as the regression-guard baseline
// for the APIA ingest -> clarify -> modify -> test -> apply loop (the C++
// counterpart of samples/pyapp/tests/test_operations.py). Performs NO network
// or LLM calls.

#include <gtest/gtest.h>

#include <stdexcept>

#include "mathops.h"

TEST(Add, AddsTwoPositives) { EXPECT_EQ(mathops::add(2, 3), 5); }

TEST(Add, AddsNegativeAndPositive) { EXPECT_EQ(mathops::add(-4, 1), -3); }

TEST(Subtract, SubtractsTwoNumbers) { EXPECT_EQ(mathops::subtract(10, 4), 6); }

TEST(Divide, DividesEvenly) { EXPECT_EQ(mathops::divide(12, 3), 4); }

TEST(Divide, ThrowsOnDivideByZero) {
    EXPECT_THROW(mathops::divide(1, 0), std::invalid_argument);
}
