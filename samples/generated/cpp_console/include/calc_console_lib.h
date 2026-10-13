// APIA-generated skeleton header + core feature.
//
// The skeleton emits the stub ``answer()`` so the project links and tests from
// the first increment. The ``add``/``subtract`` helpers below model the LIVE
// Copilot generation step: a minimal, idiomatic public API the console
// executable actually calls.
#pragma once

namespace app {
// Smoke symbol kept so the generated smoke test still links + passes.
int answer();

// Core feature: simple integer arithmetic exercised by the console app.
int add(int a, int b);
int subtract(int a, int b);
}  // namespace app
