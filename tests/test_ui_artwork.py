from __future__ import annotations

import io
import os

import pytest
import requests
from PIL import Image
from presence_fixtures import FakeLog

from totstats.ui import artwork
from totstats.ui.artwork import Artwork, cache_name

ASSET = "https://outlasttrialsstats.com/game-assets/photo_FP_Trial.webp"
NOW = 1_800_000_000.0


@pytest.mark.parametrize(
    ("url", "name"),
    [
        (ASSET, "photo_FP_Trial.webp"),
        ("https://outlasttrialsstats.com/game-assets/icon_invader.webp", "icon_invader.webp"),
        ("https://example.com/game-assets/photo_FP_Trial.webp", None),
        ("https://outlasttrialsstats.com/game-assets/../secret.webp", None),
        ("https://outlasttrialsstats.com/game-assets/sub/photo.webp", None),
        ("https://outlasttrialsstats.com/game-assets/photo.webp?x=1", None),
        ("https://outlasttrialsstats.com/game-assets/photo.png", None),
    ],
)
def test_only_plain_site_assets_are_fetched(url, name):
    assert cache_name(url) == name


def _webp(size: int = 4) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (size, size), (200, 30, 30, 255)).save(buffer, format="WEBP")
    return buffer.getvalue()


class _Response:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, size: int):
        for start in range(0, len(self._body), size):
            yield self._body[start:start + size]

    def close(self) -> None:
        self.closed = True


class _InlineThread:
    def __init__(self, target, args, **_kwargs) -> None:
        self._run = lambda: target(*args)

    def start(self) -> None:
        self._run()


@pytest.fixture
def inline(monkeypatch):
    monkeypatch.setattr(artwork.threading, "Thread", _InlineThread)


def _serve(monkeypatch, response_for):
    calls = []

    def get(url, **_kwargs):
        calls.append(url)
        return response_for(url)

    monkeypatch.setattr(artwork.requests, "get", get)
    return calls


def _store(cache_dir=None) -> Artwork:
    return Artwork(cache_dir, FakeLog(), lambda fn: fn(), clock=lambda: NOW)


def test_a_download_is_kept_on_disk_and_announced(tmp_path, monkeypatch, inline):
    calls = _serve(monkeypatch, lambda _url: _Response(_webp()))
    store = _store(tmp_path)
    ready = []

    assert store.get(ASSET, lambda: ready.append(True)) is None
    assert ready == [True]
    assert (tmp_path / "photo_FP_Trial.webp").is_file()
    assert store.get(ASSET, lambda: None).size == (4, 4)

    os.utime(tmp_path / "photo_FP_Trial.webp", (NOW, NOW))
    assert _store(tmp_path).get(ASSET, lambda: None) is not None
    assert calls == [ASSET]


def test_a_failed_download_is_not_retried_until_asked(monkeypatch, inline):
    def fail(_url):
        raise requests.ConnectionError("offline")

    calls = _serve(monkeypatch, fail)
    store = _store()

    assert store.get(ASSET, lambda: None) is None
    assert store.get(ASSET, lambda: None) is None
    assert len(calls) == 1

    store.retry_failed()
    store.get(ASSET, lambda: None)
    assert len(calls) == 2


@pytest.mark.parametrize(
    "response",
    [
        _Response(b"x" * 10, {"Content-Length": str(artwork.MAX_BYTES + 1)}),
        _Response(b"x" * (artwork.MAX_BYTES + 1)),
        _Response(b"<html>maintenance</html>"),
    ],
    ids=["declared too large", "streamed too large", "not an image"],
)
def test_a_bad_answer_counts_as_failed_and_is_closed(monkeypatch, inline, response):
    _serve(monkeypatch, lambda _url: response)
    store = _store()
    assert store.get(ASSET, lambda: None) is None
    assert ASSET in store._failed
    assert ASSET not in store._pending
    assert response.closed


def test_an_unexpected_decoding_error_still_ends_the_download(monkeypatch, inline):
    _serve(monkeypatch, lambda _url: _Response(_webp()))

    def bomb(_body):
        raise Image.DecompressionBombError("too many pixels")

    monkeypatch.setattr(artwork, "decode", bomb)
    store = _store()
    assert store.get(ASSET, lambda: None) is None
    assert ASSET in store._failed
    assert ASSET not in store._pending


def test_a_stale_copy_is_shown_while_a_fresh_one_comes_in(tmp_path, monkeypatch, inline):
    cached = tmp_path / "photo_FP_Trial.webp"
    cached.write_bytes(_webp(4))
    old = NOW - artwork.STALE_AFTER - 60
    os.utime(cached, (old, old))
    calls = _serve(monkeypatch, lambda _url: _Response(_webp(8)))
    ready = []

    store = _store(tmp_path)
    assert store.get(ASSET, lambda: ready.append(True)).size in ((4, 4), (8, 8))
    assert calls == [ASSET]
    assert ready == [True]
    assert store.get(ASSET, lambda: None).size == (8, 8)


def test_a_stale_copy_stays_when_the_refresh_fails(tmp_path, monkeypatch, inline):
    cached = tmp_path / "photo_FP_Trial.webp"
    cached.write_bytes(_webp(4))
    old = NOW - artwork.STALE_AFTER - 60
    os.utime(cached, (old, old))

    def fail(_url):
        raise requests.ConnectionError("offline")

    _serve(monkeypatch, fail)
    store = _store(tmp_path)
    assert store.get(ASSET, lambda: None).size == (4, 4)
    assert ASSET not in store._failed
    assert store.get(ASSET, lambda: None).size == (4, 4)


def test_a_damaged_cache_file_is_fetched_again(tmp_path, monkeypatch, inline):
    (tmp_path / "photo_FP_Trial.webp").write_bytes(b"not a picture")
    calls = _serve(monkeypatch, lambda _url: _Response(_webp()))
    store = _store(tmp_path)
    assert store.get(ASSET, lambda: None) is None
    assert calls == [ASSET]
    assert store.get(ASSET, lambda: None).size == (4, 4)


def test_anything_but_a_site_asset_is_never_requested(monkeypatch, inline):
    monkeypatch.setattr(artwork.requests, "get", lambda *_a, **_kw: pytest.fail("requested"))
    assert _store().get("https://example.com/x.webp", lambda: None) is None
