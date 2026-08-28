"""Deterministic providers used for offline demonstrations and tests."""

from .protocol import Message, ModelCapabilities, ModelResponse, ToolCall


_DEMO_TASKS = {
    "calculator": {
        "source": "demo_calculator.py",
        "command": "python -B -m pytest -q test_demo_calculator.py",
        "patch": """--- a/demo_calculator.py
+++ b/demo_calculator.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
""",
        "final": "Demo task completed: I inspected the calculator, observed a failing test, applied a reviewed patch, and reran the test successfully.",
    },
    "json": {
        "source": "demo_config.json",
        "command": "python -B -m pytest -q test_demo_config.py",
        "patch": """--- a/demo_config.json
+++ b/demo_config.json
@@ -1,4 +1,4 @@
 {
   \"name\": \"ForgeCode demo\",
-  \"enabled\": false
+  \"enabled\": true
 }
""",
        "final": "Demo task completed: I inspected the JSON configuration, observed a failing contract test, applied a reviewed patch, and reran the test successfully.",
    },
}


class DemoProvider:
    """Exercise a real inspect -> failing test -> patch -> passing test flow."""

    def __init__(self, task: str = "calculator"):
        if task not in _DEMO_TASKS:
            raise ValueError(f"unknown demo task: {task}")
        self.spec = _DEMO_TASKS[task]
        self.calls = 0
        self.plan_mode = False

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(streaming=False, json_mode=False, max_input_chars=200_000, max_output_chars=20_000)

    def health(self) -> dict:
        return {"provider": "demo", "configured": True, "task": self.spec["source"], "capabilities": self.capabilities.to_dict()}

    async def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            available = {schema.get("function", {}).get("name") for schema in tools}
            self.plan_mode = "run_command" not in available
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-1", "workspace_summary", {}),)))
        if self.plan_mode:
            return ModelResponse(Message("assistant", "Plan: inspect the relevant source and tests, identify the failing behavior, then switch to act mode to apply a reviewed patch and run verification. No side effects were requested."))
        if self.calls == 2:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-2", "read_file", {"path": self.spec["source"], "max_chars": 2_000}),)))
        if self.calls == 3:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-3", "run_command", {"command": self.spec["command"]}),)))
        if self.calls == 4:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-4", "apply_patch", {"patch": self.spec["patch"]}),)))
        if self.calls == 5:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-5", "run_command", {"command": self.spec["command"]}),)))
        return ModelResponse(Message("assistant", self.spec["final"]))


__all__ = ["DemoProvider", "_DEMO_TASKS"]
