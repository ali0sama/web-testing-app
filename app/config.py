"""Loads settings from the .env file and defines project paths.

Everything that may change between machines or websites lives in .env,
so switching to the instructor's website only means editing TARGET_URL.
"""
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
DB_PATH = DATA_DIR / "app.db"
MODEL_LOG_PATH = DATA_DIR / "model_usage_log.jsonl"
STATIC_DIR = PROJECT_ROOT / "static"
WORKER_PATH = PROJECT_ROOT / "runner" / "worker.py"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def host_of(url: str) -> str:
    """Return the lower-case host name of a URL ('' if there is none)."""
    return (urlparse(url).hostname or "").lower()


def parse_host_list(text: str) -> list[str]:
    """Turn 'a.com, b.com' into ['a.com', 'b.com']."""
    hosts = []
    for part in text.split(","):
        part = part.strip().lower()
        if part:
            hosts.append(part)
    return hosts


class Settings:
    def __init__(self):
        self.llm_provider = os.getenv("LLM_PROVIDER", "groq").strip().lower()
        self.groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
        self.groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").strip()
        self.ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip()

        self.target_url = os.getenv("TARGET_URL", "https://the-internet.herokuapp.com/login").strip()
        extra_hosts = parse_host_list(os.getenv("EXTRA_ALLOWED_HOSTS", ""))
        allowed = set(extra_hosts)
        if host_of(self.target_url):
            allowed.add(host_of(self.target_url))
        self.allowed_hosts = sorted(allowed)

        self.runner_timeout = int(os.getenv("RUNNER_TIMEOUT_SECONDS", "90"))

    @property
    def model_name(self) -> str:
        return self.groq_model if self.llm_provider == "groq" else self.ollama_model

    def is_url_allowed(self, url: str) -> bool:
        """Only http(s) URLs whose host is on the allowlist may be opened."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        return (parsed.hostname or "").lower() in self.allowed_hosts


settings = Settings()
