"""Shared utilities for all SEO agents."""
import time
from app.config import settings


def get_model(agent_type: str) -> str:
    """Return appropriate model for each agent type."""
    fast  = settings.OPENAI_MODEL_FAST  or settings.OPENAI_MODEL or "gpt-4o-mini"
    smart = settings.OPENAI_MODEL_SMART or settings.OPENAI_MODEL or "gpt-4o"
    mid   = settings.OPENAI_MODEL or "gpt-4o"
    return {
        "research":  fast,
        "planning":  fast,
        "audit":     mid,
        "learning":  smart,
        "copywrite": smart,
    }.get(agent_type, mid)


class Timer:
    def __init__(self):
        self._t = time.time()

    def ms(self) -> int:
        return int((time.time() - self._t) * 1000)
