"""Import validates and previews a complete pack before one publication."""

import io
import zipfile

import pytest
from PIL import Image

from grimoire.store import characters, worlds
from grimoire.store.expression_library import contracts, packs, read
from tests.test_expressions_store import apply, outfit, png
from tests.test_expressions_store import target as target


def archive(files):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    data.seek(0)
    return data


def accept(target, preview):
    return packs.publish(target, preview["token"],
        [{"file": row["file"], "outfit": row["outfit"], "expression": row["expression"], "overwrite": True}
         for row in preview["images"]], expected_effective_revision=preview["effective_revision"])


def test_flat_pack_targets_selected_outfit(target):
    preview = packs.preview(target, archive({"neutral.png": png(), "anger.png": png()}), mode="sillytavern", outfit_id="armor")
    result = accept(target, preview)
    assert set(result["outfits"]) == {"armor"}
    assert set(result["outfits"]["armor"]["expressions"]) == {"neutral", "anger"}


def test_folder_import_uses_same_mapping_and_path_validation(target):
    preview = packs.preview_folder(target, [("expressions/armor/neutral.png", io.BytesIO(png()))])
    assert accept(target, preview)["outfits"]["armor"]["expressions"]["neutral"]["available"]
    with pytest.raises(contracts.ExpressionInvalidError):
        packs.preview_folder(target, [("../neutral.png", io.BytesIO(png()))])


def test_hierarchical_pack_round_trips_metadata_and_alpha(target, tmp_path):
    outfit(target)
    apply(target, {"op": "patch-outfit", "outfit": "everyday", "metadata": {
        "label": "Casual", "clothing_aliases": ["coat"], "presentation": {"scale": 1.5}}})
    destination = tmp_path / "sprites.zip"
    packs.export(target, destination)
    with zipfile.ZipFile(destination) as z, Image.open(io.BytesIO(z.read("expressions/everyday/neutral.png"))) as image:
        assert image.getpixel((0, 0))[3] == 100
    apply(target, {"op": "remove-outfit", "outfit": "everyday"})
    with destination.open("rb") as stream:
        result = accept(target, packs.preview(target, stream, mode="grimoire"))
    assert result["outfits"]["everyday"]["label"] == "Casual"
    assert result["outfits"]["everyday"]["presentation"]["scale"] == 1.5


@pytest.mark.parametrize("with_sprites", [False, True])
def test_pack_round_trip_retains_empty_outfit_metadata(target, tmp_path, with_sprites):
    if with_sprites:
        outfit(target)
    apply(target, {"op": "create-outfit", "outfit": "formal", "metadata": {
        "label": "Formal clothing", "clothing_aliases": ["coat"], "presentation": {"scale": 1.5}}})
    destination = tmp_path / "sparse.zip"
    packs.export(target, destination)
    apply(target, *[{"op": "remove-outfit", "outfit": key} for key in read.effective(target)["outfits"]])
    with destination.open("rb") as stream:
        result = accept(target, packs.preview(target, stream, mode="grimoire"))
    assert set(result["outfits"]) == ({"everyday", "formal"} if with_sprites else {"formal"})
    assert result["outfits"]["formal"]["expressions"] == {}
    assert result["outfits"]["formal"]["label"] == "Formal clothing"
    assert result["outfits"]["formal"]["clothing_aliases"] == ["coat"]
    assert result["outfits"]["formal"]["presentation"]["scale"] == 1.5


def test_alternate_expression_collision_requires_selection(target):
    preview = packs.preview(target, archive({"joy.png": png(), "joy_1.png": png()}), mode="sillytavern", outfit_id="everyday")
    assert all(row["ambiguous"] for row in preview["images"])
    before = read.effective(target)
    with pytest.raises(contracts.ExpressionInvalidError):
        accept(target, preview)
    assert read.effective(target) == before
    selection = [{"file": "joy_1.png", "outfit": "everyday", "expression": "joy", "overwrite": False}]
    assert "joy" in packs.publish(target, preview["token"], selection,
        expected_effective_revision=preview["effective_revision"])["outfits"]["everyday"]["expressions"]


@pytest.mark.parametrize("files", [{"../neutral.png": b"x"}, {"Neutral.png": b"x", "neutral.png": b"y"},
                                  {"NUL.png": b"x"}, {"a/../../anger.png": b"x"}])
def test_unsafe_pack_leaves_collection_unchanged(target, files):
    before = outfit(target)
    with pytest.raises(contracts.ExpressionInvalidError):
        packs.preview(target, archive(files), mode="sillytavern", outfit_id="everyday")
    assert read.effective(target) == before


def test_preview_token_bound_to_target_and_revision(target):
    preview = packs.preview(target, archive({"neutral.png": png()}), mode="sillytavern", outfit_id="everyday")
    outfit(target)
    with pytest.raises(contracts.ExpressionConflictError):
        accept(target, preview)


def test_expired_preview_and_overbudget_pack(target, monkeypatch):
    preview = packs.preview(target, archive({"neutral.png": png()}), mode="sillytavern", outfit_id="everyday")
    monkeypatch.setattr(packs, "now", lambda: preview["expires_at"] + 1)
    with pytest.raises(contracts.ExpressionInvalidError):
        accept(target, preview)
    monkeypatch.setattr(packs, "MAX_MEMBERS", 1)
    with pytest.raises(contracts.ExpressionInvalidError):
        packs.preview(target, archive({"neutral.png": png(), "joy.png": png()}), mode="sillytavern", outfit_id="everyday")


def test_preview_cannot_land_on_recreated_actor(target):
    preview = packs.preview(target, archive({"neutral.png": png()}), mode="sillytavern", outfit_id="everyday")
    root = worlds.world_root(target.owner_id)
    characters.delete_character(root, target.actor_id)
    actor, version = characters.create_character(root, "Mara")
    assert (actor, version) == (target.actor_id, target.version_id)
    with pytest.raises(contracts.ExpressionConflictError):
        accept(target, preview)


def test_animated_image_is_reported_without_blocking_static_sprites(target):
    animated = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(animated, "GIF", save_all=True,
        append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0)
    preview = packs.preview(target, archive({"neutral.png": png(), "joy.gif": animated.getvalue()}),
                            mode="sillytavern", outfit_id="everyday")
    assert [row["expression"] for row in preview["images"]] == ["neutral"]
    assert any("joy.gif" in issue for issue in preview["issues"])
    assert accept(target, preview)["outfits"]["everyday"]["expressions"]["neutral"]["available"]


@pytest.mark.parametrize("existing", [False, True])
def test_export_enforces_aggregate_pixel_budget_before_publication(target, tmp_path, monkeypatch, existing):
    outfit(target)
    apply(target, {"op": "put-expression", "outfit": "everyday", "expression": "joy", "data": png(), "ext": "png"})
    destination = tmp_path / "sprites.zip"
    if existing:
        destination.write_bytes(b"previous export")
    monkeypatch.setattr(packs, "MAX_PIXELS", 191)  # Two 8x12 sprites each fit; their sum exceeds the budget.
    with pytest.raises(contracts.ExpressionBudgetError, match="megapixels"):
        packs.export(target, destination)
    assert destination.read_bytes() == b"previous export" if existing else not destination.exists()


def test_export_at_pixel_budget_round_trips_shared_sprite_members(target, tmp_path, monkeypatch):
    outfit(target)
    apply(target, {"op": "put-expression", "outfit": "everyday", "expression": "joy", "data": png(), "ext": "png"})
    monkeypatch.setattr(packs, "MAX_PIXELS", 192)
    destination = tmp_path / "sprites.zip"
    packs.export(target, destination)
    with destination.open("rb") as stream:
        result = accept(target, packs.preview(target, stream, mode="grimoire"))
    assert set(result["outfits"]["everyday"]["expressions"]) == {"neutral", "joy"}
