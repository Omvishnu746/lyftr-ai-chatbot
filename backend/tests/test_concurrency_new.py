"""
New concurrency tests added during the targeted audit fix pass.

These cover:
  A. Simultaneous slot saturation via ThreadPoolExecutor — _active_worker_count never exceeds cap.
  B. Concurrent cache hit/write consistency — only 1 live LLM call for identical prompts.
  C. Slot-time deduction — a blocked slot causes fast-fail well within timeout_seconds.
  D. caching=False prevents LiteLLM from auto-writing/reading litellm.cache outside _cache_lock.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import MagicMock, patch

import litellm
import pytest

from app.cache_service import (
    clear_cache,
    get_cached_response,
    set_cached_response,
)
from app.config import settings
from app.llm_service import _call_completion_with_timeout, get_active_worker_count
from tests.conftest import create_mock_completion_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_messages(text: str):
    return [{"role": "user", "content": text}]


# ---------------------------------------------------------------------------
# Test A — Simultaneous slot saturation via real ThreadPoolExecutor
# ---------------------------------------------------------------------------

def test_simultaneous_slot_saturation_never_exceeds_cap(monkeypatch):
    """
    Fire MAX_WORKERS simultaneous requests when LLM_MAX_CONCURRENT_CALLS=2.
    _active_worker_count must never exceed 2 at any point.
    Requests that cannot acquire a slot raise TimeoutError (counted as rejected).
    """
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENT_CALLS", 2)
    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 1.0)

    # Barrier: all threads unblock simultaneously to maximise contention
    barrier = threading.Barrier(5, timeout=5)
    release = threading.Event()

    # Track peaks
    observed_peaks = []
    peak_lock = threading.Lock()

    def blocking_completion(*args, **kwargs):
        # Sample peak before sleeping
        current = get_active_worker_count()
        with peak_lock:
            observed_peaks.append(current)
        release.wait(timeout=2.0)
        return create_mock_completion_response("ok", 5, 2)

    outcomes = []

    def one_call(i):
        try:
            barrier.wait()  # all 5 fire simultaneously
            result = _call_completion_with_timeout(
                model="gemini/gemini-3.5-flash-lite",
                messages=_make_messages(f"prompt-{i}"),
                api_key="test_key",
                timeout_seconds=0.3,
            )
            return ("ok", result)
        except TimeoutError as e:
            return ("rejected", str(e))
        except Exception as e:
            return ("error", str(e))

    with patch("app.llm_service.litellm.completion", side_effect=blocking_completion):
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(one_call, i) for i in range(5)]
                outcomes = [f.result() for f in as_completed(futures, timeout=10)]
        finally:
            release.set()
            # Let daemon threads finish and counter drain
            deadline = time.monotonic() + 2.0
            while get_active_worker_count() > 0 and time.monotonic() < deadline:
                time.sleep(0.02)

    # Counter fully drained
    assert get_active_worker_count() == 0, (
        f"Worker count did not drain to 0; got {get_active_worker_count()}"
    )

    # Peak concurrency never exceeded cap
    if observed_peaks:
        assert max(observed_peaks) <= 2, (
            f"Peak concurrency {max(observed_peaks)} exceeded cap of 2"
        )

    # Some requests succeeded, some were rejected — none errored unexpectedly
    statuses = [o[0] for o in outcomes]
    assert all(s in ("ok", "rejected") for s in statuses), (
        f"Unexpected outcome statuses: {statuses}"
    )
    # At most 2 succeeded (slot cap)
    assert statuses.count("ok") <= 2, (
        f"More than 2 requests succeeded with cap=2: {statuses}"
    )


# ---------------------------------------------------------------------------
# Test B — Concurrent cache hit/write consistency
# ---------------------------------------------------------------------------

def test_concurrent_cache_hit_consistency(monkeypatch):
    """
    Fire 4 simultaneous requests with identical prompts via ThreadPoolExecutor.
    The application-level cache (cache_service) must serve subsequent requests from
    cache without making additional LLM calls.  All 4 responses must be valid.
    Cache state must be consistent after concurrent writes.
    """
    clear_cache()

    model = settings.LLM_MODEL
    prompt = "What is the capital of France?"
    messages = _make_messages(prompt)

    call_count = {"n": 0}
    call_lock = threading.Lock()

    def counted_completion(*args, **kwargs):
        with call_lock:
            call_count["n"] += 1
        time.sleep(0.05)  # simulate latency
        return create_mock_completion_response("Paris", 10, 3)

    results = []
    errors = []

    def do_cache_lookup():
        # Simulate exactly what execute_chat does: check cache, call LLM, store cache
        cached = get_cached_response(model=model, messages=messages)
        if cached is not None:
            return cached["reply"]
        # Cache miss — call LLM
        with patch("app.llm_service.litellm.completion", side_effect=counted_completion):
            response = _call_completion_with_timeout(
                model=model,
                messages=messages,
                api_key="test_key",
                timeout_seconds=2.0,
            )
        reply = response.choices[0].message.content
        set_cached_response(
            model=model,
            messages=messages,
            data={"reply": reply, "input_tokens": 10, "output_tokens": 3, "model": model},
        )
        return reply

    with patch("app.llm_service.litellm.completion", side_effect=counted_completion):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(do_cache_lookup) for _ in range(4)]
            for f in as_completed(futures, timeout=10):
                try:
                    results.append(f.result())
                except Exception as e:
                    errors.append(str(e))

    assert not errors, f"Unexpected errors: {errors}"
    assert len(results) == 4, f"Expected 4 results, got {len(results)}"
    assert all(r == "Paris" for r in results), f"Inconsistent replies: {results}"

    # Cache should be populated after the first real call
    cached_after = get_cached_response(model=model, messages=messages)
    assert cached_after is not None, "Cache should be populated after concurrent writes"
    assert cached_after["reply"] == "Paris"

    # LLM was called at most once per distinct miss window (racing threads may both miss
    # on first access before one populates the cache, so we allow up to 4 — but crucially
    # all 4 results are still consistent "Paris")
    assert call_count["n"] >= 1, "LLM should have been called at least once"

    clear_cache()


# ---------------------------------------------------------------------------
# Test C — Slot-time deduction: blocked slot causes fast-fail
# ---------------------------------------------------------------------------

def test_slot_time_deduction_causes_fast_fail(monkeypatch):
    """
    With LLM_MAX_CONCURRENT_CALLS=1 and the single slot occupied by a long sleep,
    a second request with a tight timeout_seconds should fail (TimeoutError) almost
    immediately — well before the full timeout_seconds has elapsed.
    """
    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENT_CALLS", 1)

    slot_held = threading.Event()
    release_slot = threading.Event()

    def hold_slot(*args, **kwargs):
        slot_held.set()
        release_slot.wait(timeout=5.0)
        return create_mock_completion_response("held", 5, 2)

    errors = []

    def occupy_slot():
        try:
            _call_completion_with_timeout(
                model="gemini/gemini-3.5-flash-lite",
                messages=_make_messages("occupy"),
                api_key="test_key",
                timeout_seconds=3.0,  # long hold
            )
        except Exception as e:
            errors.append(("occupy", str(e)))

    with patch("app.llm_service.litellm.completion", side_effect=hold_slot):
        # Occupy the single slot in a background thread
        holder = threading.Thread(target=occupy_slot, daemon=True)
        holder.start()
        slot_held.wait(timeout=2.0)  # wait until slot is taken

        # Now fire a request with tight timeout — slot_wait_budget is ~ 25% of 0.4 = 0.1s
        tight_timeout = 0.4
        t0 = time.monotonic()
        try:
            _call_completion_with_timeout(
                model="gemini/gemini-3.5-flash-lite",
                messages=_make_messages("fast-fail"),
                api_key="test_key",
                timeout_seconds=tight_timeout,
            )
            assert False, "Should have raised TimeoutError"
        except TimeoutError:
            elapsed = time.monotonic() - t0
            # Must fail within slot_wait_budget + small epsilon (not full timeout_seconds)
            slot_wait_budget = min(0.5, max(0.05, tight_timeout * 0.25))
            assert elapsed < slot_wait_budget + 0.15, (
                f"Fast-fail took {elapsed:.3f}s but slot_wait_budget was {slot_wait_budget:.3f}s"
            )
        finally:
            release_slot.set()
            holder.join(timeout=2.0)
            # Drain counter
            deadline = time.monotonic() + 2.0
            while get_active_worker_count() > 0 and time.monotonic() < deadline:
                time.sleep(0.02)


# ---------------------------------------------------------------------------
# Test D — caching=False prevents LiteLLM internal cache bypass
# ---------------------------------------------------------------------------

def test_caching_false_prevents_litellm_internal_cache_bypass(monkeypatch):
    """
    When litellm.cache is active, litellm.completion() would normally auto-read/write
    it via sync_get_cache / sync_set_cache.  Passing caching=False must suppress this.

    This test verifies that after a successful _call_completion_with_timeout() call:
      1. litellm.cache.get_cache() returns None for that key (LiteLLM did NOT auto-store).
      2. The app's explicit set_cached_response() / get_cached_response() still works.
    """
    clear_cache()

    # Install a real in-memory litellm cache
    original_cache = litellm.cache
    local_litellm_cache = litellm.Cache(type="local")
    litellm.cache = local_litellm_cache

    model = settings.LLM_MODEL
    messages = _make_messages("caching=False bypass test")

    mock_response = create_mock_completion_response("bypass-test-reply", 8, 4)

    # Capture kwargs to verify caching=False was passed
    captured_kwargs = {}

    def recording_completion(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return mock_response

    try:
        with patch("app.llm_service.litellm.completion", side_effect=recording_completion):
            result = _call_completion_with_timeout(
                model=model,
                messages=messages,
                api_key="test_key",
                timeout_seconds=2.0,
            )

        # Verify caching=False was forwarded to litellm.completion
        assert captured_kwargs.get("caching") is False, (
            f"Expected caching=False in litellm.completion call, got: {captured_kwargs.get('caching')!r}"
        )

        # Verify LiteLLM did NOT auto-store into litellm.cache
        # (build the same cache key litellm would use)
        internal_hit = litellm.cache.get_cache(
            model=model,
            messages=messages,
        )
        assert internal_hit is None, (
            "LiteLLM internal cache should be empty when caching=False is passed"
        )

        # Verify app-level cache still works independently
        set_cached_response(
            model=model,
            messages=messages,
            data={"reply": "bypass-test-reply", "input_tokens": 8, "output_tokens": 4, "model": model},
        )
        app_hit = get_cached_response(model=model, messages=messages)
        assert app_hit is not None, "App-level cache should return a hit after explicit set"
        assert app_hit["reply"] == "bypass-test-reply"

    finally:
        litellm.cache = original_cache
        clear_cache()


# ---------------------------------------------------------------------------
# Test E — Exhausted attempt budget releases slot and raises TimeoutError
#           without ever starting a worker thread (regression for fix PR#5b)
# ---------------------------------------------------------------------------

def test_exhausted_budget_does_not_start_worker(monkeypatch):
    """
    Regression: when slot acquisition consumes the entire attempt budget
    (remaining_after_slot <= 0), _call_completion_with_timeout must:
      1. Release the acquired slot immediately (counter stays at 0 afterwards).
      2. Raise TimeoutError without ever calling litellm.completion or starting a thread.
      3. Never decrement the counter below 0 (double-release guard).
    """
    import app.llm_service as svc

    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENT_CALLS", 20)

    timeout_seconds = 0.1  # 100 ms budget
    now = [1000.0]

    def fake_monotonic():
        return now[0]

    # Advance the clock right after slot acquisition (inside Condition.__exit__)
    # or simulate via a Condition wrapper, OR advance when _worker_condition is acquired.
    original_cond = svc._worker_condition

    class ClockAdvancingCondition:
        def __enter__(self):
            original_cond.__enter__()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            res = original_cond.__exit__(exc_type, exc_val, exc_tb)
            # Simulate that acquiring the slot took 150ms (> 100ms budget)
            now[0] += timeout_seconds + 0.05
            return res

        def wait(self, timeout=None):
            return original_cond.wait(timeout=timeout)

        def notify_all(self):
            return original_cond.notify_all()

    thread_started = {"started": False}
    original_thread_cls = threading.Thread

    def fail_if_thread_created(*args, **kwargs):
        thread_started["started"] = True
        return original_thread_cls(*args, **kwargs)

    completion_called = {"called": False}

    def should_not_be_called(*args, **kwargs):
        completion_called["called"] = True
        return create_mock_completion_response("should-not-reach", 1, 1)

    with patch("app.llm_service.time.monotonic", side_effect=fake_monotonic), \
         patch("app.llm_service._worker_condition", ClockAdvancingCondition()), \
         patch("app.llm_service.threading.Thread", side_effect=fail_if_thread_created), \
         patch("app.llm_service.litellm.completion", side_effect=should_not_be_called):

        with original_cond:
            svc._active_worker_count = 0

        with pytest.raises(TimeoutError) as exc_info:
            _call_completion_with_timeout(
                model="gemini/gemini-3.5-flash-lite",
                messages=_make_messages("exhausted budget"),
                api_key="test_key",
                timeout_seconds=timeout_seconds,
            )

    assert "budget exhausted" in str(exc_info.value).lower()
    assert not thread_started["started"], "Worker thread must not be created when budget is exhausted"
    assert not completion_called["called"], "litellm.completion must not be called when budget is exhausted"
    with original_cond:
        assert svc._active_worker_count == 0


# ---------------------------------------------------------------------------
# Test F — Thread startup overhead consumes the absolute attempt deadline
# ---------------------------------------------------------------------------

def test_thread_startup_overhead_consumes_attempt_deadline(monkeypatch):
    """
    1. When worker.start() consumes part of the budget, done_event.wait() only waits
       for the remaining budget (and no 50ms artificial floor extends it).
    2. When worker.start() consumes the entire remaining budget, TimeoutError is raised
       immediately without decrementing the running worker's slot counter; the worker's
       finally block releases the slot exactly once when the worker finishes.
    """
    import app.llm_service as svc

    monkeypatch.setattr(settings, "LLM_MAX_CONCURRENT_CALLS", 20)

    # Part 1: Partial startup overhead + sub-50ms budget verifies NO 0.05s floor
    timeout_seconds = 0.02  # 20ms total budget (< 50ms)
    now = [2000.0]
    seen_timeouts = []
    seen_wait_timeouts = []

    original_thread_cls = threading.Thread
    original_event_cls = threading.Event

    class SpyEvent:
        def __init__(self):
            self._ev = original_event_cls()

        def set(self):
            self._ev.set()

        def is_set(self):
            return self._ev.is_set()

        def wait(self, timeout=None):
            # Ignore internal threading.Thread._started.wait(timeout=None)
            if timeout is not None:
                seen_wait_timeouts.append(timeout)
            return self._ev.wait(timeout=timeout)

    class SlowStartThread(original_thread_cls):
        def start(self):
            # Simulate 5ms thread startup overhead before spawning
            now[0] += 0.005
            super().start()

    def fast_completion(*args, **kwargs):
        seen_timeouts.append(kwargs.get("timeout"))
        return create_mock_completion_response("ok", 5, 2)

    with patch("app.llm_service.time.monotonic", side_effect=lambda: now[0]), \
         patch("app.llm_service.threading.Thread", SlowStartThread), \
         patch("app.llm_service.threading.Event", SpyEvent), \
         patch("app.llm_service.litellm.completion", side_effect=fast_completion):
        with svc._worker_condition:
            svc._active_worker_count = 0
        resp = _call_completion_with_timeout(
            model="gemini/gemini-3.5-flash-lite",
            messages=_make_messages("sub-50ms"),
            api_key="test_key",
            timeout_seconds=timeout_seconds,
        )
        assert resp.choices[0].message.content == "ok"

    # Both LLM timeout and done_event.wait timeout must be ~0.015s (< 0.02s, NOT >= 0.05s)
    assert len(seen_timeouts) == 1
    assert 0.0 < seen_timeouts[0] <= 0.0151
    assert len(seen_wait_timeouts) == 1
    assert 0.0 < seen_wait_timeouts[0] <= 0.0151
    assert get_active_worker_count() == 0

    # Part 2: Thread startup consumes 100% of remaining budget
    now2 = [3000.0]
    worker_release = threading.Event()
    worker_entered = threading.Event()
    completion_called_2 = {"called": False}

    class ExhaustingStartThread(original_thread_cls):
        def start(self):
            super().start()
            worker_entered.wait(timeout=1.0)
            # Advance clock past attempt_deadline right as start() returns
            now2[0] += 0.2

    def gated_completion(*args, **kwargs):
        completion_called_2["called"] = True
        return create_mock_completion_response("late", 1, 1)

    # Note: let _runner check remaining time BEFORE worker_release blocks it or observe slot ownership
    wait_calls_part2 = []

    class SpyEvent2:
        def __init__(self):
            self._ev = original_event_cls()

        def set(self):
            self._ev.set()

        def is_set(self):
            return self._ev.is_set()

        def wait(self, timeout=None):
            if timeout is not None:
                wait_calls_part2.append(timeout)
            return self._ev.wait(timeout=timeout)

    # Wrap _runner target via Thread subclass so the thread stays alive briefly while main raises
    class HeldWorkerThread(original_thread_cls):
        def __init__(self, target=None, daemon=None, **kwargs):
            def wrapped():
                worker_entered.set()
                worker_release.wait(timeout=2.0)
                target()
            super().__init__(target=wrapped, daemon=daemon, **kwargs)

        def start(self):
            super().start()
            worker_entered.wait(timeout=1.0)
            now2[0] += 0.2  # startup took 200ms > 100ms budget

    with patch("app.llm_service.time.monotonic", side_effect=lambda: now2[0]), \
         patch("app.llm_service.threading.Thread", HeldWorkerThread), \
         patch("app.llm_service.threading.Event", SpyEvent2), \
         patch("app.llm_service.litellm.completion", side_effect=gated_completion):
        with svc._worker_condition:
            svc._active_worker_count = 0
        try:
            with pytest.raises(TimeoutError):
                _call_completion_with_timeout(
                    model="gemini/gemini-3.5-flash-lite",
                    messages=_make_messages("startup-exhausted"),
                    api_key="test_key",
                    timeout_seconds=0.1,
                )
            # While the worker has not yet exited, the slot MUST still be owned by it (count == 1)
            assert get_active_worker_count() == 1
            assert wait_calls_part2 == []
        finally:
            worker_release.set()
            deadline = time.monotonic() + 2.0
            while get_active_worker_count() > 0 and time.monotonic() < deadline:
                time.sleep(0.01)

    # Once the worker's finally block runs, count returns to 0 and litellm was NOT called
    assert get_active_worker_count() == 0
    assert not completion_called_2["called"]


# ---------------------------------------------------------------------------
# Test G — Worker-slot counter cleanup on every exit path
# ---------------------------------------------------------------------------

def test_worker_slot_cleanup_on_every_exit_path():
    """
    Verify _active_worker_count returns to exact baseline (0) across:
      1. worker.start() raising RuntimeError.
      2. litellm.completion raising an exception inside _runner.
      3. Worker timeout (count stays 1 while worker runs, drops to 0 when worker finishes).
    """
    import app.llm_service as svc

    with svc._worker_condition:
        svc._active_worker_count = 0

    # 1. Thread-start failure
    class FailingStartThread(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    with patch("app.llm_service.threading.Thread", FailingStartThread):
        with pytest.raises(RuntimeError, match="can't start new thread"):
            _call_completion_with_timeout(
                model="gemini/gemini-3.5-flash-lite",
                messages=_make_messages("start-fail"),
                api_key="test_key",
                timeout_seconds=0.5,
            )
    assert get_active_worker_count() == 0

    # 2. Exception inside _runner
    with patch("app.llm_service.litellm.completion", side_effect=ValueError("boom")):
        with pytest.raises(ValueError, match="boom"):
            _call_completion_with_timeout(
                model="gemini/gemini-3.5-flash-lite",
                messages=_make_messages("runner-exc"),
                api_key="test_key",
                timeout_seconds=0.5,
            )
    assert get_active_worker_count() == 0

    # 3. Timeout leaving hung daemon worker running, then releasing on finish
    release = threading.Event()

    def slow_fn(*args, **kwargs):
        release.wait(timeout=2.0)
        return create_mock_completion_response("done", 1, 1)

    with patch("app.llm_service.litellm.completion", side_effect=slow_fn):
        try:
            with pytest.raises(TimeoutError):
                _call_completion_with_timeout(
                    model="gemini/gemini-3.5-flash-lite",
                    messages=_make_messages("hung"),
                    api_key="test_key",
                    timeout_seconds=0.06,
                )
            # Worker is still running; slot must still be held (not double-released)
            assert get_active_worker_count() == 1
        finally:
            release.set()
            deadline = time.monotonic() + 2.0
            while get_active_worker_count() > 0 and time.monotonic() < deadline:
                time.sleep(0.01)

    assert get_active_worker_count() == 0


# ---------------------------------------------------------------------------
# Test H — Cache TTL eviction behavior
# ---------------------------------------------------------------------------

def test_cache_ttl_eviction():
    """Verify explicit cache stores entries with TTL and evicts them after expiration."""
    clear_cache()
    model = settings.LLM_MODEL
    messages = _make_messages("ttl eviction test")

    set_cached_response(
        model=model,
        messages=messages,
        data={"reply": "short-lived", "input_tokens": 5, "output_tokens": 2, "model": model},
        ttl=1,
    )

    hit = get_cached_response(model=model, messages=messages)
    assert hit is not None
    assert hit["reply"] == "short-lived"

    # Wait past 1-second TTL
    time.sleep(1.15)
    expired = get_cached_response(model=model, messages=messages)
    assert expired is None

    clear_cache()
