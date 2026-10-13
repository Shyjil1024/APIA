// New-behavior test (APIA, C++/GoogleTest) for legacy_cppapp inventory.
//
// After the characterization suite pinned the current behavior, APIA added a
// NEW function (reorder_quantity) without altering any existing function. This
// test covers the new behavior; the characterization suite stays green,
// proving the change was safe. Performs NO network or LLM calls.

#include <gtest/gtest.h>

#include "inventory.h"

TEST(ReorderQuantity, ZeroWhenNotLow) {
    // Not low (10 > default threshold 5) -> no reorder.
    EXPECT_EQ(inventory::reorder_quantity(10), 0);
}

TEST(ReorderQuantity, FillsUpToTargetWhenLow) {
    // Low (3 <= 5) -> reorder up to default target 10 -> 7.
    EXPECT_EQ(inventory::reorder_quantity(3), 7);
}

TEST(ReorderQuantity, ZeroWhenLowButAlreadyAtTarget) {
    // Low by threshold but already at/above target -> no reorder.
    EXPECT_EQ(inventory::reorder_quantity(12, 15, 10), 0);
}
