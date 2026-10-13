# Partial verification notice (gui)

APIA generated this project as an HONEST-PARTIAL shape: `gui` cannot be
fully auto-verified in a headless environment (no display / no target hardware /
no privileged kernel). APIA emitted the skeleton, a build check and unit tests,
but it will NEVER report a faked green for this shape.

What APIA verified: the skeleton builds/compiles and its unit tests run.

What needs YOUR manual check: the runtime behavior specific to this shape
(window/UI interaction, on-device execution, or driver load), which no automated
step in this environment can prove.
