import time

from forgecode.embed import EmbeddedSession, ForgeCodeError
from forgecode.security.trust import TrustStore


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
        # The old reader must not publish its exit into the new generation's
        # queue after reconnecting.
        time.sleep(0.1)
        trailing = [session.poll(0) for _ in range(8)]
        assert not any(item and item.get("kind") == "process_exit" for item in trailing)
    finally:
        session.close()


def test_embedded_act_reconnect_requires_current_trust(tmp_path):
    TrustStore(tmp_path).grant()
    session = EmbeddedSession(str(tmp_path), mode="act")
    try:
        session.process.terminate()
        session.process.wait(timeout=3)
        TrustStore(tmp_path).revoke()
        try:
            session.reconnect()
            raise AssertionError("expected trust failure")
        except ForgeCodeError as exc:
            assert exc.code == "trust_required"
    finally:
        session.close()


def test_embedded_send_after_process_exit_is_typed(tmp_path):
    session = EmbeddedSession(str(tmp_path))
    try:
        session.process.terminate()
        session.process.wait(timeout=3)
        try:
            session.send("hello")
        except ForgeCodeError as exc:
            assert exc.code == "process_error"
        else:
            raise AssertionError("expected process_error")
    finally:
        session.close()


def test_embedded_session_bounds_event_queue(tmp_path):
    for value in (0, 100_001):
        try:
            EmbeddedSession(str(tmp_path), max_events=value)
        except ValueError:
            pass
        else:
            raise AssertionError("expected max_events validation")
