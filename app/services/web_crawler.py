import hashlib
import re
from typing import Optional
import httpx
from bs4 import BeautifulSoup
import html2text as _h2t


class WebCrawler:
    _HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; SEOAgentBot/1.0)",
        "Accept": "text/html,application/xhtml+xml",
    }

    def fetch(self, url: str) -> dict:
        resp = httpx.get(url, headers=self._HEADERS, timeout=25.0, follow_redirects=True)
        resp.raise_for_status()
        return self._parse(url, resp.text)

    def _parse(self, url: str, html: str) -> dict:
        soup = BeautifulSoup(html, "lxml")

        for tag in soup.find_all(["nav", "footer", "script", "style", "header", "aside", "noscript"]):
            tag.decompose()

        title = ""
        if soup.find("h1"):
            title = soup.find("h1").get_text(strip=True)
        elif soup.title:
            title = soup.title.get_text(strip=True)

        main = soup.find("article") or soup.find("main") or soup.find("body") or soup

        # Plain text
        content_text = main.get_text(separator="\n", strip=True)
        content_text = re.sub(r"\n{3,}", "\n\n", content_text).strip()

        # Markdown via html2text
        h = _h2t.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0
        content_md = h.handle(str(main)).strip()
        content_md = re.sub(r"\n{3,}", "\n\n", content_md)

        checksum = hashlib.sha256(content_text.encode()).hexdigest()

        return {
            "url": url,
            "title": title,
            "content_text": content_text[:20000],
            "content_md": content_md[:20000],
            "checksum": checksum,
            "word_count": len(content_text.split()),
        }

    def crawl_blog_index(self, index_url: str) -> list[str]:
        resp = httpx.get(index_url, headers=self._HEADERS, timeout=25.0, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        parts = index_url.split("/")
        base = parts[0] + "//" + parts[2]
        urls: set[str] = set()

        for a in soup.find_all("a", href=True):
            href: str = a["href"]
            if href.startswith("/"):
                href = base + href
            elif not href.startswith("http"):
                continue
            if href.startswith(base) and href != index_url:
                path = href.replace(base, "").strip("/").split("/")
                if len(path) >= 2:
                    urls.add(href)

        return list(urls)[:50]
