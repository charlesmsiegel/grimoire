"""A second frozen store vintage, read independently of today's writers.

The original frozen fixture remains the legacy migration contract. This sibling
freezes format-2 inference, retirement, image objects and knowledge overrides as
they existed on its documented writer revision. A snapshot is only one oracle:
semantics and input digests must still reject corruption if output is regenerated.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from grimoire import store
from grimoire.store import (
    assets,
    frontmatter,
    image_refs,
    image_store,
    inference_retired,
    knowledge_scene,
)
from grimoire.store.inference import migrate, resolve, retire
from tests import frozen_copy
from tests.fixtures.frozen_campaign_current import build
from tests.fixtures.frozen_campaign_current import sweep as current

HERE = Path(__file__).parent / "fixtures" / "frozen_campaign_current"
HOME = HERE / "home"
CAMPAIGN = "the-drowned-ledger"
SCENE = "001--the-tide-comes-in"
IMAGE_ID = "px1-4d1c1c15a23950ea4e48620b1c9a9c1fe8d7e7a5a14229cdf650a4119a8d0b93"
BLOB_SHA = "84a72db063766168c722a1a7803157c49c11402ec2b1264e359b95b52f54f45d"


@pytest.fixture(autouse=True)
def _both_inputs_untouched() -> Iterator[None]:
    """Every operation works on copies; both checked-in input trees stay frozen."""
    originals = {root: frozen_copy.digest(root) for root in (frozen_copy.HOME, HOME)}
    yield
    for root, before in originals.items():
        assert frozen_copy.digest(root) == before, f"a frozen test wrote into {root.name}"


def test_current_input_matches_its_pinned_manifest() -> None:
    """Output snapshot regeneration cannot silently replace the input vintage."""
    assert HOME.is_dir(), "the current-format input vintage must be checked in"
    manifest = json.loads((HERE / "digest.json").read_text(encoding="utf-8"))
    assert frozen_copy.digest(HOME) == manifest


@pytest.fixture
def current_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Only a byte-for-byte copy is prepared; today's writers never mint the input."""
    home = frozen_copy.copy_home(tmp_path / "home", source=HOME)
    monkeypatch.setenv("GRIMOIRE_HOME", str(home))
    return home


def _assert_inference(home: Path) -> None:
    """A format marker alone cannot bless lost selections or retirement data."""
    cfg = frontmatter.parse_frontmatter_head(home / "config.md")
    campaign = frontmatter.parse_frontmatter_head(
        home / "campaigns" / CAMPAIGN / "campaign.md")
    for meta in (cfg, campaign):
        assert meta[store.inference_keys.FORMAT_KEY] == "2"
        assert meta[store.inference_keys.RETIRED_KEY] == "1"
    assert cfg["role_primary_provider"] == "openrouter"
    assert cfg["role_primary_model"] == "anthropic/claude-opus-4.1"
    assert not set(store.inference_keys.LEGACY_GLOBAL_KEYS) & cfg.keys()
    assert not retire.LEGACY_CAMPAIGN_KEYS & campaign.keys()
    retired = inference_retired.read(strict=True)
    assert retired["fields"] == {
        "claude": {"model": "opus", "post_process": "none"},
        "openrouter": {"model": "anthropic/claude-opus-4.1", "post_process": "none"},
    }
    for provider in ("openrouter", "claude"):
        raw = frontmatter.parse_frontmatter_head(home / "llm_connections" / f"{provider}.md")
        assert raw[store.llm_connections.STRIPPED_KEY] == "1"
        assert not set(store.llm_connections.MODEL_FIELDS) & raw.keys()
        assert raw["api_key"] == ""
    selected = resolve.resolve("scene", CAMPAIGN)
    assert not selected.legacy
    assert selected.attempts
    assert (selected.attempts[0].provider_id, selected.attempts[0].model) == (
        "openrouter", "anthropic/claude-opus-4.1")
    status = migrate.status()
    assert status.state == "done" and not status.skipped and not status.retirement["left"]


def _assert_image() -> None:
    """The pinned source bytes, pixel identity, sidecar and placement agree."""
    directory = assets.version_dir(store.worlds.world_root("saltmarch"), "seraphine", "default")
    assert image_refs.read(directory, assets.AVATAR) == image_refs.Ref(assets.AVATAR, IMAGE_ID, None)
    obj = image_store.read_fresh(IMAGE_ID)
    assert obj is not None
    assert (obj.identity, obj.blob_sha256, obj.ext, obj.width, obj.height) == (
        "pixels", BLOB_SHA, "png", 24, 16)
    resolved = image_refs.resolve(directory, assets.AVATAR)
    assert resolved is not None and resolved.image_id == IMAGE_ID
    content = resolved.blob_path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == BLOB_SHA
    assert content == (HERE.parent / "images" / "png_onepixel.png").read_bytes()
    assert assets.image_id(store.worlds.world_root("saltmarch"), "seraphine", "default",
                           assets.AVATAR) == IMAGE_ID


def _assert_knowledge() -> None:
    """The stored grants match real scene facts, including actor precedence."""
    entries, intact = store.knowledge.read_checked(CAMPAIGN)
    assert intact and set(entries) == {"k-00000001", "k-00000002"}
    token, _messages, facts = knowledge_scene.current(CAMPAIGN, SCENE)
    assert token
    for entry in entries.values():
        assert entry["subject"]["scene"] == token
        assert store.knowledge.holds(entry, facts)
    audiences = knowledge_scene.audiences_for(CAMPAIGN)
    for actor, expected in (("seraphine", ("k-00000001", "knows", "override_all")),
                            ("mara", ("k-00000002", "unaware", "override"))):
        match = store.knowledge.decide(entries, actor=f"characters:{actor}", scene=token,
                                       post=None, author=None, facts=facts, audiences=audiences)
        assert match is not None
        assert (match.kid, match.status, match.basis) == expected


def test_inference_is_stored_current_and_retired(current_home: Path) -> None:
    _assert_inference(current_home)


def test_image_is_addressed_by_bytes_and_pixels(current_home: Path) -> None:
    _assert_image()


def test_overrides_match_the_scene_and_actor_precedence(current_home: Path) -> None:
    _assert_knowledge()


def test_changed_scene_expires_the_stored_grant(current_home: Path) -> None:
    """Today's writer changes a COPY; the frozen subject keeps its old fingerprint."""
    entries = store.knowledge.read(CAMPAIGN)
    store.scenes.append_message(CAMPAIGN, SCENE, "user", "I put my hand on the ledger.")
    token, _messages, facts = knowledge_scene.current(CAMPAIGN, SCENE)
    assert not store.knowledge.holds(entries["k-00000001"], facts)
    audiences = knowledge_scene.audiences_for(CAMPAIGN)
    assert store.knowledge.decide(entries, actor="characters:seraphine", scene=token or "",
                                  post=None, author=None, facts=facts, audiences=audiences) is None
    # Withholding fails closed even when the scene text has changed.
    match = store.knowledge.decide(entries, actor="characters:mara", scene=token or "",
                                   post=None, author=None, facts=facts, audiences=audiences)
    assert match is not None and match.status == "unaware"


def test_current_read_only_sweep_matches_its_own_snapshot(current_home: Path) -> None:
    before = frozen_copy.digest(current_home)
    expected = json.loads(current.SNAPSHOT.read_text(encoding="utf-8"))
    assert current.sweep(current_home) == expected
    assert frozen_copy.digest(current_home) == before, "a current-format read wrote state"


def test_already_current_migrations_are_byte_idempotent(current_home: Path) -> None:
    """These are already-current assertions; the legacy rename contract stays separate."""
    before = frozen_copy.digest(current_home)
    for _ in range(2):
        store.migrations.migrate_scene_ids()
        result = migrate.ensure()
        assert result.state == "done" and not result.retirement["left"]
        assert frozen_copy.digest(current_home) == before
    _assert_inference(current_home)
    _assert_image()
    _assert_knowledge()


@pytest.mark.parametrize("mutation", ["selection", "retirement", "unretired"])
def test_inference_oracle_rejects_corruption_without_a_snapshot(
    current_home: Path, mutation: str,
) -> None:
    if mutation == "selection":
        store.write_config(role_primary_model="")
    elif mutation == "retirement":
        (current_home / inference_retired.FILENAME).unlink()
    else:
        # A damaged synced copy can carry a retired marker beside model fields.
        connection = current_home / "llm_connections" / "openrouter.md"
        meta = frontmatter.parse_frontmatter_head(connection)
        connection.write_text(frontmatter.dump_frontmatter({**meta, "model": "opus"}, ""),
                              encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_inference(current_home)


@pytest.mark.parametrize("mutation", ["blob", "placement"])
def test_image_oracle_rejects_corruption_without_a_snapshot(
    current_home: Path, mutation: str,
) -> None:
    if mutation == "blob":
        image_store.blob_path(BLOB_SHA, "png").write_bytes(b"corrupted frozen image")
    else:
        directory = assets.version_dir(store.worlds.world_root("saltmarch"), "seraphine", "default")
        image_refs.delete(directory, assets.AVATAR)
    with pytest.raises(AssertionError):
        _assert_image()


@pytest.mark.parametrize("mutation", ["digest", "identity", "precedence"])
def test_knowledge_oracle_rejects_corruption_without_a_snapshot(
    current_home: Path, mutation: str,
) -> None:
    if mutation == "precedence":
        entry = store.knowledge.get(CAMPAIGN, "k-00000002")
        assert entry is not None
        entry["status"] = "knows"
        store.knowledge.update(CAMPAIGN, "k-00000002", entry)
    else:
        entry = store.knowledge.get(CAMPAIGN, "k-00000001")
        assert entry is not None
        entry["subject"]["digest" if mutation == "digest" else "scene"] = (
            "0" * (64 if mutation == "digest" else 32))
        store.knowledge.update(CAMPAIGN, "k-00000001", entry)
    with pytest.raises(AssertionError):
        _assert_knowledge()


def test_builder_refuses_existing_output_before_writing(tmp_path: Path) -> None:
    out = tmp_path / "existing"
    out.mkdir()
    (out / "sentinel").write_bytes(b"leave this directory unchanged")
    before = frozen_copy.digest(out)
    with pytest.raises(FileExistsError):
        build.build(out)
    assert frozen_copy.digest(out) == before


@pytest.mark.parametrize("root", [HOME, frozen_copy.HOME])
def test_builder_refuses_new_paths_inside_either_frozen_fixture(root: Path) -> None:
    with pytest.raises(ValueError, match="outside both"):
        build.build(root / "forbidden-new-vintage")


def test_builder_requires_an_explicit_output() -> None:
    with pytest.raises(SystemExit) as exc:
        build.main([])
    assert exc.value.code == 2
