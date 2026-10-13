#include "inventory.h"

namespace inventory {

double apply_discount(double price, double percent) {
    return price - (price * percent / 100.0);
}

int restock(int current, int incoming) { return current + incoming; }

bool is_low_stock(int level, int threshold) { return level <= threshold; }

double total_value(const std::vector<std::pair<int, double>>& items) {
    double total = 0.0;
    for (const auto& item : items) {
        total += item.first * item.second;
    }
    return total;
}

int reorder_quantity(int level, int threshold, int target) {
    if (!is_low_stock(level, threshold)) {
        return 0;
    }
    int needed = target - level;
    return needed > 0 ? needed : 0;
}

}  // namespace inventory
