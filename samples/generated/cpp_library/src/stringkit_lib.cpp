// APIA-generated skeleton source + core feature.
#include "stringkit_lib.h"

#include <algorithm>
#include <cctype>

namespace app {
// Smoke stub kept so the generated smoke test still links + passes.
int answer() { return 42; }

std::string to_upper(const std::string& s) {
  std::string out = s;
  std::transform(out.begin(), out.end(), out.begin(),
                 [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
  return out;
}

std::string reverse(const std::string& s) {
  return std::string(s.rbegin(), s.rend());
}

bool is_palindrome(const std::string& s) {
  return s == reverse(s);
}
}  // namespace app
