import os
from typing import List
from dotenv import load_dotenv

load_dotenv()

class Settings:
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:////data/app.db")
    ALLOWED_ORIGINS: str = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"
    )
    ENABLE_FAILURE_SIMULATION: bool = os.getenv(
        "ENABLE_FAILURE_SIMULATION", "true"
    ).lower() in ("true", "1", "yes")

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
