import os

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["LITELLM_OFFLINE_MODE"] = "true"
os.environ["LITELLM_TELEMETRY"] = "False"

import logging
from typing import Any, Dict, List, Optional
import litellm
from litellm.caching.caching import Cache

litellm.telemetry = False

logger = logging.getLogger(__name__)

_litellm_cache: Optional[Cache] = None

def init_cache() -> Cache:
    """Initialize LiteLLM built-in cache."""
    global _litellm_cache
    if _litellm_cache is None:
        try:
            # LiteLLM built-in local cache
            _litellm_cache = Cache(type="local")
            litellm.cache = _litellm_cache
            logger.info("LiteLLM local cache initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize LiteLLM cache: {e}")
            raise
    return _litellm_cache

def is_cache_enabled() -> bool:
    """Check if LiteLLM cache is configured and ready."""
    global _litellm_cache
    return _litellm_cache is not None

def get_cached_response(model: str, messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Retrieve response from LiteLLM cache using model and messages."""
    global _litellm_cache
    if _litellm_cache is None:
        return None
    try:
        val = _litellm_cache.get_cache(model=model, messages=messages)
        if val is not None and isinstance(val, dict):
            return val
    except Exception as e:
        logger.warning(f"Error querying LiteLLM cache: {e}")
    return None

def set_cached_response(
    model: str,
    messages: List[Dict[str, str]],
    data: Dict[str, Any],
    ttl: int = 86400
) -> None:
    """Store response in LiteLLM cache using model and messages."""
    global _litellm_cache
    if _litellm_cache is None:
        return
    try:
        _litellm_cache.add_cache(data, model=model, messages=messages, ttl=ttl)
    except Exception as e:
        logger.warning(f"Error storing in LiteLLM cache: {e}")

def clear_cache() -> None:
    """Clear cached entries for testing."""
    global _litellm_cache
    if _litellm_cache is not None and hasattr(_litellm_cache, "cache"):
        try:
            if hasattr(_litellm_cache.cache, "cache_dict"):
                _litellm_cache.cache.cache_dict.clear()
            elif hasattr(_litellm_cache.cache, "clear"):
                _litellm_cache.cache.clear()
        except Exception:
            pass
