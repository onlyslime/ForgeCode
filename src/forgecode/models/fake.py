"""Deterministic provider used for offline demonstrations and tests."""

from .protocol import Message, ModelResponse, ToolCall


class DemoProvider:
    """Exercise inspect -> write -> fail -> repair without network access."""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-1", "list_files", {"pattern": "README.md"}),)))
        if self.calls == 2:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-2", "read_file", {"path": "README.md", "max_chars": 400}),)))
        if self.calls == 3:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-3", "write_file", {"path": ".forgecode/demo.txt", "content": "ForgeCode demo\n"}),)))
        if self.calls == 4:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-4", "run_command", {"command": "python -c \"import sys; print('intentional failure'); sys.exit(1)\""}),)))
        if self.calls == 5:
            return ModelResponse(Message("assistant", tool_calls=(ToolCall("demo-5", "run_command", {"command": "python -c \"print('repair passed')\""}),)))
        return ModelResponse(Message("assistant", "Demo task completed after inspecting the README, creating a file, observing a failed command, and repairing it."))
