from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

IGNORED_QUERY = {"fbclid", "gclid", "msclkid"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".pdf", ".zip", ".gz", ".7z", ".exe", ".dmg", ".mp4", ".mp3", ".woff", ".woff2", ".css", ".js", ".xml", ".json"}


def normalize_url(url: str, base: str = "", keep_query: bool = False) -> str | None:
    try:
        p = urlsplit(urljoin(base, url.strip()))
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return None
        host = p.hostname.encode("idna").decode("ascii").lower()
        if ":" in host:
            host = f"[{host}]"
        if p.port and not ((p.scheme == "https" and p.port == 443) or (p.scheme == "http" and p.port == 80)):
            host += f":{p.port}"
        query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                                 if not k.lower().startswith("utm_") and k.lower() not in IGNORED_QUERY)) if keep_query else ""
        return urlunsplit((p.scheme.lower(), host, p.path or "/", query, ""))
    except (ValueError, UnicodeError):
        return None


def filename_for(url: str) -> str:
    p = urlsplit(url)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", p.path.strip("/") or "index").strip("-")[:90] or "page"
    return f"{slug}-{hashlib.sha256(url.encode()).hexdigest()[:16]}.md"


@dataclass
class Page:
    url: str
    title: str
    markdown: str
    links: list[str]
    digest: str
    words: int


class DocumentationConverter(MarkdownConverter):
    def convert_pre(self, el, text, parent_tags):
        code = el.find("code")
        classes = list(el.get("class", [])) + (list(code.get("class", [])) if code else [])
        language = next((c.split("-", 1)[1] for c in classes if c.startswith(("language-", "lang-"))), "")
        raw = (code or el).get_text().rstrip("\n")
        runs = re.findall(r"`+", raw)
        fence = "`" * max(3, max((len(r) + 1 for r in runs), default=3))
        return f"\n\n{fence}{language}\n{raw}\n{fence}\n\n"


def extract_page(html: str, url: str, selector: str = "", keep_query: bool = False) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    base = urljoin(url, base_tag["href"]) if base_tag else url
    links = sorted({u for a in soup.find_all("a", href=True)
                    if (u := normalize_url(a["href"], base, keep_query))})
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if selector:
        root = soup.select_one(selector)
        if root is None:
            raise ValueError(f"Inhaltsselektor nicht gefunden: {selector}")
    else:
        # Prefer documentation body over outer layouts containing sidebars.
        root = next((r for sel in (".theme-doc-markdown", ".md-content__inner", "[itemprop='articleBody']", ".rst-content .document", "article", "main", "[role='main']")
                     if (r := soup.select_one(sel)) is not None), soup.body or soup)
    h1 = root.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    title = title or urlsplit(url).path or "Dokumentation"
    for el in root.select("script, style, nav, aside, footer, form, noscript, .headerlink, .anchor, .toc, .table-of-contents, .pagination-nav, .edit-page-link, button"):
        el.decompose()
    for a in root.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("#"):
            a["href"] = urljoin(base, href)
    for img in root.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src:
            img["src"] = urljoin(base, src)
    markdown = DocumentationConverter(heading_style="ATX", bullets="-", escape_underscores=False).convert(str(root)).strip()
    if not markdown:
        raise ValueError("Kein Textinhalt gefunden; gegebenenfalls Browser-Modus aktivieren.")
    digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    return Page(url, title, markdown, links, digest, len(markdown.split()))
