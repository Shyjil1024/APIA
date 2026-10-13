// Basic arithmetic operations.
//
// Intentionally small and well-tested so it can serve as the regression-guard
// baseline for the APIA ingest -> clarify -> modify -> test -> apply loop
// (the C++ counterpart of samples/pyapp/calc/operations.py).
#ifndef CPPAPP_MATHOPS_H
#define CPPAPP_MATHOPS_H

namespace mathops {

// Return the sum of two numbers.
int add(int a, int b);

// Return the difference of two numbers (a - b).
int subtract(int a, int b);

// Return the quotient a / b.
//
// Throws std::invalid_argument if b is zero.
int divide(int a, int b);

}  // namespace mathops

#endif  // CPPAPP_MATHOPS_H
