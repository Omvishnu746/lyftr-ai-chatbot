from typing import List, Optional, Literal
from pydantic import BaseModel, field_validator

class ChatRequest(BaseModel):
    session_id: str
    message: str
    system_prompt: Optional[str] = None

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("session_id is required")
        v = v.strip()
        if len(v) < 1 or len(v) > 128:
            raise ValueError("session_id must be between 1 and 128 characters")
        return v

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        if v is None:
            raise ValueError("message is required")
        v = v.strip()
        if not v:
            raise ValueError("message cannot be empty")
        if len(v) > 4000:
            raise ValueError("message must be 4000 characters or less")
        return v

class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str] = None
    fallback_attempts: int

class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata

class MessageMetadata(BaseModel):
    resolution_source: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

class MessageItem(BaseModel):
    role: str
    content: str
    metadata: Optional[MessageMetadata] = None

class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: List[MessageItem]

class HealthResponse(BaseModel):
    status: str
    keys_configured: int
    cache_enabled: bool
