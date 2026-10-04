from __future__ import annotations
import argparse
import asyncio
import json
import multiprocessing
import signal
from .engine import Config, CrawlEngine


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="DocHarbor – Dokumentation als Markdown archivieren")
    parser.add_argument("url")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--scope", choices=["path", "domain", "page"], default="path")
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--workers", type=int, default=0, help="0 = alle CPU-Kerne")
    parser.add_argument("--max-pages", type=int, default=2000)
    parser.add_argument("--max-depth", type=int, default=30)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--selector", default="")
    parser.add_argument("--exclude", default="")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--keep-query", action="store_true")
    parser.add_argument("--no-sitemap", action="store_true")
    parser.add_argument("--ignore-robots", action="store_true")
    args = parser.parse_args()
    config = Config(url=args.url, output=args.output, scope=args.scope, concurrency=args.concurrency,
        workers=args.workers, max_pages=args.max_pages, max_depth=args.max_depth, delay=args.delay,
        selector=args.selector, exclude=args.exclude, browser=args.browser, keep_query=args.keep_query,
        sitemap=not args.no_sitemap, robots=not args.ignore_robots)
    try:
        engine = CrawlEngine(config, lambda e: print(json.dumps(e, ensure_ascii=False), flush=True) if e["kind"] != "stats" else None)
        signal.signal(signal.SIGINT, lambda *_: engine.stop.set())
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, lambda *_: engine.stop.set())
        asyncio.run(engine.run())
        errors = sum(r["status"] == "error" for r in engine.records.values())
        return 2 if errors else 0
    except Exception as e:
        print(f"Fehler: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
