"""Bounded context construction for model calls."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Sequence

from ..models import Message


_MAX_CONTEXT_ITEMS = 128
_MAX_CONTEXT_TOOL_CALLS = 64


def _bounded_value(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "\n[argument truncated]"
    if isinstance(value, dict):
        items = list(value.items())
        result = {str(key): _bounded_value(item, limit) for key, item in items[:_MAX_CONTEXT_ITEMS]}
        if len(items) > _MAX_CONTEXT_ITEMS:
            result["_truncated_items"] = len(items) - _MAX_CONTEXT_ITEMS
        return result
    if isinstance(value, (list, tuple)):
        result = [_bounded_value(item, limit) for item in value[:_MAX_CONTEXT_ITEMS]]
        if len(value) > _MAX_CONTEXT_ITEMS:
            result.append(f"[omitted {len(value) - _MAX_CONTEXT_ITEMS} items]")
        return result
    return value


def _message_size(message: Message) -> int:
    calls = [
        {"id": call.id, "name": call.name, "arguments": _bounded_value(call.arguments, 4_000)}
        for call in message.tool_calls[:_MAX_CONTEXT_TOOL_CALLS]
    ]
    return len(message.content) + len(json.dumps(calls, ensure_ascii=False, default=str))


@dataclass(frozen=True)
class ContextBuilder:
    max_chars: int = 60_000
    max_message_chars: int = 16_000

    def __post_init__(self) -> None:
        if self.max_chars < 1 or self.max_message_chars < 1:
            raise ValueError("context limits must be positive")

    def system_message(self, workspace: Path, tool_names: Sequence[str], *, approval_mode: str, mode: str = "act") -> Message:
        tools = ", ".join(tool_names) or "none"
        mode_rule = (
            "You are in PLAN mode. Explore and produce a concrete plan only; file changes, commands, and verification are forbidden."
            if mode == "plan"
            else ("You are in BYPASS mode. Execute the requested task directly; do not ask for confirmations."
                  if mode == "bypass" else "You are in ACT mode. Side effects still require approval and every change must be verified.")
        )
        content = (
            "You are ForgeCode, a local coding agent.\n"
            "Workspace root: . (all paths are relative to the validated workspace)\n"
            f"Execution mode: {mode}. {mode_rule}\n"
            f"Available tools: {tools}\n"
            f"Approval mode: {approval_mode}. Never assume a denied operation ran.\n"
            "Inspect relevant files before editing, make the smallest safe change, and verify with a real command. "
            "Treat tool errors, non-zero exit codes, and test failures as context for a bounded repair attempt.\n"
            "Use a conversational three-phase workflow: (1) before starting, briefly tell the user what you will do; "
            "(2) during a multi-step task, send concise progress updates between groups of tool calls, especially when "
            "changing strategy after an error; (3) when finished, explain what changed, verification performed, and any "
            "remaining limitation. Do not remain silent for a long sequence of tools."
        )
        return Message(role="system", content=content)

    def fit(self, messages: Sequence[Message]) -> list[Message]:
        """Keep system/user intent plus the newest complete-looking context."""
        normalized = [self._truncate(message) for message in messages]
        units = self._message_units(normalized)
        if sum(_message_size(message) for message in normalized) <= self.max_chars:
            return [normalized[index] for unit in units for index in unit]

        selected_indexes: list[int] = []
        for index, message in enumerate(normalized):
            if message.role == "system" and "AUTOMATIC CONTEXT SUMMARY" in message.content:
                selected_indexes.append(index)
        for index, message in enumerate(normalized):
            if message.role == "system":
                selected_indexes.append(index)
                break
        for index, message in enumerate(normalized):
            if message.role == "user":
                selected_indexes.append(index)
                break

        used = sum(_message_size(normalized[index]) for index in selected_indexes)
        selected_set = set(selected_indexes)
        for unit in reversed(units):
            if any(index in selected_set for index in unit):
                continue
            size = sum(_message_size(normalized[index]) for index in unit)
            if used + size > self.max_chars:
                continue
            selected_indexes.extend(unit)
            used += size
        selected_indexes.sort()
        selected = [normalized[index] for index in selected_indexes]
        omitted = len(normalized) - len(selected_indexes)
        if omitted:
            marker = Message(role="system", content=f"[{omitted} older context messages omitted to stay within the {self.max_chars}-character budget]")
            insertion = 1 if selected and selected[0].role == "system" else 0
            selected.insert(insertion, marker)
        # Drop optional context first, then shrink retained messages until the
        # serialized estimate is within the configured budget.
        while len(selected) > 1 and sum(_message_size(message) for message in selected) > self.max_chars:
            removable_unit = next((unit for unit in self._message_units(selected)
                                   if all(selected[index].role not in {"system", "user"} for index in unit)), None)
            if removable_unit is None:
                break
            for index in reversed(removable_unit):
                selected.pop(index)
        if sum(_message_size(message) for message in selected) > self.max_chars:
            while selected and sum(_message_size(message) for message in selected) > self.max_chars:
                candidates = [unit for unit in self._message_units(selected)
                              if all(selected[index].role not in {"system", "user"} for index in unit)]
                if candidates:
                    for index in reversed(max(candidates, key=lambda unit: sum(_message_size(selected[i]) for i in unit))):
                        selected.pop(index)
                    continue
                largest = max(range(len(selected)), key=lambda index: _message_size(selected[index]))
                current = selected[largest]
                current_size = _message_size(current)
                allowance = self.max_chars - (sum(_message_size(item) for item in selected) - current_size)
                candidate = self._fit_message(current, allowance)
                if _message_size(candidate) >= current_size:
                    selected.pop(largest)
                else:
                    selected[largest] = candidate
        return [selected[index] for unit in self._message_units(selected) for index in unit]

    @staticmethod
    def _message_units(messages: Sequence[Message]) -> list[tuple[int, ...]]:
        """Group assistant tool calls with all matching tool results.

        OpenAI-compatible providers require these messages to stay together;
        dropping only one side produces HTTP 400 errors on the next request.
        Orphan or incomplete tool exchanges are excluded entirely.
        """
        units: list[tuple[int, ...]] = []
        index = 0
        while index < len(messages):
            message = messages[index]
            if message.role == "tool":
                index += 1
                continue
            if message.role == "assistant" and message.tool_calls:
                ids = [call.id for call in message.tool_calls]
                end = index + 1
                found: list[int] = []
                while end < len(messages) and messages[end].role == "tool":
                    found.append(end)
                    end += 1
                if len(found) == len(ids) and [messages[i].tool_call_id for i in found] == ids:
                    units.append(tuple([index, *found]))
                index = end
                continue
            units.append((index,))
            index += 1
        return units

    def _fit_message(self, message: Message, budget: int) -> Message:
        """Return the largest useful content truncation whose estimate fits."""
        if budget <= 0:
            return Message(role=message.role, tool_call_id=message.tool_call_id)
        low, high = 0, max(1, self.max_message_chars)
        best = Message(role=message.role, tool_call_id=message.tool_call_id)
        empty_calls = Message(role=message.role, tool_call_id=message.tool_call_id, tool_calls=message.tool_calls)
        if _message_size(empty_calls) <= budget:
            best = empty_calls
        elif _message_size(best) > budget:
            return best
        while low <= high:
            middle = (low + high) // 2
            candidate = self._truncate(message, middle)
            if _message_size(candidate) <= budget:
                best = candidate
                low = middle + 1
            else:
                high = middle - 1
        return best

    def _truncate(self, message: Message, limit: int | None = None) -> Message:
        limit = self.max_message_chars if limit is None else max(0, limit)
        content = message.content
        if len(content) > limit:
            content = content[:limit] + "\n[message truncated]"
        calls = tuple(
            type(call)(call.id, call.name, _bounded_value(call.arguments, max(256, limit // 2)))
            for call in message.tool_calls[:_MAX_CONTEXT_TOOL_CALLS]
        )
        return Message(role=message.role, content=content, tool_call_id=message.tool_call_id, tool_calls=calls)
