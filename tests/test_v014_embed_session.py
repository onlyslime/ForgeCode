import time

from forgecode.embed import EmbeddedSession


def test_embedded_session_controls_production_chat(tmp_path):
    session = EmbeddedSession(str(tmp_path))
    try:
        assert session.is_alive
        assert session.returncode is None
        event = None
        deadline = time.time() + 5
        while time.time() < deadline and event is None:
            event = session.poll(0.1)
        assert event and event.get("kind") == "interactive_header"
        session.send("/status")
        assert session.poll(2) is not None
    finally:
        session.close()
    assert not session.is_alive
    assert session.returncode is not None


def test_embedded_session_reconnects_dead_worker(tmp_path):
    session = EmbeddedSession(str(tmp_path))
    try:
        session.process.terminate()
        session.process.wait(timeout=3)
        assert session.reconnect() is True
        event = session.poll(2)
        assert event and event.get("kind") == "process_reconnected"
    finally:
        session.close()
