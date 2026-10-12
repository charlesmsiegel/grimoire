"""Local download is the retained original, independently cached from tiles."""

import io

import pytest
from PIL import Image

from grimoire.routes import common
from grimoire.store import image_store, world_images
from tests.record_art_fixtures import KINDS, art_url, image_url, png, setup_records, upload


@pytest.fixture
def records(client):
    return setup_records(client)


@pytest.mark.parametrize("scope", ("world", "campaign"))
@pytest.mark.parametrize("kind", KINDS)
def test_download_attachment_does_not_change_inline_headers(client, records, scope, kind):
    upload(client, records, "world", kind, "avatar")
    [row] = client.get(art_url(records, scope, kind)).json()["images"]
    inline = client.get(row["url"])
    attachment = client.get(row["url"] + "&download=1")
    assert attachment.status_code == 200, attachment.text
    assert attachment.headers["content-disposition"].startswith("attachment;")
    assert "attachment" not in inline.headers.get("content-disposition", "")
    assert attachment.content == inline.content
    assert attachment.headers["etag"] != inline.headers["etag"]
    assert "immutable" in attachment.headers["cache-control"]
    # An inline validator cannot turn a differently represented response into
    # a 304, including its download filename and attachment headers.
    assert client.get(row["url"] + "&download=1",
                      headers={"If-None-Match": inline.headers["etag"]}).status_code == 200
    assert client.get(row["url"] + "&download=1",
                      headers={"If-None-Match": attachment.headers["etag"]}).status_code == 304


def test_download_ignores_thumbnail_width(client, records):
    upload(client, records, "world", "characters", "avatar")
    [row] = client.get(art_url(records, "world", "characters")).json()["images"]
    response = client.get(row["url"] + "&download=1&w=128")
    obj = image_store.read(row["image_id"])
    assert response.content == image_store.blob_path(obj.blob_sha256, obj.ext).read_bytes()
    with Image.open(io.BytesIO(response.content)) as picture:
        assert picture.size == (320, 200)


@pytest.mark.parametrize("label", ('Saltmarch:"/\\?*<>|', "CON", "Saltmarch\r\nRealm", "Mara ☾"))
@pytest.mark.parametrize("ext", ("png", 'p"ng\r\n/'))
def test_download_filename_handles_reserved_characters(label, ext):
    header = common.attachment_disposition(label, ext)
    assert header.startswith("attachment;")
    assert "\r" not in header and "\n" not in header
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert not any(c in fallback for c in ':"/\\?*<>|')
    assert fallback.casefold() != "con.png"
    assert "filename*=UTF-8''" in header


@pytest.mark.parametrize("failure", ("missing", "corrupt"))
def test_download_refuses_missing_or_corrupt_blob(client, records, failure):
    uploaded = upload(client, records, "world", "characters", "avatar")
    obj = image_store.read(uploaded["image_id"])
    path = image_store.blob_path(obj.blob_sha256, obj.ext)
    if failure == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    response = client.get(image_url(records, "world", "characters", "avatar"), params={"download": "1"})
    assert response.status_code == 404, response.text


def test_download_preserves_stale_token_revalidation_and_library_serving(client, records):
    world_images.put_image(records["world"], "Saltmarch", png(), "png")
    [row] = world_images.list_images(records["world"])
    url = f'/api/worlds/{records["world"]}/images/Saltmarch'
    response = client.get(url, params={"download": "1", "v": "a" * 64})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache"
    obj = image_store.read(row["image_id"])
    assert response.content == image_store.blob_path(obj.blob_sha256, obj.ext).read_bytes()
    assert 'filename="Saltmarch.png"' in response.headers["content-disposition"]
