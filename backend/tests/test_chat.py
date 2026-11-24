from unittest.mock import patch
from tests.conftest import create_mock_completion_response

def test_valid_chat_request(client):
    """Test 2: Valid chat request returns reply and all mandatory metadata fields."""
    mock_resp = create_mock_completion_response("Paris is the capital of France.", 35, 8)

    with patch("app.llm_service.litellm.completion", return_value=mock_resp):
        payload = {
            "session_id": "session-valid-1",
            "message": "What is the capital of France?"
        }
        response = client.post("/chat", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["session_id"] == "session-valid-1"
        assert data["reply"] == "Paris is the capital of France."

        meta = data["metadata"]
        assert meta["resolution_source"] == "LIVE_INFERENCE"
        assert meta["model"] == "gemini/gemini-2.0-flash"
        assert meta["key_id"] == "key_1"
        assert meta["fallback_attempts"] == 0
        assert isinstance(meta["latency_ms"], int)
        assert isinstance(meta["input_tokens"], int)
        assert isinstance(meta["output_tokens"], int)
        assert meta["input_tokens"] == 35
        assert meta["output_tokens"] == 8

def test_empty_message(client):
    """Test 3: Empty messages or whitespace-only messages are rejected with 422."""
    # Completely empty string
    res1 = client.post("/chat", json={"session_id": "s-1", "message": ""})
    assert res1.status_code == 422
    assert "detail" in res1.json()

    # Whitespace only string
    res2 = client.post("/chat", json={"session_id": "s-1", "message": "   \n\t  "})
    assert res2.status_code == 422
    assert "detail" in res2.json()

def test_message_over_4000_chars(client):
    """Test 4: Messages exceeding 4000 characters are rejected with 422."""
    long_message = "A" * 4001
    response = client.post("/chat", json={"session_id": "s-1", "message": long_message})
    assert response.status_code == 422
    assert "detail" in response.json()

def test_token_usage_integers(client):
    """Test 11: Return token usage as integers for each individual request."""
    mock_resp = create_mock_completion_response("Response with token tracking.", 42, 9)

    with patch("app.llm_service.litellm.completion", return_value=mock_resp):
        response = client.post("/chat", json={"session_id": "s-token", "message": "Count my tokens"})
        assert response.status_code == 200
        data = response.json()
        meta = data["metadata"]
        assert isinstance(meta["input_tokens"], int)
        assert isinstance(meta["output_tokens"], int)
        assert meta["input_tokens"] == 42
        assert meta["output_tokens"] == 9
