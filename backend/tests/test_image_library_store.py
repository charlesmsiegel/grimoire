"""The scope-free half of an image library (``store/image_library.py``).

What a name may be, how big an upload may get, and how a flat directory
enumerates -- with no idea whose library it is. The two scopes over it
(``store.campaign_images``, ``store.world_images``) are tested elsewhere.
"""

import io

import pytest
from PIL import Image

from grimoire.store import image_library


def _png(size=(4, 4), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("name", ["map", "coast-line", "carte_du_monde", "地図"])
def test_a_name_a_link_can_carry_is_addressable(name):
    """Including one in no Latin script: a library is not English, and the rule
    is a denylist of punctuation the surrounding syntax owns."""
    assert image_library.addressable(name)


@pytest.mark.parametrize("name", ["my map", "map(1)", "a#b", "a?b", "und'quote",
                                  "undescribed", "Undescribed", "promote-tmp"])
def test_a_name_a_link_or_a_route_cannot_carry_is_not(name):
    """Three rules in one conjunction, and all three have to hold: what a
    markdown link can carry, what the routes have already spent
    (``undescribed``, case-folded because Windows and macOS fold it), and what
    ``assets.storable`` will write under (``promote-tmp``)."""
    assert not image_library.addressable(name)


def test_validate_size_refuses_only_past_the_cap():
    image_library.validate_size(b"x" * 10)
    with pytest.raises(image_library.ImageTooLarge):
        image_library.validate_size(b"x" * (image_library.MAX_BYTES + 1))


def test_listing_reports_addressable_images_and_ignores_strays(tmp_path):
    """A directory a human browses and a sync client writes into: a stray is
    neither offered nor disturbed, and a file whose name no link can carry is
    not offered either -- the picker could not insert it."""
    (tmp_path / "map.png").write_bytes(_png())
    (tmp_path / "notes.txt").write_text("not ours")
    (tmp_path / "my map.png").write_bytes(_png())

    rows = image_library.listing(tmp_path)
    assert [r["name"] for r in rows] == ["map"]
    assert rows[0]["ext"] == "png" and rows[0]["v"]

    # Untouched, both of them.
    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "my map.png").exists()


def test_listing_is_empty_for_a_directory_that_is_not_there(tmp_path):
    """The world half of a campaign with no world, and every library before its
    first upload. Never an exception."""
    assert image_library.listing(tmp_path / "nope") == []
