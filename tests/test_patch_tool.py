from pathlib import Path

from forgecode.security import WorkspaceGuard
from forgecode.tools import AllowAllApproval, DenyAllApproval, ToolContext, build_default_registry


def _registry(tmp_path: Path):
    guard = WorkspaceGuard(tmp_path)
    return build_default_registry(guard), ToolContext(guard, AllowAllApproval())


def test_apply_patch_updates_multiple_hunks_and_reports_diff(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n\ndef label():\n    return 'old'\n", encoding="utf-8")
    registry, context = _registry(tmp_path)
    patch = """--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
@@ -4,2 +4,2 @@
 def label():
-    return 'old'
+    return 'new'
"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert result.ok
    assert result.metadata["hunks"] == 2
    assert "@@" in result.metadata["diff"]
    assert "return a + b" in (tmp_path / "calc.py").read_text(encoding="utf-8")


def test_apply_patch_applies_separate_hunks_after_line_offset(tmp_path):
    target = tmp_path / "offset.txt"
    target.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    registry, context = _registry(tmp_path)
    patch = """--- a/offset.txt
+++ b/offset.txt
@@ -1,2 +1,3 @@
 one
+inserted
 two
@@ -3,2 +4,2 @@
 three
-four
+FOUR
"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert result.ok
    assert target.read_text(encoding="utf-8") == "one\ninserted\ntwo\nthree\nFOUR\n"
    assert result.metadata["hunks"] == 2


def test_apply_patch_supports_explicit_zero_count_insertion_hunk(tmp_path):
    target = tmp_path / "insert.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")
    registry, context = _registry(tmp_path)
    patch = """--- a/insert.txt
+++ b/insert.txt
@@ -1,0 +2,1 @@
+between
"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert result.ok
    assert target.read_text(encoding="utf-8") == "one\nbetween\ntwo\n"


def test_apply_patch_preserves_no_trailing_newline_and_crlf(tmp_path):
    no_newline = tmp_path / "no-newline.txt"
    no_newline.write_bytes(b"one\ntwo")
    crlf = tmp_path / "crlf.txt"
    crlf.write_bytes(b"one\r\ntwo\r\n")
    registry, context = _registry(tmp_path)
    patch = """--- a/no-newline.txt
+++ b/no-newline.txt
@@ -1,2 +1,2 @@
 one
-two
+TWO
"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert result.ok
    assert no_newline.read_bytes() == b"one\nTWO"
    patch = """--- a/crlf.txt
+++ b/crlf.txt
@@ -1,2 +1,2 @@
 one
-two
+TWO
"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert result.ok
    assert crlf.read_bytes() == b"one\r\nTWO\r\n"


def test_apply_patch_accepts_git_diff_preamble_and_header_like_removed_lines(tmp_path):
    target = tmp_path / "header.txt"
    target.write_text("--- old heading\nvalue\n", encoding="utf-8")
    registry, context = _registry(tmp_path)
    patch = """diff --git a/header.txt b/header.txt
index 1111111..2222222 100644
--- a/header.txt
+++ b/header.txt
@@ -1,2 +1,2 @@
 --- old heading
-value
+new value
"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert result.ok
    assert target.read_text(encoding="utf-8") == "--- old heading\nnew value\n"


def test_apply_patch_accepts_no_newline_marker(tmp_path):
    target = tmp_path / "marker.txt"
    target.write_bytes(b"one\ntwo")
    registry, context = _registry(tmp_path)
    patch = """--- a/marker.txt
+++ b/marker.txt
@@ -1,2 +1,2 @@
 one
-two
\\ No newline at end of file
+TWO
\\ No newline at end of file
"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert result.ok
    assert target.read_bytes() == b"one\nTWO"


def test_apply_patch_rolls_back_all_files_when_a_later_atomic_write_fails(tmp_path, monkeypatch):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    registry, context = _registry(tmp_path)
    patch = """--- a/first.txt
+++ b/first.txt
@@ -1,1 +1,1 @@
-one
+ONE
--- a/second.txt
+++ b/second.txt
@@ -1,1 +1,1 @@
-two
+TWO
"""
    import forgecode.tools.patch as patch_module

    original_atomic_write = patch_module._atomic_write

    failed = False

    def fail_second(path, content):
        nonlocal failed
        if not failed and path.name == "second.txt":
            failed = True
            raise OSError("injected write failure")
        return original_atomic_write(path, content)

    monkeypatch.setattr(patch_module, "_atomic_write", fail_second)
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert not result.ok
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "two\n"


def test_apply_patch_custom_format_creates_file(tmp_path):
    registry, context = _registry(tmp_path)
    patch = """*** Begin Patch
*** Add File: created.txt
@@
+created content
*** End Patch"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert result.ok
    assert result.metadata["created"] == ["created.txt"]
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "created content\n"


def test_apply_patch_delete_requires_explicit_flag(tmp_path):
    target = tmp_path / "remove.txt"
    target.write_text("remove me\n", encoding="utf-8")
    registry, context = _registry(tmp_path)
    patch = """--- a/remove.txt
+++ /dev/null
@@ -1,1 +0,0 @@
-remove me
"""
    denied = registry.execute("apply_patch", {"patch": patch}, context)
    assert not denied.ok
    assert denied.metadata["error"] == "delete_requires_explicit_flag"
    assert target.exists()
    allowed = registry.execute("apply_patch", {"patch": patch, "allow_delete": True}, context)
    assert allowed.ok
    assert not target.exists()


def test_apply_patch_context_mismatch_keeps_original(tmp_path):
    target = tmp_path / "a.txt"
    original = "one\ntwo\n"
    target.write_text(original, encoding="utf-8")
    registry, context = _registry(tmp_path)
    patch = """--- a/a.txt
+++ b/a.txt
@@ -1,2 +1,2 @@
 wrong
-two
+three
"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert not result.ok
    assert target.read_text(encoding="utf-8") == original
    assert result.metadata["error"] == "patch_invalid"


def test_apply_patch_denied_and_path_escape_do_not_mutate(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("one\n", encoding="utf-8")
    guard = WorkspaceGuard(tmp_path)
    registry = build_default_registry(guard)
    patch = """--- a/a.txt
+++ b/a.txt
@@ -1,1 +1,1 @@
-one
+two
"""
    denied = registry.execute("apply_patch", {"patch": patch}, ToolContext(guard, DenyAllApproval()))
    assert not denied.ok
    assert target.read_text(encoding="utf-8") == "one\n"
    escaped = """--- a/../outside.txt
+++ b/../outside.txt
@@ -0,0 +1,1 @@
+bad
"""
    rejected = registry.execute("apply_patch", {"patch": escaped}, ToolContext(guard, AllowAllApproval()))
    assert not rejected.ok
    assert not (tmp_path.parent / "outside.txt").exists()


def test_apply_patch_rejects_empty_duplicate_and_binary(tmp_path):
    registry, context = _registry(tmp_path)
    empty = registry.execute("apply_patch", {"patch": "  \n"}, context)
    assert not empty.ok
    (tmp_path / "binary.bin").write_bytes(b"\xff\xfe")
    binary_patch = """--- a/binary.bin
+++ b/binary.bin
@@ -1,1 +1,1 @@
-old
+new
"""
    binary = registry.execute("apply_patch", {"patch": binary_patch}, context)
    assert not binary.ok
    duplicate = registry.execute("apply_patch", {"patch": binary_patch.replace("binary.bin", "a.txt") + "\n--- a/a.txt\n+++ b/a.txt\n@@ -0,0 +1,1 @@\n+x"}, context)
    assert not duplicate.ok


def test_apply_patch_rejects_duplicate_targets_after_path_normalization(tmp_path):
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    registry, context = _registry(tmp_path)
    patch = """--- a/a.txt
+++ b/a.txt
@@ -1,1 +1,1 @@
-one
+two
--- a/./a.txt
+++ b/./a.txt
@@ -1,1 +1,1 @@
-one
+three
"""
    result = registry.execute("apply_patch", {"patch": patch}, context)
    assert not result.ok
    assert result.metadata["error"] == "patch_invalid"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "one\n"
