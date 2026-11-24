from unittest.mock import patch
from tests.conftest import create_mock_completion_response

def test_key_1_failing_and_key_2_succeeding(client):
    """Test 12: Key 1 fails and key 2 succeeds, returning key_id=key_2 and fallback_attempts=1."""
    mock_resp = create_mock_completion_response("Success via fallback key.", 30, 6)

    with patch("app.llm_service.litellm.completion", return_value=mock_resp):
        # Simulate key_1 failure via header
        response = client.post(
            "/chat",
            json={"session_id": "s-failover-1", "message": "Test failover"},
            headers={"X-Simulate-Failure": "key_1=429"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["reply"] == "Success via fallback key."
        assert data["metadata"]["key_id"] == "key_2"
        assert data["metadata"]["fallback_attempts"] == 1
        assert data["metadata"]["resolution_source"] == "LIVE_INFERENCE"

def test_all_keys_failing(client):
    """Test 13: When all keys fail, return HTTP 503 {'detail': 'all providers unavailable'}."""
    response = client.post(
        "/chat",
        json={"session_id": "s-all-fail", "message": "Test all fail"},
        headers={"X-Simulate-Failure": "*=429"}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}

def test_failed_requests_not_stored_in_history(client):
    """Test 14: Failed requests are never stored in session message history."""
    session_id = "s-failed-not-stored"
    response = client.post(
        "/chat",
        json={"session_id": session_id, "message": "This request should fail"},
        headers={"X-Simulate-Failure": "*=500"}
    )
    assert response.status_code == 503

    # Check history endpoint: should return 404 because session has 0 messages stored
    res = client.get(f"/sessions/{session_id}/messages")
    assert res.status_code == 404
    assert res.json() == {"detail": "session not found"}

def test_failure_simulation_is_request_specific(client):
    """Test 15: Failure simulation affects only the specific request and does not disable key permanently."""
    mock_resp = create_mock_completion_response("Normal response from key_1.", 20, 5)

    with patch("app.llm_service.litellm.completion", return_value=mock_resp):
        # Request 1 with simulation header -> triggers failover to key_2
        r1 = client.post(
            "/chat",
            json={"session_id": "s-sim-iso-1", "message": "Request 1"},
            headers={"X-Simulate-Failure": "key_1=429"}
        )
        assert r1.status_code == 200
        assert r1.json()["metadata"]["key_id"] == "key_2"
        assert r1.json()["metadata"]["fallback_attempts"] == 1

        # Request 2 without simulation header -> succeeds normally on key_1
        r2 = client.post(
            "/chat",
            json={"session_id": "s-sim-iso-2", "message": "Request 2"}
        )
        assert r2.status_code == 200
        assert r2.json()["metadata"]["key_id"] == "key_1"
        assert r2.json()["metadata"]["fallback_attempts"] == 0
