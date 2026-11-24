import os
from unittest.mock import patch, PropertyMock
from app.config import Settings

def test_health_success(client):
    """Test 1: Health endpoint returns 200 when healthy and 2 keys configured."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["keys_configured"] == 2
    assert data["cache_enabled"] is True

def test_health_fewer_than_two_keys(client):
    """Test health endpoint fails with 503 when fewer than 2 keys configured."""
    with patch.dict(os.environ, {"LLM_API_KEYS": "single_key"}):
        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
        assert "At least two API keys are required" in data["detail"]

def test_health_db_failure(client):
    """Test health endpoint fails with 503 when database is inaccessible."""
    with patch("app.main.check_db_health", return_value=False):
        response = client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert "detail" in data
        assert "Database is inaccessible" in data["detail"]
