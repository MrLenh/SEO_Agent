import httpx
from app.config import settings


class RankingChecker:
    def __init__(self, shop_domain: str):
        # Store domain without protocol, e.g. "gingerglow.myshopify.com"
        self.shop_domain = shop_domain

    async def check_keyword(self, keyword: str) -> dict:
        if not settings.SERPER_API_KEY:
            return {"keyword": keyword, "position": None, "error": "SERPER_API_KEY not set"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": settings.SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": keyword, "num": 100},
            )
        data = resp.json()
        organic = data.get("organic", [])

        position = None
        ranking_url = None
        ranking_title = None
        for i, result in enumerate(organic, 1):
            link = result.get("link", "")
            if self.shop_domain in link:
                position = i
                ranking_url = link
                ranking_title = result.get("title", "")
                break

        return {
            "keyword": keyword,
            "position": position,
            "ranking_url": ranking_url,
            "ranking_title": ranking_title,
            "top3_serp": [
                {"pos": i + 1, "title": r.get("title", ""), "link": r.get("link", ""), "snippet": r.get("snippet", "")}
                for i, r in enumerate(organic[:3])
            ],
        }

    async def check_many(self, keywords: list[str]) -> list[dict]:
        import asyncio
        results = await asyncio.gather(*[self.check_keyword(kw) for kw in keywords])

        # Enrich with search volume if DataForSEO is configured
        from app.services.volume_service import get_search_volumes
        volumes = await get_search_volumes(keywords)
        for r in results:
            vol_data = volumes.get(r["keyword"], {})
            r["volume"] = vol_data.get("volume")
            r["competition"] = vol_data.get("competition")
            r["cpc"] = vol_data.get("cpc")

        return list(results)
