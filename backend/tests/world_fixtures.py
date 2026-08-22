"""One seeded world, shared by the two suites that copy a whole one.

`test_world_bundle.py` round-trips a world through a zip and
`test_world_fork.py` copies one directly; both are only worth anything if the
world they act on has something in every corner of the layout, and two
independently-grown seeds would drift until one suite was quietly testing less
than the other. So the seed lives here, beside `llm_fakes` and
`guard_markers` -- the tests package's other shared scaffolding.
"""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.store import (
    characters,
    entities,
    greetings,
    image_descriptions,
    image_subjects,
    pcs,
    taglines,
    tags,
    worlds,
)

# A one-pixel PNG: real binary that must survive verbatim (deflate on an
# already-compressed asset is exactly what a copy must not corrupt).
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
       b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
       b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

#: What the seed guarantees, relative to the world root. Asserted rather than
#: assumed: an elided `plotmap.json` would let a whole category pass untested,
#: and a gallery that silently failed to write would leave every "the copy
#: carries the assets" test passing against no assets at all.
SEEDED_FILES = (
    "world.md", "plotmap.json", "tags.md", "calendar.json",
    # An entity's own gallery and its prose sidecar (#41 asked for the
    # entity-with-assets case by name), and the greeting sidecar that holds
    # ids rather than URLs.
    "locations/the-drowned-library/assets/default/avatar.png",
    "locations/the-drowned-library/assets/default/descriptions.json",
    "greetings/the-gala/assets/default/subjects.json",
    "characters/seraphine/tagline.md",
    "characters/seraphine/assets/winifred/embed-old789.png",
)


def seed_world(name: str = "Saltmarch") -> str:
    """A world exercising every corner a whole-world copy has to carry:
    entities of two kinds, a character with two versions, a tagline and a
    localized avatar URL in its card, a greeting with a localized image in its
    body, a PC persona, tags, a plotmap and a calendar."""
    wid = worlds.create_world(name)
    root = worlds.world_root(wid)

    lid = entities.create_entity(root, "locations", "The Drowned Library")
    entities.create_entity(root, "lore", "The Tide Accord")
    # An ENTITY with assets, named separately from the character and greeting
    # galleries below: entity kinds get the same per-version folder layout
    # (`store/assets.py`), and a copy tested only against characters would not
    # notice losing them. #41 asked for this case by name.
    place = root / "locations" / lid / "assets" / "default"
    place.mkdir(parents=True)
    (place / "avatar.png").write_bytes(PNG)
    entities.update_entity(
        root, "locations", lid,
        body=f"A flooded stack.\n\n![](/api/worlds/{wid}/locations/{lid}/images/avatar)\n")
    # The description sidecar is the one under `assets/` holding prose an author
    # wrote, so it is the one that can carry a URL of its own.
    image_descriptions.set_description(
        root, lid, "default", "avatar",
        f"Shelves under water; see /api/worlds/{wid}/locations/{lid}/images/avatar",
        base="locations")

    cid = characters.create_character(root, "Seraphine", "default",
                                      characters.blank_card("Seraphine"))[0]
    vid = "default"
    card = characters.read_card(root, cid, vid)
    avatar = f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/embed-abc123"
    card["data"]["description"] = f"A tidewitch.\n\n![]({avatar})\n"
    characters.update_version(root, cid, vid, card)
    assets_dir = root / "characters" / cid / "assets" / vid
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "embed-abc123.png").write_bytes(PNG)
    # A second version, its own card and its own asset directory: the per-
    # version tree is the part of a character a hand-written copy walk forgets.
    older = characters.create_version(root, cid, "Winifred",
                                      characters.blank_card("Seraphine"))
    old_card = characters.read_card(root, cid, older)
    old_avatar = f"/api/worlds/{wid}/characters/{cid}/versions/{older}/images/embed-old789"
    old_card["data"]["description"] = f"Younger.\n\n![]({old_avatar})\n"
    characters.update_version(root, cid, older, old_card)
    old_assets = root / "characters" / cid / "assets" / older
    old_assets.mkdir(parents=True, exist_ok=True)
    (old_assets / "embed-old789.png").write_bytes(PNG)
    taglines.write(root, cid, "The tide remembers her name.")

    gid = greetings.create_greeting(root, "The Gala", cid, vid, body="Come in.")
    greetings.update_greeting(
        root, gid,
        body=f"Come in.\n\n![](/api/worlds/{wid}/greetings/{gid}/images/embed-def456)\n")
    gassets = root / "greetings" / gid / "assets" / "default"
    gassets.mkdir(parents=True, exist_ok=True)
    (gassets / "embed-def456.png").write_bytes(PNG)

    # The id-and-offset sidecars the URL rewrite must leave alone: `subjects.json`
    # holds character ids, never URLs, and the rewrite is a documented no-op on
    # it -- a claim worth a file to be made against.
    image_subjects.write_subjects(root, gid, {"embed-def456": [cid]})

    # The third localized-URL shape (`/pcs/{pid}/versions/{vid}/images/{name}`).
    # It travels for the same reason the other two do -- the substitution is on
    # the `/api/worlds/{wid}/` PREFIX, not on a per-kind list -- and that is
    # exactly why it is worth one seeded persona to prove rather than to assert.
    pid = pcs.create_pc(root, "Mara", ["player"], "default", pcs.blank_persona("Mara"))[0]
    persona = pcs.read_persona(root, pid, "default")
    persona["description"] = (
        f"A tidewright.\n\n"
        f"![](/api/worlds/{wid}/pcs/{pid}/versions/default/images/embed-pc001)\n")
    pcs.update_version(root, pid, "default", persona)

    tags.add_tag(root, "Coastal")
    greetings.set_edges(root, gid, leads_to=[gid])
    (root / "calendar.json").write_text(json.dumps({"primary": "gregorian"}), encoding="utf-8")
    for rel in SEEDED_FILES:
        assert (root / rel).is_file(), f"seed did not produce {rel}"
    return wid


def tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, keyed by its path relative to it."""
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}
