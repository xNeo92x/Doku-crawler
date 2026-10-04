from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlsplit, urljoin
from urllib.robotparser import RobotFileParser
import xml.etree.ElementTree as ET

import aiohttp

from .content import ASSET_SUFFIXES, Page, extract_page, filename_for, normalize_url

USER_AGENT = "DocHarbor/1.0 (+documentation archiver)"


def cpu_count() -> int:
    # Windows ProcessPoolExecutor has a documented maximum of 61 workers.
    count = os.cpu_count() or 1
    try:
        count = len(os.sched_getaffinity(0))
    except AttributeError:
        pass
    return min(count, 61) if os.name == "nt" else count


@dataclass
class Config:
    url: str
    output: str
    scope: str = "path"  # path, domain, page
    concurrency: int = 12
    workers: int = 0  # 0 = all available CPU cores
    max_pages: int = 2000
    max_depth: int = 30
    delay: float = 0.15  # minimum spacing between request starts per origin
    timeout: float = 30
    max_bytes: int = 10 * 1024 * 1024
    robots: bool = True
    sitemap: bool = True
    keep_query: bool = False
    selector: str = ""
    exclude: str = ""
    browser: bool = False
    resume: bool = True
    retries: int = 2

    def validate(self):
        url = normalize_url(self.url, keep_query=self.keep_query)
        if not url:
            raise ValueError("Bitte eine gültige HTTP-/HTTPS-Adresse ohne Zugangsdaten angeben.")
        self.url = url
        if self.scope not in {"path", "domain", "page"}:
            raise ValueError("Unbekannter Crawl-Bereich.")
        if not (1 <= self.concurrency <= 128 and 1 <= self.max_pages <= 1_000_000 and 0 <= self.max_depth <= 1000):
            raise ValueError("Ungültige Grenzen für Parallelität, Seiten oder Tiefe.")
        if not (0 <= self.workers <= cpu_count() and 0 <= self.delay <= 60 and 1 <= self.timeout <= 600 and self.max_bytes > 0):
            raise ValueError("Ungültige CPU-, Zeit- oder Größenlimits.")
        if self.exclude:
            re.compile(self.exclude)
        if self.selector:
            from bs4 import BeautifulSoup
            BeautifulSoup("<html></html>", "html.parser").select(self.selector)
        if not self.output.strip():
            raise ValueError("Bitte einen Ausgabeordner wählen.")


class CrawlEngine:
    def __init__(self, config: Config, emit=lambda event: None):
        config.validate()
        self.c = config
        self.emit = emit
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.output = Path(config.output).expanduser().resolve()
        self.pending: deque[tuple[str, int]] = deque()
        self.seen: set[str] = set()
        self.records: dict[str, dict] = {}
        self.active: dict[asyncio.Task, tuple[str, int]] = {}
        self.rules: dict[str, RobotFileParser] = {}
        self.gates: dict[str, asyncio.Lock] = {}
        self.next_request: dict[str, float] = {}
        self.sitemap_urls: set[str] = set()
        self.bytes = 0
        self.started = time.monotonic()
        self.browser_instance = None
        self.browser_context = None
        self.exclude = re.compile(config.exclude) if config.exclude else None
        p = urlsplit(config.url)
        self.netloc = p.netloc
        self.prefix = p.path if p.path.endswith("/") else p.path.rsplit("/", 1)[0] + "/"
        if p.path == "/":
            self.prefix = "/"
        self.pool = None

    def allowed(self, url: str) -> bool:
        p = urlsplit(url)
        if p.netloc != self.netloc or p.scheme not in {"http", "https"}:
            return False
        if self.c.scope == "page" and url != self.c.url:
            return False
        if self.c.scope == "path" and not (p.path.startswith(self.prefix) or url == self.c.url):
            return False
        if self.exclude and self.exclude.search(url):
            return False
        return Path(p.path.lower()).suffix not in ASSET_SUFFIXES

    def enqueue(self, url: str, depth: int):
        url = normalize_url(url, keep_query=self.c.keep_query)
        if url and url not in self.seen and depth <= self.c.max_depth and self.allowed(url):
            # Bound frontier too: generated calendars/query variants cannot grow indefinitely.
            if len(self.seen) >= self.c.max_pages * 10:
                return
            self.seen.add(url)
            self.pending.append((url, depth))

    def event(self, kind: str, **data):
        self.emit({"kind": kind, **data})

    def stats(self):
        self.event("stats", saved=sum(r["status"] == "saved" for r in self.records.values()),
                   errors=sum(r["status"] == "error" for r in self.records.values()),
                   skipped=sum(r["status"] == "skipped" for r in self.records.values()),
                   queued=len(self.pending), active=len(self.active), bytes=self.bytes,
                   elapsed=time.monotonic() - self.started)

    def identity(self):
        return {k: getattr(self.c, k) for k in ("url", "scope", "selector", "keep_query", "exclude", "browser")}

    def initialize(self):
        self.output.mkdir(parents=True, exist_ok=True)
        (self.output / "pages").mkdir(exist_ok=True)
        state_file = self.output / "crawl-state.json"
        if state_file.exists():
            if not self.c.resume:
                raise ValueError("Dieser Ordner enthält bereits ein Archiv. Wiederaufnahme aktivieren oder einen neuen Ordner wählen.")
            state = json.loads(state_file.read_text(encoding="utf-8"))
            if state.get("identity") != self.identity():
                raise ValueError("Dieser Ordner gehört zu einem anderen Crawl-Profil. Bitte einen neuen Ordner wählen.")
            self.records = state["records"]
            # Keep completed pages and blocked/non-HTML skips. Retry previous errors.
            retry = [(u, r.get("depth", 0)) for u, r in self.records.items() if r["status"] == "error"
                     or (r["status"] == "saved" and not (self.output / "pages" / r["file"]).is_file())]
            for u, _ in retry:
                self.records.pop(u, None)
            self.seen = set(self.records)
            for u, depth in state.get("pending", []) + retry:
                self.enqueue(u, depth)
            for r in self.records.values():
                if r["status"] == "saved":
                    for link in r.get("links", []):
                        self.enqueue(link, r.get("depth", 0) + 1)
        else:
            # Never silently mix arbitrary existing files into a new archive.
            if (self.output / "archive.md").exists():
                raise ValueError("archive.md existiert bereits. Bitte einen anderen Ausgabeordner wählen.")
        if not self.records and not self.pending:
            self.enqueue(self.c.url, 0)

    @staticmethod
    def atomic_write(path: Path, text: str):
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)

    def checkpoint(self):
        pending = list(self.pending) + list(self.active.values())
        self.atomic_write(self.output / "crawl-state.json", json.dumps(
            {"version": 1, "identity": self.identity(), "config": asdict(self.c), "records": self.records, "pending": pending},
            ensure_ascii=False, indent=2))

    async def throttle(self, origin: str):
        gate = self.gates.setdefault(origin, asyncio.Lock())
        async with gate:
            wait = self.next_request.get(origin, 0) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self.next_request[origin] = time.monotonic() + self.c.delay

    async def request(self, session, url: str, limit: int, check_scope: bool = True, check_robots: bool = True):
        current = url
        for _ in range(10):
            p = urlsplit(current)
            origin = f"{p.scheme}://{p.netloc}"
            if check_scope and self.c.scope != "page" and not self.allowed(current):
                raise Skip("Weiterleitung außerhalb des Crawl-Bereichs")
            if check_robots and self.c.robots:
                rules = self.rules.get(origin)
                if rules and not rules.can_fetch(USER_AGENT, current):
                    raise Skip("Durch robots.txt ausgeschlossen")
            await self.throttle(origin)
            async with session.get(current, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    current = normalize_url(urljoin(current, response.headers.get("Location", "")), keep_query=True)
                    if not current or urlsplit(current).netloc != self.netloc:
                        raise Skip("Weiterleitung auf andere Domain")
                    continue
                if response.status in {429, 503}:
                    try:
                        wait = min(60.0, max(1.0, float(response.headers.get("Retry-After", "2"))))
                    except ValueError:
                        wait = 2.0
                    raise RetryHTTP(response.status, wait)
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    if len(body) + len(chunk) > limit:
                        raise Skip("Antwort überschreitet Größenlimit")
                    body.extend(chunk)
                self.bytes += len(body)
                charset = response.charset or "utf-8"
                try:
                    text = body.decode(charset, errors="replace")
                except LookupError:
                    text = body.decode("utf-8", errors="replace")
                return current, response.headers.get("Content-Type", "").lower(), text
        raise Skip("Zu viele Weiterleitungen")

    async def load_robots(self, session):
        # HTTP and HTTPS have independent policies. Missing (404) allows crawling;
        # auth errors and temporary/unreachable policies conservatively disallow it.
        for scheme in {urlsplit(self.c.url).scheme, "https", "http"}:
            origin = f"{scheme}://{self.netloc}"
            rp = RobotFileParser(origin + "/robots.txt")
            try:
                _, _, text = await self.request(session, origin + "/robots.txt", 1024 * 1024, False, False)
                rp.parse(text.splitlines())
                self.sitemap_urls.update(rp.site_maps() or [])
                crawl_delay = rp.crawl_delay(USER_AGENT)
                if crawl_delay is not None:
                    self.c.delay = max(self.c.delay, crawl_delay)
            except aiohttp.ClientResponseError as e:
                rp.parse([] if e.status == 404 else ["User-agent: *", "Disallow: /"])
            except Exception as e:
                rp.parse(["User-agent: *", "Disallow: /"])
                self.event("log", message=f"robots.txt nicht verfügbar ({origin}): {e}")
            self.rules[origin] = rp

    async def load_sitemaps(self, session):
        origin = f"{urlsplit(self.c.url).scheme}://{self.netloc}"
        todo = deque(sorted(self.sitemap_urls) or [origin + "/sitemap.xml"])
        seen = set()
        while todo and len(seen) < 32 and not self.stop.is_set():
            while self.pause.is_set() and not self.stop.is_set():
                await asyncio.sleep(0.15)
            url = normalize_url(todo.popleft(), keep_query=True)
            if not url or urlsplit(url).netloc != self.netloc or url in seen:
                continue
            seen.add(url)
            try:
                _, _, text = await self.request(session, url, 5 * 1024 * 1024, False)
                root = ET.fromstring(text)
                locations = [el.text.strip() for el in root.iter() if el.tag.split("}")[-1] == "loc" and el.text]
                if root.tag.split("}")[-1] == "sitemapindex":
                    todo.extend(locations)
                else:
                    for link in locations:
                        self.enqueue(link, 1)
            except Exception as e:
                self.event("log", message=f"Sitemap übersprungen: {url} ({e})")

    async def render_browser(self, url: str, html: str):
        page = await self.browser_context.new_page()
        try:
            # Route main document to already fetched, scope/robots checked HTML.
            # Avoid an unchecked second navigation/redirect; permit same-origin assets
            # needed for hydration, but block subsequent document navigations.
            served = False
            async def route_handler(route):
                nonlocal served
                req = route.request
                target = normalize_url(req.url, keep_query=True)
                if req.is_navigation_request():
                    if not served and req.frame == page.main_frame and target == normalize_url(url, keep_query=True):
                        served = True
                        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
                    else:
                        await route.abort()
                elif target and urlsplit(target).netloc == self.netloc and req.resource_type not in {"image", "media", "font"}:
                    await route.continue_()
                else:
                    await route.abort()
            await page.route("**/*", route_handler)
            await page.goto(url, wait_until="domcontentloaded", timeout=int(self.c.timeout * 1000))
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            if self.c.selector:
                await page.wait_for_selector(self.c.selector, timeout=int(self.c.timeout * 1000))
            text = await page.content()
            if len(text.encode()) > self.c.max_bytes:
                raise Skip("Gerenderte Seite überschreitet Größenlimit")
            return text
        finally:
            await page.close()

    async def process(self, session, url, depth):
        self.event("page", url=url, status="Lädt …")
        for attempt in range(self.c.retries + 1):
            try:
                final, content_type, html = await self.request(session, url, self.c.max_bytes)
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise Skip("Kein HTML-Dokument")
                if self.c.browser:
                    html = await self.render_browser(final, html)
                page = await asyncio.get_running_loop().run_in_executor(self.pool, extract_page, html, final, self.c.selector, self.c.keep_query)
                return page
            except Skip:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, RetryHTTP) as e:
                if isinstance(e, aiohttp.ClientResponseError) and e.status < 500 and e.status != 429:
                    raise
                if attempt >= self.c.retries:
                    raise
                await asyncio.sleep(e.wait if isinstance(e, RetryHTTP) else 2 ** attempt)
                self.event("log", message=f"Neuer Versuch: {url} ({attempt + 1}/{self.c.retries})")

    def save_page(self, requested: str, depth: int, page: Page):
        duplicate = next((r for r in self.records.values() if r.get("digest") == page.digest and r["status"] == "saved"), None)
        filename = duplicate["file"] if duplicate else filename_for(requested)
        if not duplicate:
            now = datetime.now(timezone.utc).isoformat()
            metadata = f"<!-- DocHarbor | source: {page.url} | fetched: {now} -->\n\n"
            self.atomic_write(self.output / "pages" / filename, metadata + page.markdown + "\n")
        self.records[requested] = {"status": "saved", "depth": depth, "url": page.url, "title": page.title,
                                   "file": filename, "digest": page.digest, "words": page.words, "links": page.links,
                                   "duplicate": bool(duplicate)}
        for link in page.links:
            self.enqueue(link, depth + 1)
        self.event("page", url=requested, status="Duplikat" if duplicate else "Gespeichert", title=page.title, file=str(self.output / "pages" / filename))

    def exports(self):
        records = sorted((r for r in self.records.values() if r["status"] == "saved"), key=lambda r: (r["title"].casefold(), r["url"]))
        by_url = {u: r["file"] for u, r in self.records.items() if r["status"] == "saved"}
        by_url.update({r["url"]: r["file"] for r in records})
        unique = {r["file"]: r for r in records}
        anchors = {name: "page-" + hashlib.sha256(name.encode()).hexdigest()[:12] for name in unique}
        def label(text):
            return re.sub(r"[\[\]\\\n\r]", " ", text)
        def rewrite(text, combined=False):
            # Rewrite absolute Markdown links to archived pages; preserve images and
            # fragments in individual pages. In combined output target page anchor.
            def sub(m):
                raw = m.group(2)
                p = urlsplit(raw)
                normalized = normalize_url(raw, keep_query=self.c.keep_query)
                name = by_url.get(normalized)
                if not name:
                    return m.group(0)
                dest = "#" + anchors[name] if combined else name + ("#" + p.fragment if p.fragment else "")
                return m.group(1) + dest + m.group(3)
            pieces = []
            fence = None
            for line in text.splitlines(keepends=True):
                marker = re.match(r"^\s*(`{3,}|~{3,})", line)
                if marker:
                    run = marker.group(1)
                    if fence is None:
                        fence = run
                    elif run[0] == fence[0] and len(run) >= len(fence):
                        fence = None
                    pieces.append(line)
                elif fence:
                    pieces.append(line)
                else:
                    pieces.append(re.sub(r"(?<!!)(\[[^\]\n]*\]\()(https?://[^\s)]+)(\))", sub, line))
            return "".join(pieces)
        index = ["# DocHarbor – Dokumentationsarchiv", "", f"Startadresse: <{self.c.url}>", "", "## Seiten", ""]
        for r in unique.values():
            index.append(f"- [{label(r['title'])}](offline/{r['file']})")
        self.atomic_write(self.output / "index.md", "\n".join(index) + "\n")
        archive_path = self.output / "archive.md"
        temp = archive_path.with_suffix(".md.tmp")
        with temp.open("w", encoding="utf-8") as out:
            out.write("# Dokumentationsarchiv\n\n" + f"Quelle: <{self.c.url}>\n\n## Inhaltsverzeichnis\n\n")
            for name, r in unique.items():
                out.write(f"- [{label(r['title'])}](#{anchors[name]})\n")
            for name, r in unique.items():
                path = self.output / "pages" / name
                text = path.read_text(encoding="utf-8")
                out.write(f"\n\n---\n\n<a id=\"{anchors[name]}\"></a>\n\n## {label(r['title'])}\n\nQuelle: <{r['url']}>\n\n")
                out.write(rewrite(text, True))
                # URL rewriting is only a final export transform; raw text is retained
                # for stable hashes and restartability. Offline pages are a separate view.
        os.replace(temp, archive_path)
        offline = self.output / "offline"
        offline.mkdir(exist_ok=True)
        for name in unique:
            self.atomic_write(offline / name, rewrite((self.output / "pages" / name).read_text(encoding="utf-8")))
        self.atomic_write(self.output / "manifest.json", json.dumps({"source": self.c.url, "pages": self.records}, ensure_ascii=False, indent=2))

    async def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        lock = (self.output / ".docharbor.lock").open("a+b")
        try:
            try:
                if os.name == "nt":
                    import msvcrt
                    lock.seek(0)
                    if not lock.read(1):
                        lock.write(b"0")
                        lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as e:
                raise RuntimeError("Ein anderer Crawl verwendet diesen Ausgabeordner bereits.") from e
            await self._run()
        finally:
            lock.close()  # OS releases the lock, even after a process crash.

    async def _run(self):
        self.initialize()
        self.event("log", message=f"CPU-Prozesse: {self.c.workers or cpu_count()} · Downloads: {self.c.concurrency}")
        self.pool = ProcessPoolExecutor(max_workers=self.c.workers or cpu_count(), mp_context=multiprocessing.get_context("spawn"))
        playwright = None
        failed = False
        try:
            if self.c.browser:
                try:
                    from playwright.async_api import async_playwright
                    playwright = await async_playwright().start()
                    self.browser_instance = await playwright.chromium.launch(headless=True)
                    self.browser_context = await self.browser_instance.new_context(user_agent=USER_AGENT, service_workers="block")
                except Exception as e:
                    raise RuntimeError("Browser-Modus benötigt Playwright und Chromium. Siehe README. " + str(e)) from e
            timeout = aiohttp.ClientTimeout(total=self.c.timeout)
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}, connector=aiohttp.TCPConnector(limit=self.c.concurrency)) as session:
                if self.c.robots:
                    await self.load_robots(session)
                if self.c.sitemap and self.c.scope != "page":
                    await self.load_sitemaps(session)
                self.checkpoint()
                last_checkpoint = time.monotonic()
                limit = min(self.c.concurrency, 4) if self.c.browser else self.c.concurrency
                while (self.pending or self.active) and not self.stop.is_set():
                    while self.pending and len(self.active) < limit and not self.pause.is_set() and len(self.records) + len(self.active) < self.c.max_pages:
                        url, depth = self.pending.popleft()
                        task = asyncio.create_task(self.process(session, url, depth))
                        self.active[task] = (url, depth)
                    if not self.active:
                        if len(self.records) >= self.c.max_pages:
                            break
                        self.stats()
                        await asyncio.sleep(0.15)
                        continue
                    done, _ = await asyncio.wait(self.active, timeout=0.2, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        url, depth = self.active.pop(task)
                        try:
                            self.save_page(url, depth, task.result())
                        except Exception as e:
                            status = "skipped" if isinstance(e, Skip) else "error"
                            self.records[url] = {"status": status, "depth": depth, "message": str(e)}
                            self.event("page", url=url, status="Übersprungen" if status == "skipped" else "Fehler", message=str(e))
                    if time.monotonic() - last_checkpoint > 2:
                        self.checkpoint()
                        last_checkpoint = time.monotonic()
                    self.stats()
        except Exception:
            failed = True
            raise
        finally:
            # Requeue in-flight URLs before cancelling, so stop and resume never lose pages.
            self.pending.extend(self.active.values())
            tasks = list(self.active)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.active.clear()
            if self.browser_instance:
                await self.browser_instance.close()
            if playwright:
                await playwright.stop()
            # Cancel queued conversion jobs; running finite conversions finish first.
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.checkpoint()
            self.exports()
            self.stats()
            if not failed:
                self.event("finished", stopped=self.stop.is_set(), limited=bool(self.pending), output=str(self.output))


class Skip(Exception):
    pass


class RetryHTTP(Exception):
    def __init__(self, status, wait):
        super().__init__(f"HTTP {status}")
        self.wait = wait
