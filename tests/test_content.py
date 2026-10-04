from docharbor.content import extract_page, filename_for, normalize_url
from docharbor.engine import Config, CrawlEngine
import pytest


def test_normalization():
    assert normalize_url('../page?utm_source=a&q=b#anchor', 'https://EXAMPLE.org/guide/start/', True) == 'https://example.org/guide/page?q=b'
    assert normalize_url('https://example.org:443/path?q=x') == 'https://example.org/path'
    assert normalize_url('mailto:me@example.org') is None
    assert normalize_url('https://user:secret@example.org/') is None


def test_documentation_conversion():
    page = extract_page('''<title>Docs</title><nav><a href="/next">Sidebar</a></nav>
    <main><h1>Installation</h1><p>Hello <strong>world</strong>.</p>
    <pre><code class="language-python">print("```")\n  x = 1</code></pre>
    <table><tr><th>Name</th><th>Value</th></tr><tr><td>A</td><td>1</td></tr></table>
    <a href="next#part">Next</a><img src="/image.png" alt="Diagram"></main>''', 'https://example.org/docs/')
    assert page.title == 'Installation'
    assert 'Sidebar' not in page.markdown
    assert '````python' in page.markdown
    assert '  x = 1' in page.markdown
    assert '| Name | Value |' in page.markdown
    assert 'https://example.org/docs/next#part' in page.markdown
    assert 'https://example.org/image.png' in page.markdown
    assert 'https://example.org/next' in page.links


def test_selector_and_scope(tmp_path):
    with pytest.raises(ValueError, match='selektor'):
        extract_page('<main>x</main>', 'https://x.org', '.absent')
    engine = CrawlEngine(Config('https://example.org/docs/start.html', str(tmp_path)))
    assert engine.allowed('https://example.org/docs/chapter')
    assert not engine.allowed('https://example.org/docs-other/chapter')
    assert not engine.allowed('https://sub.example.org/docs/')
    assert not engine.allowed('https://example.org/docs/file.pdf')
    assert filename_for('https://x.org/a/b') != filename_for('https://x.org/a-b')


def test_config_validation(tmp_path):
    with pytest.raises(ValueError):
        Config('javascript:alert(1)', str(tmp_path)).validate()
    with pytest.raises(Exception):
        Config('https://example.org', str(tmp_path), selector='[').validate()
