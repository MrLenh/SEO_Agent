import hashlib
from typing import Optional
from sqlalchemy.orm import Session

from app.models.knowledge_item import KnowledgeItem, KnowledgeStatus
from app.services.embedding_service import EmbeddingService
from app.services.web_crawler import WebCrawler


class KnowledgeBase:
    def __init__(self):
        self.embedder = EmbeddingService()
        self.crawler = WebCrawler()

    # ── Add items ─────────────────────────────────────────────────────────────

    def add_from_url(
        self,
        url: str,
        shop_domain: Optional[str],
        db: Session,
        source_type: str = "blog",
        auto_approve: bool = False,
    ) -> KnowledgeItem:
        data = self.crawler.fetch(url)
        existing = (
            db.query(KnowledgeItem)
            .filter_by(source_url=url, shop_domain=shop_domain)
            .first()
        )
        embedding = self.embedder.embed(data["content_text"][:6000])

        if existing:
            if existing.checksum == data["checksum"]:
                return existing
            existing.title = data["title"]
            existing.content_text = data["content_text"]
            existing.content_md = data["content_md"]
            existing.embedding = embedding
            existing.checksum = data["checksum"]
            existing.extra_meta = {"word_count": data["word_count"]}
            if auto_approve:
                existing.status = KnowledgeStatus.APPROVED
            db.commit()
            db.refresh(existing)
            return existing

        item = KnowledgeItem(
            shop_domain=shop_domain,
            source_url=url,
            source_type=source_type,
            title=data["title"],
            content_text=data["content_text"],
            content_md=data["content_md"],
            embedding=embedding,
            checksum=data["checksum"],
            status=KnowledgeStatus.APPROVED if auto_approve else KnowledgeStatus.PENDING,
            extra_meta={"word_count": data["word_count"]},
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def add_from_text(
        self,
        title: str,
        content_text: str,
        content_md: str,
        source_type: str,
        shop_domain: Optional[str],
        db: Session,
        source_url: Optional[str] = None,
        auto_approve: bool = False,
    ) -> KnowledgeItem:
        checksum = hashlib.sha256(content_text.encode()).hexdigest()
        embedding = self.embedder.embed(content_text[:6000])
        item = KnowledgeItem(
            shop_domain=shop_domain,
            source_url=source_url,
            source_type=source_type,
            title=title,
            content_text=content_text[:20000],
            content_md=content_md[:20000],
            embedding=embedding,
            checksum=checksum,
            status=KnowledgeStatus.APPROVED if auto_approve else KnowledgeStatus.PENDING,
            extra_meta={"word_count": len(content_text.split())},
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        shop_domain: Optional[str],
        db: Session,
        top_k: int = 5,
    ) -> list[dict]:
        items = (
            db.query(KnowledgeItem)
            .filter(
                KnowledgeItem.shop_domain == shop_domain,
                KnowledgeItem.status == KnowledgeStatus.APPROVED,
                KnowledgeItem.embedding.isnot(None),
            )
            .all()
        )
        if not items:
            return []

        query_vec = self.embedder.embed(query)
        scored = self.embedder.rank_by_similarity(
            query_vec,
            [
                {
                    "id": i.id,
                    "title": i.title,
                    "source_url": i.source_url,
                    "content_md": i.content_md,
                    "source_type": i.source_type,
                    "embedding": i.embedding,
                }
                for i in items
            ],
        )
        return scored[:top_k]

    def get_context_for_article(
        self,
        keyword: str,
        title: str,
        shop_domain: Optional[str],
        db: Session,
    ) -> str:
        results = self.search(f"{keyword} {title}", shop_domain, db, top_k=4)
        if not results:
            return ""

        ctx = "\n\nKnowledge Base — existing brand content (avoid duplicating; use for internal links):\n"
        for r in results:
            url_hint = f" | URL: {r['source_url']}" if r.get("source_url") else ""
            ctx += f"\n**{r['title']}**{url_hint}\n"
            if r.get("content_md"):
                ctx += r["content_md"][:400].rstrip() + "…\n"
        return ctx

    def get_existing_topics(
        self,
        shop_domain: Optional[str],
        db: Session,
        limit: int = 30,
    ) -> list[str]:
        items = (
            db.query(KnowledgeItem.title)
            .filter(
                KnowledgeItem.shop_domain == shop_domain,
                KnowledgeItem.status == KnowledgeStatus.APPROVED,
                KnowledgeItem.source_type.in_(["blog", "product"]),
            )
            .limit(limit)
            .all()
        )
        return [i.title for i in items if i.title]
