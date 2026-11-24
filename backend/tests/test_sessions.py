from unittest.mock import patch
from tests.conftest import create_mock_completion_response

def test_multi_turn_memory(client):
    """Test 5: Multi-turn memory includes previous conversation history in subsequent LLM calls."""
    resp1 = create_mock_completion_response("My name is Lyftr Assistant.")
    resp2 = create_mock_completion_response("You previously asked for my name.")

    calls = []
    def mock_completion(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return resp1
        return resp2

    with patch("app.llm_service.litellm.completion", side_effect=mock_completion):
        session_id = "session-memory-1"

        # Turn 1
        r1 = client.post("/chat", json={"session_id": session_id, "message": "What is your name?"})
        assert r1.status_code == 200

        # Turn 2
        r2 = client.post("/chat", json={"session_id": session_id, "message": "What did I ask?"})
        assert r2.status_code == 200

        # Verify second call included turn 1 in messages
        assert len(calls) == 2
        second_call_messages = calls[1]["messages"]
        roles_and_contents = [(m["role"], m["content"]) for m in second_call_messages]

        assert ("user", "What is your name?") in roles_and_contents
        assert ("assistant", "My name is Lyftr Assistant.") in roles_and_contents
        assert ("user", "What did I ask?") in roles_and_contents

def test_session_isolation(client):
    """Test 6: Messages from one session do not leak into another session."""
    resp = create_mock_completion_response("Isolated reply.")
    calls = []

    def mock_completion(*args, **kwargs):
        calls.append(kwargs)
        return resp

    with patch("app.llm_service.litellm.completion", side_effect=mock_completion):
        # Session A
        client.post("/chat", json={"session_id": "session-A", "message": "Secret message from A"})

        # Session B
        client.post("/chat", json={"session_id": "session-B", "message": "Hello from B"})

        assert len(calls) == 2
        session_b_messages = calls[1]["messages"]
        session_b_texts = [m["content"] for m in session_b_messages]

        assert "Secret message from A" not in session_b_texts
        assert "Hello from B" in session_b_texts

def test_history_retrieval(client):
    """Test 7: Retrieve messages in chronological order with metadata."""
    mock_resp = create_mock_completion_response("The capital is Paris.", 15, 8)

    with patch("app.llm_service.litellm.completion", return_value=mock_resp):
        session_id = "session-history-test"
        client.post("/chat", json={"session_id": session_id, "message": "Hello"})

        res = client.get(f"/sessions/{session_id}/messages")
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] == session_id
        messages = data["messages"]
        assert len(messages) == 2

        # User message
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"

        # Assistant message
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "The capital is Paris."
        assert messages[1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert messages[1]["metadata"]["input_tokens"] == 15
        assert messages[1]["metadata"]["output_tokens"] == 8

def test_unknown_session(client):
    """Test 8: Unknown session returns 404 with detail 'session not found'."""
    response = client.get("/sessions/nonexistent-session-id-999/messages")
    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}
