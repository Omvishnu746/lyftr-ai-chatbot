import os
from typing import List
from dotenv import load_dotenv

load_dotenv()

class Settings:
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini/gemini-3.5-flash-lite")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:////data/app.db")
    ALLOWED_ORIGINS: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"
    )
    ENABLE_FAILURE_SIMULATION: bool = os.getenv(
        "ENABLE_FAILURE_SIMULATION", "true"
    ).lower() in ("true", "1", "yes")

    # Timeout, retry, and history-bounding controls
    LLM_REQUEST_TIMEOUT_SECONDS: float = float(
        os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "10.0")
    )
    LLM_TOTAL_DEADLINE_SECONDS: float = float(
        os.getenv("LLM_TOTAL_DEADLINE_SECONDS", "25.0")
    )
    LLM_MAX_RETRIES_PER_KEY: int = int(
        os.getenv("LLM_MAX_RETRIES_PER_KEY", "2")
    )
    LLM_RETRY_BASE_DELAY_SECONDS: float = float(
        os.getenv("LLM_RETRY_BASE_DELAY_SECONDS", "0.5")
    )
    LLM_RETRY_MAX_DELAY_SECONDS: float = float(
        os.getenv("LLM_RETRY_MAX_DELAY_SECONDS", "4.0")
    )
    LLM_MAX_HISTORY_TURNS: int = int(
        os.getenv("LLM_MAX_HISTORY_TURNS", "10")
    )
    LLM_MAX_HISTORY_CHARS: int = int(
        os.getenv("LLM_MAX_HISTORY_CHARS", "16000")
    )
    LLM_MAX_CONCURRENT_CALLS: int = int(
        os.getenv("LLM_MAX_CONCURRENT_CALLS", "20")
    )

    @property
    def api_keys(self) -> List[str]:
        raw_keys = os.getenv("LLM_API_KEYS", "")
        if not raw_keys:
            return []
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        return keys

    @property
    def allowed_origins_list(self) -> List[str]:
        if self.ALLOWED_ORIGINS == "*":
            return ["*"]
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

settings = Settings()
