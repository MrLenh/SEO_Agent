import math
from app.agents.base import get_client

_EMBED_MODEL = "text-embedding-3-small"


class EmbeddingService:
    def __init__(self):
        # Embeddings API is OpenAI-only — OpenRouter does not support it
        self.client = get_client(force_openai=True)

    def embed(self, text: str) -> list[float]:
        text = text.replace("\n", " ")[:8000]
        resp = self.client.embeddings.create(model=_EMBED_MODEL, input=text)
        return resp.data[0].embedding

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        mag = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
        return dot / mag if mag else 0.0

    def rank_by_similarity(self, query_vec: list[float], items: list[dict]) -> list[dict]:
        results = []
        for item in items:
            emb = item.get("embedding")
            if not emb:
                continue
            score = self.cosine_similarity(query_vec, emb)
            results.append({**item, "_score": score})
        return sorted(results, key=lambda x: x["_score"], reverse=True)
