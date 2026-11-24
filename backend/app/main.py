import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import init_db, check_db_health, get_db
from app.models import Message
from app.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    SessionMessagesResponse,
    MessageItem,
    MessageMetadata,
)
from app.cache_service import init_cache, is_cache_enabled
from app.llm_service import execute_chat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("lyftr_app")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Lyftr AI backend...")
    init_db()
    init_cache()
    yield
    # Shutdown
    logger.info("Lyftr AI backend shutting down...")

app = FastAPI(
    title="Lyftr AI Chatbot API",
    description="LLM Chatbot with Key Failover and Response Caching",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom validation exception handler to ensure {"detail": ...} JSON format
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Extract readable detail message
    errors = exc.errors()
    first_error = errors[0] if errors else {}
    msg = first_error.get("msg", "Validation error")
    loc = " -> ".join(str(l) for l in first_error.get("loc", []))
    detail = f"{loc}: {msg}" if loc else msg
    return JSONResponse(status_code=422, content={"detail": detail})

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled server error: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health", response_model=HealthResponse)
def health_check():
    """
    Health check endpoint.
    Returns HTTP 200 only when:
    - Backend is running.
    - Database is accessible.
    - At least two API keys are configured.
    - Cache is enabled.
    """
    db_ok = check_db_health()
    keys_count = len(settings.api_keys)
    cache_ok = is_cache_enabled()

    if not db_ok:
        raise HTTPException(status_code=503, detail="Database is inaccessible")

    if keys_count < 2:
        raise HTTPException(
            status_code=503,
            detail=f"At least two API keys are required. Currently configured: {keys_count}"
        )

    if not cache_ok:
        raise HTTPException(status_code=503, detail="Cache is disabled or uninitialized")

    return HealthResponse(
        status="ok",
        keys_configured=keys_count,
        cache_enabled=cache_ok
    )


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(
    req: ChatRequest,
    db: Session = Depends(get_db),
    x_simulate_failure: Optional[str] = Header(None, alias="X-Simulate-Failure")
):
    """
    Main chat endpoint with key failover, response caching,
    and failure simulation.
    """
    result = execute_chat(
        db=db,
        session_id=req.session_id,
        message=req.message,
        system_prompt=req.system_prompt,
        simulate_header=x_simulate_failure
    )
    return result


@app.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
def get_session_messages(session_id: str, db: Session = Depends(get_db)):
    """
    Retrieve message history for a session in chronological order.
    Returns 404 if the session does not exist.
    """
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=404, detail="session not found")

    messages = (
        db.query(Message)
        .filter(Message.session_id == session_id.strip())
        .order_by(Message.id.asc())
        .all()
    )

    if not messages:
        raise HTTPException(status_code=404, detail="session not found")

    items = []
    for msg in messages:
        meta_item = None
        if msg.role == "assistant" and msg.metadata_json:
            try:
                raw_meta = json.loads(msg.metadata_json)
                meta_item = MessageMetadata(
                    resolution_source=raw_meta.get("resolution_source"),
                    input_tokens=raw_meta.get("input_tokens"),
                    output_tokens=raw_meta.get("output_tokens"),
                )
            except Exception:
                meta_item = None

        items.append(
            MessageItem(
                role=msg.role,
                content=msg.content,
                metadata=meta_item
            )
        )

    return SessionMessagesResponse(
        session_id=session_id.strip(),
        messages=items
    )
