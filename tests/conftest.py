import pytest
import requests

from publisher import images


class _Image:
    status_code = 200
    headers = {"Content-Type": "image/webp"}

    def close(self):
        pass


@pytest.fixture(autouse=True)
def no_image_network(monkeypatch):
    """Tests never call stock photo APIs, open a browser or fetch uploaded files; tests that need them pass fakes."""
    real = images.StockSearch.search
    monkeypatch.setattr(images.StockSearch, "search",
                        lambda self, p, q: [] if self.get is requests.get else real(self, p, q))
    monkeypatch.setattr(images.check_in_post, "__defaults__", (lambda *a, **k: _Image(),))

    def no_browser(url):
        raise RuntimeError("no browser in tests")
    monkeypatch.setattr(images, "screenshot", no_browser)
