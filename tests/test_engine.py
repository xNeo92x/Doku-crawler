import asyncio
import json
from pathlib import Path
from aiohttp import web
from docharbor.engine import Config, CrawlEngine


def test_parallel_archive_resume_and_boundaries(tmp_path):
    async def scenario():
        active = 0
        peak = 0
        requests = []
        retry_count = 0
        async def handler(request):
            nonlocal active, peak, retry_count
            requests.append(request.path)
            if request.path == '/robots.txt':
                return web.Response(text='User-agent: *\nDisallow: /docs/private\n')
            if request.path == '/sitemap.xml':
                return web.Response(text='<urlset><url><loc>' + origin + '/docs/from-sitemap</loc></url></urlset>', content_type='application/xml')
            if request.path == '/docs/':
                links = ''.join(f'<a href="{p}">{p}</a>' for p in ['one', 'two', 'same', 'retry', 'private', 'out', 'from-sitemap', '/elsewhere/'])
                return web.Response(text='<main><h1>Start</h1>' + links + '</main>', content_type='text/html')
            if request.path == '/docs/out':
                return web.Response(status=302, headers={'Location': '/elsewhere/'})
            if request.path == '/docs/retry':
                retry_count += 1
                if retry_count == 1:
                    return web.Response(status=503, headers={'Retry-After': '1'})
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.15)
            active -= 1
            text = 'Shared' if request.path in ['/docs/one', '/docs/same'] else request.path
            return web.Response(text=f'<main><h1>{text}</h1><p>Content</p></main>', content_type='text/html')
        app = web.Application()
        app.router.add_get('/{tail:.*}', handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        try:
            config = Config(origin + '/docs/', str(tmp_path), workers=2, concurrency=4, delay=0, timeout=2)
            engine = CrawlEngine(config)
            await engine.run()
            assert peak >= 2
            assert '/docs/private' not in requests
            assert '/elsewhere/' not in requests
            assert retry_count == 2
            assert (tmp_path / 'archive.md').is_file()
            assert 'Inhaltsverzeichnis' in (tmp_path / 'archive.md').read_text()
            saved = [r for r in engine.records.values() if r['status'] == 'saved']
            assert len(saved) == 6
            assert len(list((tmp_path / 'pages').glob('*.md'))) == 5
            assert any(r['duplicate'] for r in saved)
            assert '](#page-' in (tmp_path / 'archive.md').read_text()
            count = len([p for p in requests if p.startswith('/docs/')])
            second = CrawlEngine(config)
            await second.run()
            assert len([p for p in requests if p.startswith('/docs/')]) == count
            assert second.records == engine.records
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_stop_and_resume(tmp_path):
    async def scenario():
        async def handler(request):
            if request.path == '/docs/':
                return web.Response(text='<main><h1>Start</h1><a href="slow">Next</a></main>', content_type='text/html')
            await asyncio.sleep(0.4)
            return web.Response(text='<main><h1>Slow</h1><p>Done</p></main>', content_type='text/html')
        app = web.Application()
        app.router.add_get('/{tail:.*}', handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/docs/'
        config = Config(url, str(tmp_path), workers=1, concurrency=1, delay=0, sitemap=False, robots=False)
        engine = None
        def emit(event):
            if event['kind'] == 'page' and event['url'].endswith('/slow') and event['status'] == 'Lädt …':
                engine.stop.set()
        engine = CrawlEngine(config, emit)
        try:
            await engine.run()
            state = json.loads((tmp_path / 'crawl-state.json').read_text())
            assert state['pending']
            resumed = CrawlEngine(config)
            await resumed.run()
            assert len(resumed.records) == 2
            assert not resumed.pending
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_page_limit_and_resume(tmp_path):
    async def scenario():
        async def handler(request):
            return web.Response(text=f'<main><h1>{request.path}</h1><a href="/next">Next</a></main>', content_type='text/html')
        app = web.Application()
        app.router.add_get('/{tail:.*}', handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        url = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/'
        config = Config(url, str(tmp_path), workers=1, concurrency=8, delay=0, sitemap=False, robots=False, max_pages=1)
        try:
            first = CrawlEngine(config)
            await first.run()
            assert len(first.records) == 1
            assert first.pending
            config.max_pages = 2
            second = CrawlEngine(config)
            await second.run()
            assert len(second.records) == 2
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_archive_links_preserve_code_and_images(tmp_path):
    from docharbor.content import extract_page
    config = Config('https://example.org/docs/', str(tmp_path), robots=False, sitemap=False, workers=1)
    engine = CrawlEngine(config)
    engine.initialize()
    a = extract_page('<main><h1>Start</h1><a href="next#section">Next</a><pre><code>[sample](https://example.org/docs/next)</code></pre><img src="next" alt="image"></main>', config.url)
    b = extract_page('<main><h1>Next</h1><p>Body</p></main>', 'https://example.org/docs/next')
    engine.save_page(config.url, 0, a)
    engine.save_page(b.url, 1, b)
    engine.exports()
    archive = (tmp_path / 'archive.md').read_text()
    assert '[sample](https://example.org/docs/next)' in archive
    assert '![image](https://example.org/docs/next)' in archive
    assert '[Next](#page-' in archive
    offline = (tmp_path / 'offline' / engine.records[config.url]['file']).read_text()
    assert engine.records[b.url]['file'] + '#section' in offline


def test_archive_profile_mismatch(tmp_path):
    import pytest
    first = CrawlEngine(Config('https://example.org/docs/', str(tmp_path), workers=1))
    first.initialize()
    first.checkpoint()
    second = CrawlEngine(Config('https://example.org/other/', str(tmp_path), workers=1))
    with pytest.raises(ValueError, match='anderen Crawl'):
        second.initialize()
