from forgecode.application.interactive_service import InteractiveSession


def test_escape_byte_requests_immediate_cancel():
    calls = []
    session = InteractiveSession(lambda _: None, cancel=lambda: calls.append("cancel") or {"cancelled": True})
    result = session.run_stream(["\x1b"])
    assert calls == ["cancel"]
    assert result == [{"cancelled": True}]


def test_slash_login_is_available():
    session = InteractiveSession(lambda _: None, connect=lambda _args: {"storage": "environment-only"})
    assert session.dispatch("/login")["storage"] == "environment-only"
