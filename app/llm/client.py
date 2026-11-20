"""One small client for both free model providers.

Groq (free tier) and Ollama (local) both offer an OpenAI-compatible API, so
the `openai` package is used only as an HTTP client pointed at their URLs.
No paid API is used.
"""
import time
from dataclasses import dataclass

import openai
from openai import OpenAI

from app.config import GROQ_BASE_URL, settings


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    token_source: str  # "provider" = reported by the API, "estimate" = chars / 4
    latency_ms: int


def make_client() -> OpenAI:
    if settings.llm_provider == "groq":
        if not settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is empty. Add your key to the .env file and restart the app.")
        return OpenAI(api_key=settings.groq_api_key, base_url=GROQ_BASE_URL, timeout=60, max_retries=0)
    if settings.llm_provider == "ollama":
        return OpenAI(api_key="ollama", base_url=settings.ollama_base_url, timeout=300, max_retries=0)
    raise LLMError(f"Unknown LLM_PROVIDER '{settings.llm_provider}'. Use 'groq' or 'ollama'.")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def chat(messages: list[dict], temperature: float = 0.2, json_mode: bool = True) -> LLMResult:
    client = make_client()
    request = {"model": settings.model_name, "messages": messages, "temperature": temperature}
    if json_mode:
        request["response_format"] = {"type": "json_object"}

    started = time.perf_counter()
    try:
        response = client.chat.completions.create(**request)
    except openai.RateLimitError:
        raise LLMError("Rate limit reached on the free tier. Wait a minute and try again.")
    except openai.AuthenticationError:
        raise LLMError("The API key was rejected. Check GROQ_API_KEY in .env.")
    except openai.APIConnectionError as exc:
        raise LLMError(f"Could not reach the model provider: {exc}")
    except openai.APIError as exc:
        raise LLMError(f"Model API error: {exc}")
    latency_ms = round((time.perf_counter() - started) * 1000)

    text = response.choices[0].message.content or ""
    usage = response.usage
    if usage is not None and usage.prompt_tokens is not None:
        prompt_tokens = usage.prompt_tokens
        completion_tokens = usage.completion_tokens or 0
        token_source = "provider"
    else:
        prompt_tokens = estimate_tokens("".join(m["content"] for m in messages))
        completion_tokens = estimate_tokens(text)
        token_source = "estimate"

    return LLMResult(
        text=text,
        provider=settings.llm_provider,
        model=response.model or settings.model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        token_source=token_source,
        latency_ms=latency_ms,
    )
