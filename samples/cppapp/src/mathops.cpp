#include "mathops.h"

#include <stdexcept>

namespace mathops {

int add(int a, int b) { return a + b; }

int subtract(int a, int b) { return a - b; }

int divide(int a, int b) {
    if (b == 0) {
        throw std::invalid_argument("cannot divide by zero");
    }
    return a / b;
}

int multiply(int a, int b) { return a * b; }

}  // namespace mathops
