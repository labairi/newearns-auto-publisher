import pytest
import requests

from publisher import images


class _Image:
    status_code = 200
    headers = {"Content-Type": "image/webp"}

    def close(self):
        pass


def _page(url, **kw):
    """The live article page: the featured image once, as the post content carries it."""
    class Page(_Image):
        text = '<h1>Title</h1><figure><img src="https://newearns.com/wp-content/uploads/compress-pdf-for-email-featured.webp" class="wp-image-901"/></figure>'
        def raise_for_status(self):
            pass
    return Page()


@pytest.fixture(autouse=True)
def no_image_network(monkeypatch):
    """Tests never call stock photo APIs, open a browser or fetch uploaded files; tests that need them pass fakes."""
    real = images.StockSearch.search
    monkeypatch.setattr(images.StockSearch, "search",
                        lambda self, p, q: [] if self.get is requests.get else real(self, p, q))
    monkeypatch.setattr(images.check_in_post, "__defaults__", (lambda *a, **k: _Image(),))
    monkeypatch.setattr(images.featured_on_page, "__defaults__", (_page,))

    def no_browser(url):
        raise RuntimeError("no browser in tests")
    monkeypatch.setattr(images, "screenshot", no_browser)
