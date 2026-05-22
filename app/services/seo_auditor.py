from bs4 import BeautifulSoup
from app.models.blog_post import BlogPost


class SeoAuditor:
    def audit_post(self, post: BlogPost) -> dict:
        content = post.content_html or ""
        soup = BeautifulSoup(content, "lxml")
        text = soup.get_text(" ", strip=True)
        words = [w for w in text.split() if w]
        word_count = len(words)
        title = post.title or ""
        keyword = post.focus_keyword or ""

        issues = []
        score = 0

        # Word count (20 pts)
        if word_count >= 1500:
            score += 20
        elif word_count >= 800:
            score += 12
            issues.append(f"Content only {word_count} words — aim for 1500+")
        else:
            issues.append(f"Content too short ({word_count} words) — aim for 1500+")

        # Title length (15 pts)
        title_len = len(title)
        if 50 <= title_len <= 60:
            score += 15
        elif 40 <= title_len <= 70:
            score += 10
            issues.append(f"Title is {title_len} chars — ideal is 50-60")
        else:
            issues.append(f"Title length {title_len} chars — ideal is 50-60")

        # Focus keyword (15 pts)
        if keyword:
            score += 15
            if keyword.lower() in title.lower():
                score += 10
            else:
                issues.append(f"Focus keyword '{keyword}' not found in title")
            if word_count > 0:
                density = text.lower().count(keyword.lower()) / word_count * 100
                if 1 <= density <= 3:
                    score += 5
                elif density < 1:
                    issues.append(f"Keyword density {density:.1f}% — too low, use keyword more naturally")
                else:
                    issues.append(f"Keyword density {density:.1f}% — too high, may look spammy")
        else:
            issues.append("No focus keyword set")

        # Headings (15 pts)
        h2s = soup.find_all("h2")
        h3s = soup.find_all("h3")
        if len(h2s) >= 3:
            score += 15
        elif len(h2s) >= 1:
            score += 8
            issues.append(f"Only {len(h2s)} H2 heading(s) — aim for 3+")
        else:
            issues.append("No H2 headings — structure content with section headings")

        # Images (10 pts)
        images = soup.find_all("img")
        missing_alt = [img for img in images if not img.get("alt")]
        if images:
            if not missing_alt:
                score += 10
            else:
                score += 5
                issues.append(f"{len(missing_alt)} image(s) missing alt text")
        else:
            issues.append("No images in content — add relevant images")

        # Featured image (5 pts)
        if post.featured_image_url:
            score += 5
        else:
            issues.append("No featured image")

        return {
            "post_id": post.id,
            "title": title,
            "url": post.platform_url,
            "focus_keyword": keyword,
            "word_count": word_count,
            "title_length": title_len,
            "h2_count": len(h2s),
            "h3_count": len(h3s),
            "image_count": len(images),
            "images_missing_alt": len(missing_alt),
            "score": score,
            "grade": "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "F",
            "issues": issues,
        }
