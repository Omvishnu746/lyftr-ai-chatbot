import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import httpx
import litellm
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base, _configure_sqlite_connection, get_db
from app.llm_service import get_active_worker_count
from app.main import app
from tests.conftest import create_mock_completion_response


def _make_httpx_response(status_code: int) -> httpx.Response:
    req = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/test")
    return httpx.Response(status_code=status_code, request=req)


def _create_file_sqlite_client(tmp_path):
    """Create a TestClient backed by a real WAL-mode SQLite file for true concurrent testing."""
    db_file = tmp_path / "concurrent_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    local_engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False, "timeout": 15.0},
    )
    event.listen(
        local_engine,
        "connect",
        lambda conn, rec: _configure_sqlite_connection(conn, rec, False),
    )
    with local_engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode = WAL"))
    Base.metadata.create_all(bind=local_engine)
    LocalSession = sessionmaker(autocommit=False, autoflush=False, bind=local_engine)

    def override_db():
        db = LocalSession()
        try:
            yield db
        finally:
            db.close()

    return local_engine, override_db


def test_both_keys_timing_out_returns_504_within_deadline(client, monkeypatch):
    """4. Both API keys timing out -> bounded by total deadline and returns HTTP 504."""
    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 0.12)
    monkeypatch.setattr(settings, "LLM_TOTAL_DEADLINE_SECONDS", 0.6)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 2)

    seen_timeouts = []
    seen_keys = []
    release_hung = threading.Event()

    def always_hang(*args, **kwargs):
        seen_keys.append(kwargs.get("api_key"))
        seen_timeouts.append(kwargs.get("timeout"))
        release_hung.wait(timeout=1.5)
        return create_mock_completion_response("unreachable", 1, 1)

    try:
        with patch("app.llm_service.litellm.completion", side_effect=always_hang):
            start = time.perf_counter()
            resp = client.post("/chat", json={"session_id": "s-both-timeout", "message": "Hang both"})
            elapsed = time.perf_counter() - start

        assert resp.status_code == 504
        assert "timed out" in resp.json()["detail"].lower()
        assert seen_keys == ["test_key_1", "test_key_2"]
        assert all(t is not None and t <= 0.6 for t in seen_timeouts)
        assert elapsed < 0.85
    finally:
        release_hung.set()
        deadline = time.monotonic() + 1.0
        while get_active_worker_count() > 0 and time.monotonic() < deadline:
            time.sleep(0.01)


def test_key1_timeout_then_key2_succeeds_repeatedly(client, monkeypatch):
    """1 & 5. Repeated timeout scenarios where key_1 hangs and key_2 succeeds each time."""
    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 0.12)
    monkeypatch.setattr(settings, "LLM_TOTAL_DEADLINE_SECONDS", 1.5)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 1)

    release_hung = threading.Event()

    def hang_key1_ok_key2(*args, **kwargs):
        api_key = kwargs.get("api_key")
        if api_key == "test_key_1":
            release_hung.wait(timeout=0.8)
            return create_mock_completion_response("late", 1, 1)
        return create_mock_completion_response("key2-fast-ok", 10, 4)

    try:
        with patch("app.llm_service.litellm.completion", side_effect=hang_key1_ok_key2):
            for i in range(4):
                resp = client.post(
                    "/chat",
                    json={"session_id": f"s-rep-to-{i}", "message": f"Prompt {i}"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["reply"] == "key2-fast-ok"
                assert data["metadata"]["key_id"] == "key_2"
                assert data["metadata"]["fallback_attempts"] == 1
    finally:
        release_hung.set()
        deadline = time.monotonic() + 1.0
        while get_active_worker_count() > 0 and time.monotonic() < deadline:
            time.sleep(0.01)


def test_bounded_worker_threads_under_repeated_timeouts(client, monkeypatch):
    """6. Repeated hung timeouts never create unbounded worker threads past LLM_MAX_CONCURRENT_CALLS."""
    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 0.08)
    monkeypatch.setattr(settings, "LLM_TOTAL_DEADLINE_SECONDS", 0.4)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 0)
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENT_CALLS", 3)

    release_workers = threading.Event()
    peak_workers = {"val": 0}
    started_calls = {"count": 0}
    lock = threading.Lock()

    def slow_blocked_call(*args, **kwargs):
        with lock:
            started_calls["count"] += 1
            current = get_active_worker_count()
            if current > peak_workers["val"]:
                peak_workers["val"] = current
        # Hold thread open until test releases it to force slot pressure
        release_workers.wait(timeout=2.0)
        return create_mock_completion_response("unblocked", 5, 2)

    try:
        with patch("app.llm_service.litellm.completion", side_effect=slow_blocked_call):
            for i in range(6):
                resp = client.post(
                    "/chat",
                    json={"session_id": f"s-bound-{i}", "message": f"Bound {i}"},
                )
                assert resp.status_code == 504

        assert peak_workers["val"] <= 3
        assert get_active_worker_count() <= 3
        # Requests beyond the 3 occupied slots were rejected without spawning extra threads
        assert started_calls["count"] <= 3
    finally:
        release_workers.set()
        # Allow daemon threads a moment to exit cleanly and reset counter
        deadline = time.monotonic() + 1.0
        while get_active_worker_count() > 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert get_active_worker_count() == 0


def test_retry_deadline_enforced_under_slow_transient_errors(client, monkeypatch):
    """7. Retry delays and attempts strictly respect LLM_TOTAL_DEADLINE_SECONDS."""
    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 0.25)
    monkeypatch.setattr(settings, "LLM_TOTAL_DEADLINE_SECONDS", 0.55)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 10)
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 0.25)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_DELAY_SECONDS", 1.0)

    attempts = {"count": 0}

    def slow_503(*args, **kwargs):
        attempts["count"] += 1
        time.sleep(0.05)
        raise litellm.ServiceUnavailableError(
            message="503 transient",
            llm_provider="gemini",
            model="gemini/gemini-3.5-flash-lite",
            response=_make_httpx_response(503),
        )

    with patch("app.llm_service.litellm.completion", side_effect=slow_503):
        start = time.perf_counter()
        resp = client.post("/chat", json={"session_id": "s-deadline", "message": "Deadline check"})
        elapsed = time.perf_counter() - start

    assert resp.status_code == 503
    # Despite LLM_MAX_RETRIES_PER_KEY=10, deadline capped retries well before 11*2 = 22 calls
    assert attempts["count"] < 10
    assert elapsed < 0.9


def test_simultaneous_requests_different_sessions_and_sqlite_integrity(tmp_path, monkeypatch):
    """2, 3 & 8. Simultaneous requests across sessions keep SQLite & cache safe without corruption."""
    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(settings, "LLM_TOTAL_DEADLINE_SECONDS", 5.0)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 0)
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENT_CALLS", 20)

    local_engine, override_db = _create_file_sqlite_client(tmp_path)
    app.dependency_overrides[get_db] = override_db

    def session_aware_completion(*args, **kwargs):
        messages = kwargs["messages"]
        last_user = messages[-1]["content"]
        time.sleep(0.03)
        if "FAIL_THIS_SESSION" in last_user:
            raise litellm.ServiceUnavailableError(
                message="Simulated 503",
                llm_provider="gemini",
                model="gemini/gemini-3.5-flash-lite",
                response=_make_httpx_response(503),
            )
        return create_mock_completion_response(f"Reply to: {last_user}", 12, 6)

    try:
        with TestClient(app) as concurrent_client:
            with patch("app.llm_service.litellm.completion", side_effect=session_aware_completion):
                tasks = [
                    ("sess-alpha", "alpha-1", 200),
                    ("sess-beta", "beta-1", 200),
                    ("sess-gamma", "FAIL_THIS_SESSION", 503),
                    ("sess-delta", "delta-1", 200),
                    ("sess-alpha", "alpha-2", 200),
                    ("sess-epsilon", "FAIL_THIS_SESSION", 503),
                ]

                def send_one(item):
                    sid, msg, expected_code = item
                    res = concurrent_client.post("/chat", json={"session_id": sid, "message": msg})
                    return sid, msg, expected_code, res

                # Run first 4 concurrently (distinct sessions + one failing session)
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(send_one, tasks[:4]))

                # Run follow-up + another failing session concurrently
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results.extend(list(pool.map(send_one, tasks[4:])))

                for sid, msg, expected_code, res in results:
                    assert res.status_code == expected_code
                    if expected_code == 200:
                        assert res.json()["reply"] == f"Reply to: {msg}"

                # Verify healthy sessions persisted exact chronological messages without cross-leak
                alpha_hist = concurrent_client.get("/sessions/sess-alpha/messages")
                assert alpha_hist.status_code == 200
                alpha_msgs = alpha_hist.json()["messages"]
                assert [m["content"] for m in alpha_msgs] == [
                    "alpha-1",
                    "Reply to: alpha-1",
                    "alpha-2",
                    "Reply to: alpha-2",
                ]

                beta_hist = concurrent_client.get("/sessions/sess-beta/messages")
                assert beta_hist.status_code == 200
                assert len(beta_hist.json()["messages"]) == 2

                # Verify failed sessions wrote zero rows to SQLite
                assert concurrent_client.get("/sessions/sess-gamma/messages").status_code == 404
                assert concurrent_client.get("/sessions/sess-epsilon/messages").status_code == 404
    finally:
        app.dependency_overrides.clear()
        local_engine.dispose()


def test_sqlite_integrity_after_mixed_failure_and_success_on_same_session(client, monkeypatch):
    """8b. Failed turn inside an existing session leaves prior history intact without partial user row."""
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 0)

    session_id = "s-mixed-integrity"
    state = {"fail_next": False}

    def mixed_completion(*args, **kwargs):
        if state["fail_next"]:
            raise litellm.ServiceUnavailableError(
                message="503 outage",
                llm_provider="gemini",
                model="gemini/gemini-3.5-flash-lite",
                response=_make_httpx_response(503),
            )
        user_text = kwargs["messages"][-1]["content"]
        return create_mock_completion_response(f"Answer: {user_text}", 10, 4)

    with patch("app.llm_service.litellm.completion", side_effect=mixed_completion):
        # Turn 1 succeeds
        r1 = client.post("/chat", json={"session_id": session_id, "message": "Turn 1"})
        assert r1.status_code == 200

        # Turn 2 fails
        state["fail_next"] = True
        r2 = client.post("/chat", json={"session_id": session_id, "message": "Turn 2 fails"})
        assert r2.status_code == 503

        # Turn 3 succeeds
        state["fail_next"] = False
        r3 = client.post("/chat", json={"session_id": session_id, "message": "Turn 3"})
        assert r3.status_code == 200

    hist = client.get(f"/sessions/{session_id}/messages")
    assert hist.status_code == 200
    contents = [(m["role"], m["content"]) for m in hist.json()["messages"]]
    assert contents == [
        ("user", "Turn 1"),
        ("assistant", "Answer: Turn 1"),
        ("user", "Turn 3"),
        ("assistant", "Answer: Turn 3"),
    ]
