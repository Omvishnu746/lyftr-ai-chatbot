import logging
import time
from unittest.mock import patch
import httpx
import litellm

from app.config import settings
from tests.conftest import create_mock_completion_response


def _make_httpx_response(status_code: int, headers: dict | None = None) -> httpx.Response:
    req = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/test")
    return httpx.Response(status_code=status_code, headers=headers or {}, request=req)


def test_key1_succeeds(client):
    """1. Key 1 succeeds on the first attempt with zero fallbacks."""
    mock_resp = create_mock_completion_response("Hello from key 1.", 20, 5)
    seen_keys = []

    def fake_completion(*args, **kwargs):
        seen_keys.append(kwargs.get("api_key"))
        return mock_resp

    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        resp = client.post("/chat", json={"session_id": "s-res-1", "message": "Hi"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == "Hello from key 1."
        assert data["metadata"]["key_id"] == "key_1"
        assert data["metadata"]["fallback_attempts"] == 0
        assert seen_keys == ["test_key_1"]


def test_key1_503_retry_succeeds(client, monkeypatch):
    """2. Key 1 returns 503 once, transient retry on key_1 succeeds without failing over."""
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 2)

    mock_resp = create_mock_completion_response("Recovered on key 1.", 25, 6)
    seen_keys = []

    def fake_completion(*args, **kwargs):
        seen_keys.append(kwargs.get("api_key"))
        if len(seen_keys) == 1:
            raise litellm.ServiceUnavailableError(
                message="Service Unavailable",
                llm_provider="gemini",
                model="gemini/gemini-3.5-flash-lite",
                response=_make_httpx_response(503),
            )
        return mock_resp

    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        resp = client.post("/chat", json={"session_id": "s-res-2", "message": "Retry me"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == "Recovered on key 1."
        assert data["metadata"]["key_id"] == "key_1"
        assert data["metadata"]["fallback_attempts"] == 0
        assert seen_keys == ["test_key_1", "test_key_1"]


def test_key1_repeated_503_failover_key2_succeeds(client, monkeypatch):
    """3. Key 1 repeatedly returns 503, exhausts retries, and key 2 succeeds."""
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 2)

    mock_resp = create_mock_completion_response("Served by key 2.", 30, 7)
    seen_keys = []

    def fake_completion(*args, **kwargs):
        api_key = kwargs.get("api_key")
        seen_keys.append(api_key)
        if api_key == "test_key_1":
            raise litellm.ServiceUnavailableError(
                message="503 overloaded",
                llm_provider="gemini",
                model="gemini/gemini-3.5-flash-lite",
                response=_make_httpx_response(503),
            )
        return mock_resp

    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        resp = client.post("/chat", json={"session_id": "s-res-3", "message": "Failover please"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == "Served by key 2."
        assert data["metadata"]["key_id"] == "key_2"
        assert data["metadata"]["fallback_attempts"] == 1
        # 3 attempts on key_1 (1 initial + 2 retries), then 1 success on key_2
        assert seen_keys == ["test_key_1", "test_key_1", "test_key_1", "test_key_2"]


def test_key1_429_respects_retry_after_and_failover(client, monkeypatch):
    """4a. Key 1 returns 429 with short Retry-After header, waits, then succeeds."""
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_DELAY_SECONDS", 1.0)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 1)

    mock_resp = create_mock_completion_response("OK after Retry-After.", 18, 4)
    attempts = {"count": 0}

    def fake_completion(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise litellm.RateLimitError(
                message="Rate limit hit",
                llm_provider="gemini",
                model="gemini/gemini-3.5-flash-lite",
                response=_make_httpx_response(429, headers={"Retry-After": "0.15"}),
                headers={"Retry-After": "0.15"},
            )
        return mock_resp

    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        start = time.perf_counter()
        resp = client.post("/chat", json={"session_id": "s-res-4a", "message": "Rate limit retry"})
        elapsed = time.perf_counter() - start
        assert resp.status_code == 200
        assert resp.json()["metadata"]["key_id"] == "key_1"
        assert elapsed >= 0.14


def test_key1_429_large_retry_after_fails_over_immediately(client, monkeypatch):
    """4b. Key 1 returns 429 with large Retry-After (> budget) -> skips sleeping and fails over to key_2."""
    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(settings, "LLM_TOTAL_DEADLINE_SECONDS", 4.0)
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 2)

    mock_resp = create_mock_completion_response("Failover on large Retry-After.", 22, 5)
    seen_keys = []

    def fake_completion(*args, **kwargs):
        api_key = kwargs.get("api_key")
        seen_keys.append(api_key)
        if api_key == "test_key_1":
            raise litellm.RateLimitError(
                message="Quota exceeded",
                llm_provider="gemini",
                model="gemini/gemini-3.5-flash-lite",
                response=_make_httpx_response(429, headers={"Retry-After": "60"}),
                headers={"Retry-After": "60"},
            )
        return mock_resp

    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        start = time.perf_counter()
        resp = client.post("/chat", json={"session_id": "s-res-4b", "message": "429 failover"})
        elapsed = time.perf_counter() - start
        assert resp.status_code == 200
        assert resp.json()["metadata"]["key_id"] == "key_2"
        assert resp.json()["metadata"]["fallback_attempts"] == 1
        assert seen_keys == ["test_key_1", "test_key_2"]
        assert elapsed < 1.0


def test_both_keys_503_returns_meaningful_error_and_redacts_secrets(client, monkeypatch, caplog):
    """5. Both keys return 503 -> returns informative 503 without exposing secrets or saving DB rows."""
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(settings, "LLM_RETRY_MAX_DELAY_SECONDS", 0.02)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 1)

    def fake_completion(*args, **kwargs):
        api_key = kwargs.get("api_key")
        raise litellm.ServiceUnavailableError(
            message=f"503 backend overloaded for key={api_key}",
            llm_provider="gemini",
            model="gemini/gemini-3.5-flash-lite",
            response=_make_httpx_response(503),
        )

    with caplog.at_level(logging.WARNING):
        with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
            resp = client.post("/chat", json={"session_id": "s-res-5", "message": "Both fail"})

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "ServiceUnavailableError" in detail
    assert "503" in detail
    assert "test_key_1" not in detail
    assert "test_key_2" not in detail

    # Verify logs contain key identifiers and exception metadata, never raw keys
    log_text = caplog.text
    assert "key_1" in log_text
    assert "key_2" in log_text
    assert "ServiceUnavailableError" in log_text
    assert "failover_succeeded=False" in log_text
    assert "test_key_1" not in log_text
    assert "test_key_2" not in log_text

    # Failed session should have no persisted messages
    hist = client.get("/sessions/s-res-5/messages")
    assert hist.status_code == 404


def test_key1_real_timeout_failover_key2_succeeds(client, monkeypatch):
    """
    6. Genuinely verifies timeout enforcement when key_1 ignores the timeout parameter
    and blocks in Python sleep: application timeout interrupts waiting and fails over to key_2.
    """
    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 0.15)
    monkeypatch.setattr(settings, "LLM_TOTAL_DEADLINE_SECONDS", 1.2)
    monkeypatch.setattr(settings, "LLM_RETRY_BASE_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 1)

    mock_resp = create_mock_completion_response("Key 2 saved timed-out request.", 16, 6)
    seen_keys = []
    import threading
    from app.llm_service import get_active_worker_count
    release_hung = threading.Event()

    def blocking_on_key1(*args, **kwargs):
        api_key = kwargs.get("api_key")
        seen_keys.append(api_key)
        if api_key == "test_key_1":
            # Intentionally ignore kwargs["timeout"] to prove genuine wall-clock interruption
            release_hung.wait(timeout=2.5)
            return create_mock_completion_response("Too late", 1, 1)
        return mock_resp

    try:
        with patch("app.llm_service.litellm.completion", side_effect=blocking_on_key1):
            start = time.perf_counter()
            resp = client.post("/chat", json={"session_id": "s-res-6", "message": "Timeout test"})
            elapsed = time.perf_counter() - start

        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"] == "Key 2 saved timed-out request."
        assert data["metadata"]["key_id"] == "key_2"
        assert data["metadata"]["fallback_attempts"] == 1
        assert "test_key_1" in seen_keys and "test_key_2" in seen_keys
        # Must finish well below the 2.5s sleep on key_1
        assert elapsed < 1.3
    finally:
        release_hung.set()
        deadline = time.monotonic() + 1.0
        while get_active_worker_count() > 0 and time.monotonic() < deadline:
            time.sleep(0.01)


def test_invalid_api_key_does_not_retry_and_fails_over(client, monkeypatch):
    """7. Invalid API key (AuthenticationError) performs 0 retries on key_1 and tries key_2."""
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 3)

    seen_keys = []
    mock_resp = create_mock_completion_response("Key 2 valid.", 12, 3)

    def fake_completion(*args, **kwargs):
        api_key = kwargs.get("api_key")
        seen_keys.append(api_key)
        if api_key == "test_key_1":
            raise litellm.AuthenticationError(
                message="API key not valid",
                llm_provider="gemini",
                model="gemini/gemini-3.5-flash-lite",
                response=_make_httpx_response(401),
            )
        return mock_resp

    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        resp = client.post("/chat", json={"session_id": "s-res-7a", "message": "Auth test"})
        assert resp.status_code == 200
        assert resp.json()["metadata"]["key_id"] == "key_2"
        assert resp.json()["metadata"]["fallback_attempts"] == 1
        # Only 1 attempt on key_1 (no retries!) before trying key_2
        assert seen_keys == ["test_key_1", "test_key_2"]


def test_both_keys_invalid_no_retries(client, monkeypatch):
    """7b. When both keys fail auth, exactly 1 attempt per key runs (no retry loop)."""
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 3)
    seen_keys = []

    def fake_completion(*args, **kwargs):
        seen_keys.append(kwargs.get("api_key"))
        raise litellm.AuthenticationError(
            message="Invalid API key",
            llm_provider="gemini",
            model="gemini/gemini-3.5-flash-lite",
            response=_make_httpx_response(401),
        )

    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        resp = client.post("/chat", json={"session_id": "s-res-7b", "message": "Bad keys"})
        assert resp.status_code == 503
        assert "authentication failed" in resp.json()["detail"].lower()
        assert "401" in resp.json()["detail"]
        assert seen_keys == ["test_key_1", "test_key_2"]


def test_invalid_model_produces_meaningful_error_without_retries(client, monkeypatch):
    """8. Invalid model (NotFoundError/404) aborts immediately with 0 retries and clear message."""
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES_PER_KEY", 3)
    call_count = {"n": 0}

    def fake_completion(*args, **kwargs):
        call_count["n"] += 1
        raise litellm.NotFoundError(
            message="models/bad-model is not found",
            model="gemini/bad-model",
            llm_provider="gemini",
            response=_make_httpx_response(404),
        )

    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        resp = client.post("/chat", json={"session_id": "s-res-8", "message": "Bad model"})
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "not found" in detail.lower() or "404" in detail
        # Aborted after single call — neither retried nor failed over to key_2
        assert call_count["n"] == 1


def test_long_conversation_history_sliding_window_and_sqlite_preserved(client, monkeypatch):
    """
    9. Conversation history is limited to the last 10 turns (20 history messages),
    system prompt is preserved first, pairing & chronological order hold,
    and SQLite keeps all historical turns.
    """
    monkeypatch.setattr(settings, "LLM_MAX_HISTORY_TURNS", 10)
    monkeypatch.setattr(settings, "LLM_MAX_HISTORY_CHARS", 50000)

    captured_calls = []

    def fake_completion(*args, **kwargs):
        captured_calls.append(kwargs["messages"])
        turn_num = len(captured_calls)
        return create_mock_completion_response(f"Reply {turn_num}", 10, 5)

    session_id = "s-res-9-long"
    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        for i in range(1, 15):  # 14 turns total
            resp = client.post(
                "/chat",
                json={
                    "session_id": session_id,
                    "message": f"User turn {i}",
                    "system_prompt": "Be concise.",
                },
            )
            assert resp.status_code == 200

    # Check the 14th call sent to LLM:
    # 1 system + last 10 turns (20 msgs: turns 4..13) + current user turn 14 = 22 msgs
    last_messages = captured_calls[-1]
    assert len(last_messages) == 22
    assert last_messages[0] == {"role": "system", "content": "Be concise."}
    assert last_messages[1] == {"role": "user", "content": "User turn 4"}
    assert last_messages[2] == {"role": "assistant", "content": "Reply 4"}
    assert last_messages[-2] == {"role": "assistant", "content": "Reply 13"}
    assert last_messages[-1] == {"role": "user", "content": "User turn 14"}

    # Verify strict alternating user/assistant pairing after system prompt
    convo = last_messages[1:]
    for idx, msg in enumerate(convo):
        expected_role = "user" if idx % 2 == 0 else "assistant"
        assert msg["role"] == expected_role

    # Verify SQLite still holds ALL 14 turns (28 messages)
    hist_resp = client.get(f"/sessions/{session_id}/messages")
    assert hist_resp.status_code == 200
    assert len(hist_resp.json()["messages"]) == 28


def test_history_character_limit_protection(client, monkeypatch):
    """9b. Extremely long history is bounded by LLM_MAX_HISTORY_CHARS while preserving pairing."""
    monkeypatch.setattr(settings, "LLM_MAX_HISTORY_TURNS", 10)
    monkeypatch.setattr(settings, "LLM_MAX_HISTORY_CHARS", 1000)

    captured_calls = []

    def fake_completion(*args, **kwargs):
        captured_calls.append(kwargs["messages"])
        idx = len(captured_calls)
        return create_mock_completion_response("A" * 400 + f"-{idx}", 50, 50)

    session_id = "s-res-9-chars"
    with patch("app.llm_service.litellm.completion", side_effect=fake_completion):
        for i in range(1, 6):
            r = client.post(
                "/chat",
                json={"session_id": session_id, "message": ("Q" * 300) + f"-{i}"},
            )
            assert r.status_code == 200

    last_messages = captured_calls[-1]
    history_only = last_messages[:-1]
    total_history_chars = sum(len(m["content"]) for m in history_only)
    assert total_history_chars <= 1000
    # Must have dropped older turns in complete pairs
    assert len(history_only) % 2 == 0
    assert last_messages[-1]["content"].endswith("-5")


def test_cached_responses_still_work(client):
    """10. LiteLLM response caching still returns LLM_CACHE across sessions."""
    mock_resp = create_mock_completion_response("Cached answer.", 14, 4)
    with patch("app.llm_service.litellm.completion", return_value=mock_resp) as mock_comp:
        r1 = client.post("/chat", json={"session_id": "s-res-10a", "message": "Cache check"})
        r2 = client.post("/chat", json={"session_id": "s-res-10b", "message": "Cache check"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert r2.json()["metadata"]["resolution_source"] == "LLM_CACHE"
        assert mock_comp.call_count == 1


def test_failure_simulation_still_works(client):
    """11. X-Simulate-Failure header continues to simulate key_1 failover and *=503."""
    mock_resp = create_mock_completion_response("Simulated key 2 reply.", 15, 5)
    with patch("app.llm_service.litellm.completion", return_value=mock_resp):
        r_ok = client.post(
            "/chat",
            json={"session_id": "s-res-11a", "message": "Sim failover"},
            headers={"X-Simulate-Failure": "key_1=503"},
        )
        assert r_ok.status_code == 200
        assert r_ok.json()["metadata"]["key_id"] == "key_2"
        assert r_ok.json()["metadata"]["fallback_attempts"] == 1

    r_fail = client.post(
        "/chat",
        json={"session_id": "s-res-11b", "message": "Sim all fail"},
        headers={"X-Simulate-Failure": "*=503"},
    )
    assert r_fail.status_code == 503
    assert r_fail.json() == {"detail": "all providers unavailable"}
