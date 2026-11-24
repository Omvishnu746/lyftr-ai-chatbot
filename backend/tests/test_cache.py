from unittest.mock import patch
from tests.conftest import create_mock_completion_response

def test_cache_hit_across_sessions(client):
    """Test 9: The same first message in two different sessions returns LLM_CACHE on second request."""
    mock_resp = create_mock_completion_response("Berlin is the capital of Germany.", 28, 7)

    with patch("app.llm_service.litellm.completion", return_value=mock_resp) as mock_comp:
        # First request in Session 1
        r1 = client.post("/chat", json={
            "session_id": "session-cache-1",
            "message": "Capital of Germany?"
        })
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert d1["metadata"]["key_id"] == "key_1"
        assert mock_comp.call_count == 1

        # Second request in Session 2 with the identical first message
        r2 = client.post("/chat", json={
            "session_id": "session-cache-2",
            "message": "Capital of Germany?"
        })
        assert r2.status_code == 200
        d2 = r2.json()

        # Cache hit requirements
        assert d2["metadata"]["resolution_source"] == "LLM_CACHE"
        assert d2["metadata"]["key_id"] is None
        assert d2["metadata"]["fallback_attempts"] == 0
        # No extra live completion called
        assert mock_comp.call_count == 1

def test_identical_cached_replies_and_tokens(client):
    """Test 10: Cache hits return identical reply and original token counts."""
    mock_resp = create_mock_completion_response("Quantum computing uses qubits.", 50, 12)

    with patch("app.llm_service.litellm.completion", return_value=mock_resp):
        r1 = client.post("/chat", json={"session_id": "s-cache-a", "message": "Explain quantum"})
        assert r1.status_code == 200
        d1 = r1.json()

        r2 = client.post("/chat", json={"session_id": "s-cache-b", "message": "Explain quantum"})
        assert r2.status_code == 200
        d2 = r2.json()

        assert d2["reply"] == d1["reply"]
        assert d2["metadata"]["input_tokens"] == d1["metadata"]["input_tokens"] == 50
        assert d2["metadata"]["output_tokens"] == d1["metadata"]["output_tokens"] == 12
        assert d2["metadata"]["resolution_source"] == "LLM_CACHE"
