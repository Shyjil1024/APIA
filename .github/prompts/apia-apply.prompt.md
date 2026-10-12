---
description: "APIA stage 5 (APPLY): write the approved changes, re-run the relevant tests, and prepare the commit/PR. Does not auto-push; the user confirms the final apply."
agent: apia-implementer
tools: ["edit/files", "run/terminal", "run/tests"]
---

Run the APIA **APPLY** stage.

1. Write the **approved** changes to disk (only what was approved at the MODIFY gate).
2. Re-run the relevant test suite once more via the stack adapter's `test_command`
   to confirm existing behavior still passes and the new behavior is verified.
3. Prepare the commit (and/or PR) with a concise message. **Do not auto-push.**
4. **STOP at the gate** for the user to confirm the final apply/commit.

Do not call any external LLM.
