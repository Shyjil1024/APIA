// A small, originally untested legacy C++ module.
//
// No accompanying tests existed on purpose: this is the characterization-test
// target (the C++ counterpart of samples/legacy_pyapp/legacy_pyapp/inventory.py).
// Behavior here (including its quirks) is the current ground truth that
// characterization tests must pin before APIA modifies anything.
#ifndef LEGACY_CPPAPP_INVENTORY_H
#define LEGACY_CPPAPP_INVENTORY_H

#include <utility>
#include <vector>

namespace inventory {

// Apply a percentage discount to a price.
//
// Note: performs no validation on `percent` (legacy behavior kept as-is), so a
// negative percent INCREASES the price.
double apply_discount(double price, double percent);

// Return the new stock level after receiving an incoming shipment.
int restock(int current, int incoming);

// Return true when the stock `level` is at or below `threshold`.
bool is_low_stock(int level, int threshold = 5);

// Return the total value of a list of (quantity, unit_price) pairs.
double total_value(const std::vector<std::pair<int, double>>& items);

// APIA safe-change ADDED behavior: a NEW function added without altering any
// existing function, so the characterization tests that pin the current
// behavior of the functions above stay green. Covered by test_reorder.cpp.
//
// Returns how many units to reorder to reach `target` when stock is low;
// returns 0 when stock is not low or already at/above `target`.
int reorder_quantity(int level, int threshold = 5, int target = 10);

}  // namespace inventory

#endif  // LEGACY_CPPAPP_INVENTORY_H
