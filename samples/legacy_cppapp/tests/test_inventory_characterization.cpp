// Characterization test (APIA, C++/GoogleTest) for legacy_cppapp inventory.
//
// APIA generated this to PIN THE CURRENT behavior of the originally untested
// legacy module BEFORE any change, so regressions become detectable. These
// assertions document what inventory.cpp *does today* (observed by running the
// current code), not what it *should* do -- including its quirks (e.g.
// apply_discount performs no validation, so a negative percent INCREASES the
// price).
//
// Shape follows the adapter's characterization_test_template
// (.github/skills/apia-test-authoring/templates/cpp_characterization_test.cpp.tmpl).
// It performs NO network or LLM calls.

#include <gtest/gtest.h>

#include <utility>
#include <vector>

#include "inventory.h"

// --- apply_discount: pins current (unvalidated) behavior ---------------------

TEST(ApplyDiscount, ReducesPrice) {
    // Observed: 100 with a 10% discount -> 90.0
    EXPECT_DOUBLE_EQ(inventory::apply_discount(100, 10), 90.0);
}

TEST(ApplyDiscount, ZeroPercentIsIdentity) {
    // Observed: a 0% discount returns the price unchanged.
    EXPECT_DOUBLE_EQ(inventory::apply_discount(50, 0), 50.0);
}

TEST(ApplyDiscount, NegativePercentIncreasesPrice) {
    // QUIRK pinned: no validation on percent, so a negative percent raises the
    // price. Current behavior: 100 with -10% -> 110.0.
    EXPECT_DOUBLE_EQ(inventory::apply_discount(100, -10), 110.0);
}

// --- restock -----------------------------------------------------------------

TEST(Restock, AddsIncoming) {
    // Observed: 5 + 3 -> 8.
    EXPECT_EQ(inventory::restock(5, 3), 8);
}

// --- is_low_stock ------------------------------------------------------------

TEST(IsLowStock, AtDefaultThreshold) {
    // Observed: level 5 with default threshold 5 is low (<=).
    EXPECT_TRUE(inventory::is_low_stock(5));
}

TEST(IsLowStock, AboveDefaultThreshold) {
    // Observed: level 6 with default threshold 5 is not low.
    EXPECT_FALSE(inventory::is_low_stock(6));
}

TEST(IsLowStock, CustomThreshold) {
    // Observed: level 3 with threshold 2 is not low (3 <= 2 is False).
    EXPECT_FALSE(inventory::is_low_stock(3, 2));
}

// --- total_value -------------------------------------------------------------

TEST(TotalValue, SumsQuantityTimesPrice) {
    // Observed: [(2, 3.0), (1, 4.5)] -> 10.5.
    std::vector<std::pair<int, double>> items{{2, 3.0}, {1, 4.5}};
    EXPECT_DOUBLE_EQ(inventory::total_value(items), 10.5);
}

TEST(TotalValue, EmptyIsZero) {
    // Observed: an empty list totals 0.
    std::vector<std::pair<int, double>> items{};
    EXPECT_DOUBLE_EQ(inventory::total_value(items), 0.0);
}
