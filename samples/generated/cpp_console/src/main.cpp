// APIA-generated C++ console entrypoint (skeleton + core feature).
#include <iostream>
#include "calc_console_lib.h"

int main() {
  // Run the real core feature and print its result so the console app is
  // genuinely runnable (not just a stub print).
  std::cout << "2 + 3 = " << app::add(2, 3) << std::endl;
  std::cout << "10 - 4 = " << app::subtract(10, 4) << std::endl;
  return 0;
}
