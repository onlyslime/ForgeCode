from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, FileMetadataTool, ListSymbolsTool, ReadRangeTool, ToolContext

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
