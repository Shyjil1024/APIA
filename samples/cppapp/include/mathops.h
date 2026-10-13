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

// Return the product of two numbers.
//
// APIA worked-example NEW behavior (FEAT-004): added alongside the C++ Layer-1
// artifact producer (src/report_main.cpp), the C++ counterpart of pyapp's
// calc.operations.multiply. Covered by tests/test_mathops.cpp.
int multiply(int a, int b);

}  // namespace mathops

#endif  // CPPAPP_MATHOPS_H
