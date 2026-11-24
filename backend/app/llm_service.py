import json
import logging
import time
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session
import litellm

from app.config import settings
from app.models import Message
from app.cache_service import (
    get_cached_response,
    set_cached_response,
)

logger = logging.getLogger(__name__)

SUPPORTED_SIMULATION_STATUSES = {429, 500, 502, 503}

def parse_simulation_header(header_val: Optional[str]) -> Dict[str, int]:
    """
    Parse X-Simulate-Failure header into a mapping of key_id/wildcard -> HTTP status code.
    Examples:
      'key_1=429' -> {'key_1': 429}
      'key_1=429,key_2=500' -> {'key_1': 429, 'key_2': 500}
      '*=429' -> {'*': 429}
    """
    if not header_val or not settings.ENABLE_FAILURE_SIMULATION:
        return {}

    rules: Dict[str, int] = {}
    parts = [p.strip() for p in header_val.split(",") if p.strip()]
    for part in parts:
        if "=" in part:
            target, code_str = part.split("=", 1)
            target = target.strip()
            try:
                code = int(code_str.strip())
                if code in SUPPORTED_SIMULATION_STATUSES:
                    rules[target] = code
            except ValueError:
                continue
    return rules

def build_messages(
    db: Session,
    session_id: str,
    new_message: str,
    system_prompt: Optional[str] = None
) -> List[Dict[str, str]]:
    """Build complete chronological message history for LiteLLM."""
    messages: List[Dict[str, str]] = []

    # Optional system prompt
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})

    # Prior messages from SQLite in chronological order
    history = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.id.asc())
        .all()
    )

    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})

    # Newly entered user message
    messages.append({"role": "user", "content": new_message.strip()})
    return messages

def estimate_tokens(text: str) -> int:
    """Fallback token estimation when provider usage metadata is missing."""
    if not text:
        return 1
    return max(1, len(text.split()) * 4 // 3)

def execute_chat(
    db: Session,
    session_id: str,
    message: str,
    system_prompt: Optional[str] = None,
    simulate_header: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute chat with automatic key failover, response caching,
    token tracking, and failure simulation.
    """
    start_time = time.perf_counter()

    # 1. Build complete messages payload
    messages = build_messages(db, session_id, message, system_prompt)
    model = settings.LLM_MODEL

    # 2. Check failure simulation header
    sim_rules = parse_simulation_header(simulate_header)
    is_simulating = bool(sim_rules)

    # 3. Check LiteLLM cache (bypass if failure simulation requested)
    if not is_simulating:
        cached_entry = get_cached_response(model=model, messages=messages)
        if cached_entry is not None:
            latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))
            reply = cached_entry["reply"]
            input_tokens = int(cached_entry["input_tokens"])
            output_tokens = int(cached_entry["output_tokens"])

            # Store in database history
            # User message
            user_msg = Message(
                session_id=session_id,
                role="user",
                content=message.strip(),
                metadata_json=None
            )
            db.add(user_msg)

            # Assistant message
            assistant_meta = {
                "resolution_source": "LLM_CACHE",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }
            assistant_msg = Message(
                session_id=session_id,
                role="assistant",
                content=reply,
                metadata_json=json.dumps(assistant_meta)
            )
            db.add(assistant_msg)
            db.commit()

            return {
                "session_id": session_id,
                "reply": reply,
                "metadata": {
                    "resolution_source": "LLM_CACHE",
                    "model": model,
                    "latency_ms": latency_ms,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "key_id": None,
                    "fallback_attempts": 0
                }
            }

    # 4. Key pool execution with failover
    keys = settings.api_keys
    if not keys:
        logger.error("No API keys configured")
        raise HTTPException(status_code=503, detail="all providers unavailable")

    fallback_attempts = 0
    chosen_key_id: Optional[str] = None
    reply_text: Optional[str] = None
    input_tokens = 0
    output_tokens = 0

    for idx, key in enumerate(keys):
        current_key_id = f"key_{idx + 1}"

        # Check simulation rule for this key or wildcard
        sim_code = sim_rules.get(current_key_id) or sim_rules.get("*")
        if sim_code is not None:
            logger.info(f"Simulating failure {sim_code} for {current_key_id}")
            fallback_attempts += 1
            continue

        try:
            logger.info(f"Calling provider with {current_key_id} and model {model}")
            response = litellm.completion(
                model=model,
                messages=messages,
                api_key=key,
            )

            # Extract response text
            reply_text = response.choices[0].message.content or ""

            # Extract token usage
            usage = getattr(response, "usage", None)
            if usage is not None:
                input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

            if input_tokens <= 0:
                # Estimate if missing
                all_text = " ".join([m["content"] for m in messages])
                input_tokens = estimate_tokens(all_text)
            if output_tokens <= 0:
                output_tokens = estimate_tokens(reply_text)

            chosen_key_id = current_key_id
            break

        except Exception as e:
            logger.warning(f"Provider call failed for {current_key_id}: {e}")
            fallback_attempts += 1
            continue

    # If all keys failed
    if chosen_key_id is None or reply_text is None:
        logger.error("All providers unavailable or failed")
        # Ensure no messages are added to history
        db.rollback()
        raise HTTPException(status_code=503, detail="all providers unavailable")

    latency_ms = max(1, int((time.perf_counter() - start_time) * 1000))

    # 5. Cache response in LiteLLM cache if not simulating
    if not is_simulating:
        set_cached_response(
            model=model,
            messages=messages,
            data={
                "reply": reply_text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "model": model,
            }
        )

    # 6. Store user and assistant messages in database history
    user_msg = Message(
        session_id=session_id,
        role="user",
        content=message.strip(),
        metadata_json=None
    )
    db.add(user_msg)

    assistant_meta = {
        "resolution_source": "LIVE_INFERENCE",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    assistant_msg = Message(
        session_id=session_id,
        role="assistant",
        content=reply_text,
        metadata_json=json.dumps(assistant_meta)
    )
    db.add(assistant_msg)
    db.commit()

    return {
        "session_id": session_id,
        "reply": reply_text,
        "metadata": {
            "resolution_source": "LIVE_INFERENCE",
            "model": model,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "key_id": chosen_key_id,
            "fallback_attempts": fallback_attempts
        }
    }
