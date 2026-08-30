from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, FileMetadataTool, ListSymbolsTool, ReadRangeTool, ToolContext, FindDefinitionTool, FindReferencesTool, SymbolHoverTool

def test_understanding_tools_are_bounded_and_structured(tmp_path):
    target = tmp_path / "sample.py"
    target.write_text("class Demo:\n    def run(self):\n        return 1\n", encoding="utf-8")
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    ranged = ReadRangeTool(context.guard).execute({"path": "sample.py", "start_line": 2, "end_line": 3}, context)
    assert "2 |     def run" in ranged.output
    symbols = ListSymbolsTool(context.guard).execute({"path": "sample.py"}, context)
    assert "Demo" in symbols.output and "run" in symbols.output
    metadata = FileMetadataTool(context.guard).execute({"path": "sample.py"}, context)
    assert metadata.metadata["encoding"] == "utf-8" and len(metadata.metadata["sha256"]) == 64


def test_code_scanners_notice_cancellation_inside_large_files(tmp_path):
    target = tmp_path / "large.py"
    target.write_text(("value = 1\n" * 600) + "def target():\n    return value\n", encoding="utf-8")
    calls = 0

    def cancelled_after_first_poll():
        nonlocal calls
        calls += 1
        return calls >= 2

    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval(), cancellation_requested=cancelled_after_first_poll)
    result = ListSymbolsTool(context.guard).execute({"path": "large.py"}, context)
    assert not result.ok
    assert result.metadata["error"] == "cancelled"


def test_symbol_search_scanners_keep_cancellation_semantics(tmp_path):
    target = tmp_path / "large.py"
    target.write_text(("value = 1\n" * 600) + "def target():\n    return value\n", encoding="utf-8")
    for tool in (FindDefinitionTool(), FindReferencesTool(), SymbolHoverTool()):
        calls = 0

        def cancelled_after_first_poll():
            nonlocal calls
            calls += 1
            return calls >= 2

        context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval(), cancellation_requested=cancelled_after_first_poll)
        result = tool.execute({"symbol": "target"}, context)
        assert not result.ok
        assert result.metadata["error"] == "cancelled"


def test_symbol_search_skips_oversized_source_files(tmp_path):
    target = tmp_path / "huge.py"
    target.write_bytes(b"# padding\n" * 300_000)
    context = ToolContext(WorkspaceGuard(tmp_path), AllowAllApproval())
    result = FindReferencesTool().execute({"symbol": "missing"}, context)
    assert result.ok
    assert result.metadata["count"] == 0
