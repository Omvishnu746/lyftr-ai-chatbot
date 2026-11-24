import json
import logging
import random
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
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
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
AUTH_STATUS_CODES = {401, 403}
BAD_REQUEST_STATUS_CODES = {400, 422}

_KEY_REDACT_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_-]{10,}"),
    re.compile(r"([?&](?:key|api_key)=)[^&\s]+", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._-]+", re.IGNORECASE),
]

# Bounded concurrency state for LLM worker threads (prevents thread/socket accumulation)
_worker_condition = threading.Condition()
_active_worker_count = 0


def get_active_worker_count() -> int:
    """Return the current number of in-flight LLM completion worker threads."""
    with _worker_condition:
        return _active_worker_count


def _redact_secrets(text: str, keys: Optional[List[str]] = None) -> str:
    """Remove any API keys or credential tokens from log/error strings."""
    if not text:
        return ""
    sanitized = text
    for key in keys or []:
        if key and len(key) >= 4:
            sanitized = sanitized.replace(key, "[REDACTED]")
    sanitized = _KEY_REDACT_PATTERNS[0].sub("[REDACTED_KEY]", sanitized)
    sanitized = _KEY_REDACT_PATTERNS[1].sub(r"\1[REDACTED]", sanitized)
    sanitized = _KEY_REDACT_PATTERNS[2].sub(r"\1[REDACTED]", sanitized)
    return sanitized


def _extract_provider_name(model: str, exc: Optional[Exception] = None) -> str:
    """Determine provider name from exception metadata or model string."""
    if exc is not None:
        provider = getattr(exc, "llm_provider", None)
        if provider:
            return str(provider)
    if model and "/" in model:
        return model.split("/", 1)[0]
    return "unknown"


def _extract_status_code(exc: Exception) -> Optional[int]:
    """Extract HTTP status code from LiteLLM / OpenAI / httpx exceptions."""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code

    response = getattr(exc, "response", None)
    if response is not None:
        resp_code = getattr(response, "status_code", None)
        if isinstance(resp_code, int):
            return resp_code

    # Class-based fallback when status_code attribute is omitted
    if isinstance(exc, litellm.RateLimitError):
        return 429
    if isinstance(exc, litellm.ServiceUnavailableError):
        return 503
    if isinstance(exc, litellm.BadGatewayError):
        return 502
    if isinstance(exc, litellm.InternalServerError):
        return 500
    if isinstance(exc, litellm.AuthenticationError):
        return 401
    if isinstance(exc, litellm.PermissionDeniedError):
        return 403
    if isinstance(exc, litellm.NotFoundError):
        return 404
    if isinstance(exc, (litellm.BadRequestError, litellm.InvalidRequestError)):
        return 400
    if isinstance(exc, litellm.UnprocessableEntityError):
        return 422
    if isinstance(
        exc,
        (TimeoutError, litellm.Timeout, litellm.CompletionTimeout, litellm.SDKTimeout),
    ):
        return 504

    match = re.search(r"(?:status[_\s]*code[:=\s]+|HTTP\s+)([45]\d{2})\b", str(exc), re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _extract_retry_after(exc: Exception) -> Optional[float]:
    """Parse Retry-After header (seconds or HTTP-date) when available."""
    headers: Any = getattr(exc, "headers", None)
    if not headers:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None

    raw_value: Optional[str] = None
    if hasattr(headers, "items"):
        for k, v in headers.items():
            if str(k).lower() == "retry-after":
                raw_value = str(v).strip()
                break
    if not raw_value:
        return None

    try:
        seconds = float(raw_value)
        return max(0.0, seconds)
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(raw_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def _classify_provider_error(exc: Exception, keys: List[str]) -> Dict[str, Any]:
    """Classify exception into retryability, failover behavior, and safe details."""
    exc_class = type(exc).__name__
    status_code = _extract_status_code(exc)
    retry_after = _extract_retry_after(exc)
    sanitized_msg = _redact_secrets(str(exc), keys)
    if len(sanitized_msg) > 300:
        sanitized_msg = sanitized_msg[:297] + "..."

    # 1. Timeouts (check before APIConnectionError because litellm.Timeout subclasses it)
    if (
        isinstance(
            exc,
            (TimeoutError, litellm.Timeout, litellm.CompletionTimeout, litellm.SDKTimeout),
        )
        or "timeout" in exc_class.lower()
    ):
        return {
            "exception_class": exc_class,
            "status_code": status_code or 504,
            "retry_after": retry_after,
            "category": "timeout",
            "retryable_same_key": True,
            "failover_to_next_key": True,
            "message": sanitized_msg,
        }

    # 2. Authentication / Permission errors: never retry same key, but fail over to next key
    if (
        isinstance(exc, (litellm.AuthenticationError, litellm.PermissionDeniedError))
        or status_code in AUTH_STATUS_CODES
    ):
        return {
            "exception_class": exc_class,
            "status_code": status_code or 401,
            "retry_after": None,
            "category": "auth",
            "retryable_same_key": False,
            "failover_to_next_key": True,
            "message": sanitized_msg,
        }

    # 3. Invalid model / not found: never retry, never fail over across keys
    if isinstance(exc, litellm.NotFoundError) or status_code == 404:
        return {
            "exception_class": exc_class,
            "status_code": status_code or 404,
            "retry_after": None,
            "category": "invalid_model",
            "retryable_same_key": False,
            "failover_to_next_key": False,
            "message": sanitized_msg,
        }

    # 4. Bad requests / context / content policy: never retry, never fail over
    if (
        isinstance(
            exc,
            (
                litellm.BadRequestError,
                litellm.InvalidRequestError,
                litellm.UnprocessableEntityError,
                litellm.ContextWindowExceededError,
                litellm.UnsupportedParamsError,
                litellm.ContentPolicyViolationError,
            ),
        )
        or status_code in BAD_REQUEST_STATUS_CODES
    ):
        return {
            "exception_class": exc_class,
            "status_code": status_code or 400,
            "retry_after": None,
            "category": "bad_request",
            "retryable_same_key": False,
            "failover_to_next_key": False,
            "message": sanitized_msg,
        }

    # 5. Transient HTTP & temporary connection failures
    if (
        isinstance(
            exc,
            (
                litellm.RateLimitError,
                litellm.ServiceUnavailableError,
                litellm.BadGatewayError,
                litellm.InternalServerError,
                litellm.APIConnectionError,
                ConnectionError,
                OSError,
            ),
        )
        or status_code in TRANSIENT_STATUS_CODES
        or "connection" in exc_class.lower()
    ):
        category = "rate_limit" if status_code == 429 else "transient"
        return {
            "exception_class": exc_class,
            "status_code": status_code,
            "retry_after": retry_after,
            "category": category,
            "retryable_same_key": True,
            "failover_to_next_key": True,
            "message": sanitized_msg,
        }

    # 6. Unknown / generic exceptions: treat 4xx as non-retryable, others as transient
    if status_code is not None and 400 <= status_code < 500:
        return {
            "exception_class": exc_class,
            "status_code": status_code,
            "retry_after": None,
            "category": "bad_request",
            "retryable_same_key": False,
            "failover_to_next_key": False,
            "message": sanitized_msg,
        }

    return {
        "exception_class": exc_class,
        "status_code": status_code,
        "retry_after": retry_after,
        "category": "transient",
        "retryable_same_key": True,
        "failover_to_next_key": True,
        "message": sanitized_msg,
    }


def _compute_backoff_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    retry_after: Optional[float] = None,
) -> float:
    """Calculate exponential backoff with jitter, respecting Retry-After."""
    exp_delay = base_delay * (2 ** attempt)
    jitter = random.uniform(0.0, min(0.25, exp_delay * 0.5))
    delay = min(max_delay, exp_delay + jitter)
    if retry_after is not None and retry_after > 0:
        delay = max(delay, retry_after)
    return max(0.0, delay)


def _call_completion_with_timeout(
    model: str,
    messages: List[Dict[str, str]],
    api_key: str,
    timeout_seconds: float,
) -> Any:
    """
    Execute litellm.completion under both LiteLLM's network-level timeout and a
    strict absolute wall-clock deadline enforced across every phase:

      Phase 1 — slot acquisition: wait up to slot_wait_budget for a worker slot.
      Phase 2 — budget check: if the entire attempt budget is already consumed, release
                the slot and raise TimeoutError without starting a thread.
      Phase 3 — thread start + LLM call: pass the remaining budget to litellm as the
                network timeout.
      Phase 4 — wait: recalculate remaining time against the same absolute deadline
                before calling done_event.wait(), so thread-startup overhead cannot
                silently consume the caller's deadline.

    The _runner finally block is the SOLE owner of the slot counter release once the
    thread has started successfully; the main thread must NOT touch the counter after
    worker.start() succeeds, even on timeout.
    """
    global _active_worker_count

    completion_fn = litellm.completion
    max_concurrent = max(1, int(getattr(settings, "LLM_MAX_CONCURRENT_CALLS", 20)))

    if timeout_seconds <= 0:
        raise TimeoutError(
            f"LLM attempt budget exhausted before execution (budget={timeout_seconds:.3f}s)"
        )

    # Absolute deadline for the entire attempt — computed once and never adjusted.
    slot_acquire_start = time.monotonic()
    attempt_deadline = slot_acquire_start + timeout_seconds

    slot_wait_budget = min(0.5, max(0.0, timeout_seconds * 0.25))
    slot_deadline = min(attempt_deadline, slot_acquire_start + slot_wait_budget)

    # --- Phase 1: acquire a worker slot ---
    with _worker_condition:
        while _active_worker_count >= max_concurrent:
            remaining_wait = slot_deadline - time.monotonic()
            if remaining_wait <= 0:
                raise TimeoutError(
                    f"LLM worker concurrency limit ({max_concurrent}) reached; "
                    "skipping call to prevent thread accumulation"
                )
            _worker_condition.wait(timeout=remaining_wait)
        _active_worker_count += 1

    # --- Phase 2: check remaining budget (slot acquisition + lock overhead) ---
    remaining_after_slot = attempt_deadline - time.monotonic()
    if remaining_after_slot <= 0:
        with _worker_condition:
            _active_worker_count = max(0, _active_worker_count - 1)
            _worker_condition.notify_all()
        slot_elapsed = time.monotonic() - slot_acquire_start
        raise TimeoutError(
            f"LLM attempt budget exhausted during slot acquisition "
            f"(budget={timeout_seconds:.3f}s, slot_elapsed={slot_elapsed:.3f}s)"
        )

    # --- Phase 3: start worker thread with remaining budget as the LiteLLM network timeout ---
    done_event = threading.Event()
    outcome: Dict[str, Any] = {}

    def _runner() -> None:
        """Daemon thread: runs litellm.completion and releases the slot exactly once."""
        global _active_worker_count
        try:
            # Re-read remaining time at thread entry; thread startup itself takes time.
            llm_timeout = attempt_deadline - time.monotonic()
            if llm_timeout <= 0:
                outcome["error"] = TimeoutError(
                    f"LLM attempt budget exhausted before LiteLLM call "
                    f"(budget={timeout_seconds:.3f}s)"
                )
                return
            outcome["response"] = completion_fn(
                model=model,
                messages=messages,
                api_key=api_key,
                timeout=float(llm_timeout),
                num_retries=0,
                caching=False,  # Prevent LiteLLM auto-read/write of litellm.cache outside _cache_lock
            )
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc
        finally:
            done_event.set()
            with _worker_condition:
                _active_worker_count = max(0, _active_worker_count - 1)
                _worker_condition.notify_all()

    worker = threading.Thread(target=_runner, daemon=True)
    try:
        worker.start()
    except Exception:
        # Thread never started — _runner.finally will never run, so we own the counter.
        with _worker_condition:
            _active_worker_count = max(0, _active_worker_count - 1)
            _worker_condition.notify_all()
        raise

    # --- Phase 4: wait for the worker, bounded by the same absolute deadline ---
    # Recompute remaining time AFTER worker.start() to account for thread-startup overhead.
    wait_timeout = attempt_deadline - time.monotonic()
    if wait_timeout <= 0:
        if not done_event.is_set():
            # Worker thread was started; its finally block owns slot release.
            raise TimeoutError(
                f"LLM completion call timed out after {timeout_seconds:.2f}s"
            )
    else:
        finished = done_event.wait(timeout=wait_timeout)
        if not finished:
            # Worker is still running (daemon — will be abandoned). The _runner finally block
            # will decrement the counter when it eventually finishes; we must NOT touch it here.
            raise TimeoutError(
                f"LLM completion call timed out after {timeout_seconds:.2f}s"
            )

    if "error" in outcome:
        raise outcome["error"]

    return outcome.get("response")


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


def _pair_history_turns(history: List[Message]) -> List[Tuple[Dict[str, str], Dict[str, str]]]:
    """
    Group historical DB messages into chronological (user, assistant) turn pairs.
    Orphan messages (if any) are skipped so user/assistant alternation stays valid.
    """
    turns: List[Tuple[Dict[str, str], Dict[str, str]]] = []
    pending_user: Optional[Dict[str, str]] = None

    for msg in history:
        if msg.role == "user":
            pending_user = {"role": "user", "content": msg.content}
        elif msg.role == "assistant" and pending_user is not None:
            turns.append((pending_user, {"role": "assistant", "content": msg.content}))
            pending_user = None

    return turns


def _truncate_content(content: str, max_chars: int) -> str:
    """Truncate oversized historical message content cleanly."""
    if len(content) <= max_chars:
        return content
    suffix = "... [truncated]"
    if max_chars <= len(suffix):
        return content[:max_chars]
    return content[: max_chars - len(suffix)] + suffix


def build_messages(
    db: Session,
    session_id: str,
    new_message: str,
    system_prompt: Optional[str] = None
) -> List[Dict[str, str]]:
    """
    Build chronological message history for LiteLLM with a sliding window of the
    last N conversation turns and character budget protection.
    Historical rows in SQLite are never modified or deleted.
    """
    messages: List[Dict[str, str]] = []

    # Optional system prompt (always preserved)
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})

    # Prior messages from SQLite in chronological order
    history = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.id.asc())
        .all()
    )

    max_turns = max(0, int(settings.LLM_MAX_HISTORY_TURNS))
    max_history_chars = max(500, int(settings.LLM_MAX_HISTORY_CHARS))

    turns = _pair_history_turns(history)
    if max_turns == 0:
        turns = []
    elif len(turns) > max_turns:
        turns = turns[-max_turns:]

    # Walk newest-to-oldest within the sliding window to enforce character budget
    selected_turns: List[Tuple[Dict[str, str], Dict[str, str]]] = []
    accumulated_chars = 0

    for user_msg, assistant_msg in reversed(turns):
        turn_chars = len(user_msg["content"]) + len(assistant_msg["content"])
        if not selected_turns and turn_chars > max_history_chars:
            # Ensure even a single oversized recent turn stays within budget
            per_msg_cap = max(250, max_history_chars // 2)
            trimmed_user = {
                "role": "user",
                "content": _truncate_content(user_msg["content"], per_msg_cap),
            }
            trimmed_assistant = {
                "role": "assistant",
                "content": _truncate_content(assistant_msg["content"], per_msg_cap),
            }
            selected_turns.append((trimmed_user, trimmed_assistant))
            break

        if accumulated_chars + turn_chars > max_history_chars:
            break

        selected_turns.append((user_msg, assistant_msg))
        accumulated_chars += turn_chars

    # Restore chronological order
    for user_msg, assistant_msg in reversed(selected_turns):
        messages.append(user_msg)
        messages.append(assistant_msg)

    # Newly entered user message (always preserved last)
    messages.append({"role": "user", "content": new_message.strip()})
    return messages


def estimate_tokens(text: str) -> int:
    """Fallback token estimation when provider usage metadata is missing."""
    if not text:
        return 1
    return max(1, len(text.split()) * 4 // 3)


def _build_failure_http_exception(
    model: str,
    fallback_attempts: int,
    total_elapsed_ms: int,
    failure_records: List[Dict[str, Any]],
    simulated_only: bool,
) -> HTTPException:
    """Create a meaningful, secret-free HTTPException from failure records."""
    if simulated_only or not failure_records:
        return HTTPException(status_code=503, detail="all providers unavailable")

    last = failure_records[-1]
    category = last["category"]
    status_code = last["status_code"]
    exc_class = last["exception_class"]
    safe_msg = last["message"]

    if category == "invalid_model":
        return HTTPException(
            status_code=400,
            detail=(
                f"Configured model '{model}' was not found or is unavailable "
                f"(HTTP {status_code or 404})."
            ),
        )

    if category == "bad_request":
        return HTTPException(
            status_code=400,
            detail=(
                f"Provider rejected request (HTTP {status_code or 400}, {exc_class}): "
                f"{safe_msg}"
            ),
        )

    if all(r["category"] == "auth" for r in failure_records):
        return HTTPException(
            status_code=503,
            detail=(
                f"Provider authentication failed across configured API keys "
                f"(HTTP {status_code or 401}). Please verify API key configuration."
            ),
        )

    if category == "timeout":
        return HTTPException(
            status_code=504,
            detail=(
                f"AI provider timed out across {len(failure_records)} attempt(s) "
                f"after {total_elapsed_ms} ms. Please try again."
            ),
        )

    if category == "rate_limit" or status_code == 429:
        return HTTPException(
            status_code=503,
            detail=(
                f"All AI provider keys are temporarily rate-limited (HTTP 429) "
                f"after {fallback_attempts} failover attempt(s). Please try again shortly."
            ),
        )

    return HTTPException(
        status_code=503,
        detail=(
            f"AI provider temporarily unavailable ({exc_class}, "
            f"HTTP {status_code or 'N/A'}) after {len(failure_records)} attempt(s) "
            f"in {total_elapsed_ms} ms. Please try again."
        ),
    )


def execute_chat(
    db: Session,
    session_id: str,
    message: str,
    system_prompt: Optional[str] = None,
    simulate_header: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute chat with bounded timeouts, intelligent retry & dual-key failover,
    response caching, token tracking, and failure simulation.
    """
    start_time = time.perf_counter()

    # 1. Build bounded chronological messages payload
    messages = build_messages(db, session_id, message, system_prompt)
    model = settings.LLM_MODEL
    provider_name = _extract_provider_name(model)

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
            user_msg = Message(
                session_id=session_id,
                role="user",
                content=message.strip(),
                metadata_json=None
            )
            db.add(user_msg)

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

    # 4. Key pool execution with per-key retry + cross-key failover + total deadline
    keys = settings.api_keys
    if not keys:
        logger.error("No API keys configured | provider=%s model=%s", provider_name, model)
        raise HTTPException(status_code=503, detail="all providers unavailable")

    total_deadline_sec = max(0.2, float(settings.LLM_TOTAL_DEADLINE_SECONDS))
    call_timeout_sec = max(0.05, float(settings.LLM_REQUEST_TIMEOUT_SECONDS))
    max_retries_per_key = max(0, int(settings.LLM_MAX_RETRIES_PER_KEY))
    base_delay = max(0.0, float(settings.LLM_RETRY_BASE_DELAY_SECONDS))
    max_delay = max(base_delay, float(settings.LLM_RETRY_MAX_DELAY_SECONDS))

    request_deadline = time.monotonic() + total_deadline_sec

    fallback_attempts = 0
    chosen_key_id: Optional[str] = None
    successful_attempt_num = 1
    reply_text: Optional[str] = None
    input_tokens = 0
    output_tokens = 0

    failure_records: List[Dict[str, Any]] = []
    simulated_failures = 0
    abort_failover = False

    for idx, key in enumerate(keys):
        if abort_failover:
            break

        current_key_id = f"key_{idx + 1}"

        # Check simulation rule for this key or wildcard
        sim_code = sim_rules.get(current_key_id) or sim_rules.get("*")
        if sim_code is not None:
            logger.info(
                "Simulating failure | provider=%s model=%s key_id=%s status_code=%s",
                provider_name,
                model,
                current_key_id,
                sim_code,
            )
            simulated_failures += 1
            fallback_attempts += 1
            continue

        remaining_keys_after = len(keys) - idx - 1
        # Reserve enough budget so key_1 cannot starve key_2
        reserve_for_remaining = (
            remaining_keys_after * min(call_timeout_sec, total_deadline_sec / len(keys))
        )
        key_deadline = request_deadline - reserve_for_remaining

        max_attempts_for_key = max_retries_per_key + 1

        for attempt in range(max_attempts_for_key):
            remaining_for_key = key_deadline - time.monotonic()
            if remaining_for_key <= 0.05:
                logger.warning(
                    "Key budget exhausted before attempt | provider=%s model=%s key_id=%s attempt=%d",
                    provider_name,
                    model,
                    current_key_id,
                    attempt + 1,
                )
                if remaining_keys_after > 0:
                    fallback_attempts += 1
                break

            attempt_timeout = min(call_timeout_sec, remaining_for_key)
            attempt_start = time.perf_counter()

            try:
                logger.info(
                    "Calling provider | provider=%s model=%s key_id=%s attempt=%d/%d timeout=%.2fs",
                    provider_name,
                    model,
                    current_key_id,
                    attempt + 1,
                    max_attempts_for_key,
                    attempt_timeout,
                )
                response = _call_completion_with_timeout(
                    model=model,
                    messages=messages,
                    api_key=key,
                    timeout_seconds=attempt_timeout,
                )

                # Extract response text
                reply_text = response.choices[0].message.content or ""

                # Extract token usage
                usage = getattr(response, "usage", None)
                if usage is not None:
                    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

                if input_tokens <= 0:
                    all_text = " ".join([m["content"] for m in messages])
                    input_tokens = estimate_tokens(all_text)
                if output_tokens <= 0:
                    output_tokens = estimate_tokens(reply_text)

                chosen_key_id = current_key_id
                successful_attempt_num = attempt + 1
                break

            except Exception as exc:  # noqa: BLE001
                attempt_elapsed_ms = max(1, int((time.perf_counter() - attempt_start) * 1000))
                info = _classify_provider_error(exc, keys)
                exc_provider = _extract_provider_name(model, exc)
                failure_records.append(
                    {
                        "key_id": current_key_id,
                        "attempt": attempt + 1,
                        "elapsed_ms": attempt_elapsed_ms,
                        **info,
                    }
                )

                logger.warning(
                    "Provider call failed | provider=%s model=%s key_id=%s attempt=%d/%d "
                    "exception=%s status_code=%s elapsed_ms=%d retry_after=%s error=%s",
                    exc_provider,
                    model,
                    current_key_id,
                    attempt + 1,
                    max_attempts_for_key,
                    info["exception_class"],
                    info["status_code"],
                    attempt_elapsed_ms,
                    info["retry_after"],
                    info["message"],
                )

                # Non-retryable request/model errors should abort immediately
                if not info["failover_to_next_key"]:
                    abort_failover = True
                    break

                # Key-specific non-retryable errors (e.g. 401/403) skip retries and fail over
                if not info["retryable_same_key"]:
                    fallback_attempts += 1
                    break

                # On timeout, avoid piling abandoned in-flight calls onto the same stalled key
                # when a failover key is available (or when remaining key budget is below a full call).
                if info["category"] == "timeout" and (
                    remaining_keys_after > 0
                    or (key_deadline - time.monotonic()) < call_timeout_sec
                ):
                    logger.info(
                        "Timeout on %s; failing over immediately to avoid duplicate stalled calls",
                        current_key_id,
                    )
                    fallback_attempts += 1
                    break

                # Exhausted configured retries for this key -> fail over
                if attempt >= max_retries_per_key:
                    fallback_attempts += 1
                    break

                # Compute backoff with jitter + Retry-After, bounded by remaining key budget
                delay = _compute_backoff_delay(
                    attempt=attempt,
                    base_delay=base_delay,
                    max_delay=max_delay,
                    retry_after=info["retry_after"],
                )
                remaining_after_error = key_deadline - time.monotonic()
                min_call_window = min(0.5, call_timeout_sec * 0.5)

                # If waiting (e.g. large Retry-After or tight budget) leaves insufficient
                # time for another call on this key, fail over immediately to the next key.
                if delay >= remaining_after_error - min_call_window:
                    logger.info(
                        "Skipping remaining retries for %s (delay=%.2fs, remaining_budget=%.2fs); failing over",
                        current_key_id,
                        delay,
                        max(0.0, remaining_after_error),
                    )
                    fallback_attempts += 1
                    break

                if delay > 0:
                    time.sleep(delay)

        if chosen_key_id is not None:
            break

    total_elapsed_ms = max(1, int((time.perf_counter() - start_time) * 1000))

    # If all keys failed
    if chosen_key_id is None or reply_text is None:
        failover_summary = [
            f"{r['key_id']}#{r['attempt']}:{r['exception_class']}({r['status_code']})"
            for r in failure_records
        ]
        logger.error(
            "LLM request failed | provider=%s model=%s failover_succeeded=False "
            "fallback_attempts=%d latency_ms=%d attempts=%s",
            provider_name,
            model,
            fallback_attempts,
            total_elapsed_ms,
            failover_summary,
        )
        db.rollback()
        simulated_only = simulated_failures > 0 and not failure_records
        raise _build_failure_http_exception(
            model=model,
            fallback_attempts=fallback_attempts,
            total_elapsed_ms=total_elapsed_ms,
            failure_records=failure_records,
            simulated_only=simulated_only,
        )

    failover_succeeded = chosen_key_id != "key_1" and fallback_attempts > 0
    logger.info(
        "LLM request succeeded | provider=%s model=%s key_id=%s attempt=%d "
        "failover_succeeded=%s fallback_attempts=%d latency_ms=%d",
        provider_name,
        model,
        chosen_key_id,
        successful_attempt_num,
        failover_succeeded,
        fallback_attempts,
        total_elapsed_ms,
    )

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
            "latency_ms": total_elapsed_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "key_id": chosen_key_id,
            "fallback_attempts": fallback_attempts
        }
    }
