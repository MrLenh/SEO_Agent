"""Shared utilities for all SEO agents."""
import time
from openai import OpenAI
from app.config import settings

# Default model IDs per agent type when OPENROUTER is configured
_MODELS_OPENROUTER = {
    "research":  "google/gemini-2.0-flash-001",
    "planning":  "google/gemini-2.0-flash-001",
    "audit":     "deepseek/deepseek-r1",
    "learning":  "anthropic/claude-haiku-4-5",
    "copywrite": "anthropic/claude-sonnet-4-5",
}

# Default model IDs per agent type when using OpenAI directly
_MODELS_OPENAI = {
    "research":  "gpt-4o-mini",
    "planning":  "gpt-4o-mini",
    "audit":     "gpt-4o",
    "learning":  "gpt-4o",
    "copywrite": "gpt-4o",
}


def get_client(force_openai: bool = False) -> OpenAI:
    """Return an OpenAI-compatible client.

    Routes through OpenRouter when OPENROUTER_API_KEY is set, giving access
    to 300+ models at lower cost with automatic fallback routing.
    Pass force_openai=True for services that require OpenAI-native APIs
    (embeddings, image generation) which OpenRouter does not support.
    """
    if settings.OPENROUTER_API_KEY and not force_openai:
        return OpenAI(
            api_key=settings.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": settings.APP_URL or "https://seo-agent.app",
                "X-Title": "SEO Agent",
            },
        )
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def get_model(agent_type: str) -> str:
    """Return appropriate model ID for an agent type.

    Priority: env overrides (OPENAI_MODEL_FAST/SMART) > provider defaults.
    When OPENROUTER_API_KEY is set, provider defaults use cheaper/specialized
    models (DeepSeek, Gemini, Claude) instead of the full gpt-4o stack.
    """
    fast  = settings.OPENAI_MODEL_FAST  or ""
    smart = settings.OPENAI_MODEL_SMART or ""
    mid   = settings.OPENAI_MODEL or ""

    explicit = {
        "research": fast, "planning": fast,
        "audit": mid,
        "learning": smart, "copywrite": smart,
    }.get(agent_type, mid)

    if explicit:
        return explicit

    defaults = _MODELS_OPENROUTER if settings.OPENROUTER_API_KEY else _MODELS_OPENAI
    return defaults.get(agent_type, mid or "gpt-4o")


def uses_claude(model: str) -> bool:
    """True when the model is an Anthropic Claude model (OpenRouter or direct)."""
    return "anthropic/claude" in model or model.startswith("claude-")


def build_messages(system: str, user: str, model: str = "") -> list[dict]:
    """Build a chat messages list with optional prompt caching for Claude models.

    For Claude models (via OpenRouter), the system prompt is tagged with
    cache_control so Anthropic caches it for 5 minutes — reducing costs by
    up to 90% on repeated calls with the same system prompt (e.g. write →
    expand → rewrite within one pipeline run).
    """
    msgs: list[dict] = []
    if system and system.strip():
        if uses_claude(model):
            msgs.append({
                "role": "system",
                "content": [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ],
            })
        else:
            msgs.append({"role": "system", "content": system})
    if user and user.strip():
        msgs.append({"role": "user", "content": user})
    return msgs


class Timer:
    def __init__(self):
        self._t = time.time()

    def ms(self) -> int:
        return int((time.time() - self._t) * 1000)
