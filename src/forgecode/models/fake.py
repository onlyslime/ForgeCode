"""Deterministic provider used for offline demonstrations and tests."""

from .protocol import Message, ModelResponse, ToolCall


class DemoProvider:
    """Exercise a real inspect -> failing test -> patch -> passing test flow."""

    def __init__(self):
        self.calls = 0
        self.plan_mode = False

    async def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            available = {schema.get("function", {}).get("name") for schema in tools}
            self.plan_mode = "run_command" not in available
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-1", "workspace_summary", {}),)))
        if self.plan_mode:
            return ModelResponse(Message("assistant", "Plan: inspect the relevant source and tests, identify the failing behavior, then switch to act mode to apply a reviewed patch and run verification. No side effects were requested."))
        if self.calls == 2:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-2", "read_file", {"path": "demo_calculator.py", "max_chars": 2_000}),)))
        if self.calls == 3:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-3", "run_command", {"command": "python -B -m pytest -q test_demo_calculator.py"}),)))
        if self.calls == 4:
            patch = """--- a/demo_calculator.py
+++ b/demo_calculator.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-4", "apply_patch", {"patch": patch}),)))
        if self.calls == 5:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-5", "run_command", {"command": "python -B -m pytest -q test_demo_calculator.py"}),)))
        return ModelResponse(Message("assistant", "Demo task completed: I inspected the calculator, observed a failing test, applied a reviewed patch, and reran the test successfully."))
