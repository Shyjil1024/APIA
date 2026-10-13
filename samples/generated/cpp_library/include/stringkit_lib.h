// APIA-generated skeleton header + core feature.
//
// The skeleton emits the stub ``answer()`` so the library links and tests from
// the first increment. The string helpers below model the LIVE Copilot
// generation step: a minimal, idiomatic public API for a reusable library.
#pragma once

#include <string>

namespace app {
// Smoke symbol kept so the generated smoke test still links + passes.
int answer();

// Core feature: small string helpers.
std::string to_upper(const std::string& s);
std::string reverse(const std::string& s);
bool is_palindrome(const std::string& s);
}  // namespace app
