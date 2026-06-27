from grimoire.store import fetch


def test_sniff_ext_detects_png_and_rejects_text():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    assert fetch.sniff_ext(png) == "png"
    assert fetch.sniff_ext(b"not an image") is None


def test_decode_data_uri_returns_bytes_and_ext():
    # 1x1 gif, base64
    uri = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="
    got = fetch.decode_data_uri(uri)
    assert got is not None
    raw, ext = got
    assert ext == "gif"
    assert raw[:6] in (b"GIF87a", b"GIF89a")


def test_decode_data_uri_rejects_non_data_uri():
    assert fetch.decode_data_uri("https://example.com/a.png") is None


def test_host_is_blocked_blocks_loopback():
    assert fetch.host_is_blocked("127.0.0.1") is True
    assert fetch.host_is_blocked("localhost") is True


def test_download_url_rejects_non_image(monkeypatch):
    # bytes that aren't an image and a non-image content-type -> None
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (b"<html>nope", "text/html"))
    assert fetch.download_url("https://h/page") is None


def test_download_url_accepts_sniffed_image_despite_bad_content_type(monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "application/octet-stream"))
    got = fetch.download_url("https://h/x")
    assert got is not None and got[1] == "png"


def test_download_url_swallows_errors(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetch, "_http_get_bytes", boom)
    assert fetch.download_url("https://h/x") is None
