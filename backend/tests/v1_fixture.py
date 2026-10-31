"""A synthetic Grimoire 0.2.1 library, written byte-for-byte as V1 writes it.

Every name and text here is invented. The base files (cards, image sidecars,
placements, persona, appearance bases and the expression manifest) were checked
byte-identical against V1 0.2.1's own writers, and the whole tree, additions
included, was read back by V1 0.2.1's readers in a scratch environment. Nothing
here imports V1. Paths are kept short so the tree also fits Windows' MAX_PATH.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

STAMP = "'2026-01-01T00:00:00Z'"
RED = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAEElEQVR4nGM4IScHRwzEcQCxYxBBO0tjggAAAABJRU5ErkJggg==")
BLUE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAEElEQVR4nGOQkzsBRwzEcQCcIxBBYbnFHgAAAABJRU5ErkJggg==")
#: Object ids V1's pixel canonicalizer computed for RED and BLUE.
RED_ID = "px1-64b20bdfe3230aa03fbc462885001973e2bb46125209756093d6d533608c7fe7"
BLUE_ID = "px1-ea90d0db7985a28c97cc1b5ba599814238f2a995f3744caa366a2a56c2e96a03"
MISSING_ID = "px1-" + "0" * 64
REV = "0123456789abcdef0123456789abcdef"
#: The expression manifest digest V1 computed for the base fixture.
V1_EXPRESSION_DIGEST = "8d4f762456b91a7add8febe21be16499ebfe4fd0e76c5c0d62031df6bc825dc5"
SID = "001--the-tide-comes-in"
CAMPAIGN = "campaigns/the-drowned-ledger"
MARA = "worlds/saltmarch/characters/mara"


def sha(data: str | bytes) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def card(name: str, extensions: dict | None = None) -> str:
    data = {"name": name, "description": "", "personality": "", "scenario": "", "first_mes": "",
            "mes_example": "", "alternate_greetings": [], "tags": [], "extensions": extensions or {}}
    return json.dumps({"spec": "chara_card_v3", "spec_version": "3.0", "data": data},
                      indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def placement(image: str | None = None, focus: int | None = None) -> str:
    raw: dict = {"format": 1}
    if image is not None:
        raw["image"] = image
    if focus is not None:
        raw["focus"] = focus
    return json.dumps(raw, sort_keys=True) + "\n"


def sidecar(image_id: str, data: bytes) -> str:
    raw = {"blob": {"animated": False, "ext": "png", "height": 4, "mime": "image/png",
                    "sha256": sha(data), "size": len(data), "width": 4},
           "format": 1, "id": image_id, "identity": "pixels"}
    return json.dumps(raw, indent=2, sort_keys=True) + "\n"


def _enc(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def expression_manifest(manifest: dict, placements: dict[str, bytes]) -> bytes:
    """V1's compact manifest with its placement count and content digest."""
    digest = hashlib.sha256(_enc(manifest))
    for relpath in sorted(placements):
        digest.update(_enc([relpath, len(placements[relpath])]))
        digest.update(placements[relpath])
    return _enc({**manifest, "content_digest": digest.hexdigest(), "placement_count": len(placements)})


def frontmatter(fields: list[tuple[str, str]], body: str = "") -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in fields) + "---\n\n" + body


def write(root: Path, relpath: str, data: str | bytes) -> None:
    path = root.joinpath(*relpath.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


MARA_DEFAULT = card("Mara")
PERSONA = frontmatter([("name", "Winifred"), ("pronouns", "she/her"), ("summary", "''"),
                       ("birthdate", "''"), ("goals", "''"), ("player_notes", "''")], "A debtor.")
NEUTRAL = placement(RED_ID).encode()
EXPRESSIONS = {"default_outfit": "casual", "format": 1, "hidden_expressions": {}, "hidden_outfits": [],
               "outfits": {"casual": {"label": "Casual"}}, "previous_revision": None, "revision": REV}
SCENE = frontmatter(
    [("title", "The tide comes in"), ("model", "''"), ("created", STAMP), ("updated", STAMP),
     ("identity", "00000000000000000000000000000001")],
    '**Winifred:** <!-- grimoire-response {"post_id": "00000000000000000000000000000011"} -->\n'
    "I knock on the door.\n\n"
    '**Mara:** <!-- grimoire-response {"post_id": "00000000000000000000000000000012"} -->\n'
    "The door opens.\n")


def build_library(root: Path) -> Path:
    """Write the fixture library at `root` (which must not exist) and return it."""
    root.mkdir(parents=True)
    w = lambda rel, data: write(root, rel, data)  # noqa: E731
    w("config.md", frontmatter([("inference_format", "2"), ("inference_retired", "1")]))
    for wid, name in (("saltmarch", "Saltmarch"), ("realm", "Realm")):
        w(f"worlds/{wid}/world.md", frontmatter([("name", name), ("created", STAMP), ("updated", STAMP)]))
        w(f"worlds/{wid}/characters/mara/character.md",
          frontmatter([("name", "Mara"), ("default_version", "default")]))
    w(f"{MARA}/default.json", MARA_DEFAULT)
    w(f"{MARA}/older.json", card("Mara", {"grimoire_label": "Older", "x_synthetic_note": "synthetic"}))
    w("worlds/realm/characters/mara/default.json", MARA_DEFAULT)
    for data, image_id in ((RED, RED_ID), (BLUE, BLUE_ID)):
        digest = sha(data)
        w(f"assets/image-store/blobs/{digest[:2]}/{digest}.png", data)
        w(f"assets/image-store/objects/{image_id[4:6]}/{image_id}.json", sidecar(image_id, data))
    w(f"{MARA}/assets/default/image-refs/avatar.json", placement(RED_ID, focus=40))
    w(f"{MARA}/assets/default/image-refs/gallery_1.json", placement(BLUE_ID))
    w(f"{MARA}/assets/older/expressions/revisions/{REV}/casual/image-refs/neutral.json", NEUTRAL)
    w(f"{MARA}/assets/older/expressions/manifest.json",
      expression_manifest(EXPRESSIONS, {"casual/image-refs/neutral.json": NEUTRAL}))
    w("worlds/realm/characters/mara/assets/default/image-refs/avatar.json", placement(MISSING_ID))
    pc = frontmatter([("name", "Winifred"), ("tags", "coastal"), ("default_version", "default")])
    w("worlds/saltmarch/pcs/winifred/pc.md", pc)
    w("worlds/saltmarch/pcs/winifred/default.md", PERSONA)
    # Flat records: lore, a location with a key V1 does not know, a group with one dangling member.
    w("worlds/saltmarch/lore/the-salt-pact.md",
      frontmatter([("name", "The Salt Pact"), ("keys", "salt,pact")], "An old agreement.\n"))
    w("worlds/saltmarch/locations/tidewatch-keep.md",
      frontmatter([("name", "Tidewatch Keep"), ("lantern_color", "amber")], "A keep.\n"))
    w("worlds/saltmarch/groups/tide-wardens.md",
      frontmatter([("name", "Tide Wardens"), ("leader", "'characters:mara'"),
                   ("members", "'characters:mara,characters:ghost'")], "Wardens.\n"))
    w("worlds/saltmarch/greetings/harbour-dawn.md",
      frontmatter([("name", "Harbour dawn"), ("character", "mara"), ("version", "default")],
                  "Dawn.\n\n![Mara](/api/worlds/saltmarch/characters/mara/versions/default/images/avatar)\n"))
    # The campaign: overlay on saltmarch, Mara and Winifred locked at their default versions.
    w(f"{CAMPAIGN}/campaign.md", frontmatter(
        [("name", "The Drowned Ledger"), ("world", "saltmarch"), ("created", STAMP), ("updated", STAMP),
         ("world_copy", "overlay"), ("inference_format", "2"), ("inference_retired", "1")]))
    w(f"{CAMPAIGN}/sync.md", "---\n---\n\n")
    w(f"{CAMPAIGN}/deleted.json", json.dumps(["assets/characters/mara/default/gallery_1"], indent=2) + "\n")
    w(f"{CAMPAIGN}/characters/mara/character.md",
      frontmatter([("name", "Mara"), ("default_version", "default")]))
    w(f"{CAMPAIGN}/characters/mara/default.json", MARA_DEFAULT)
    w(f"{CAMPAIGN}/characters/mara/assets/default/image-refs/avatar.json", placement(focus=70))
    w(f"{CAMPAIGN}/pcs/winifred/pc.md", pc)
    w(f"{CAMPAIGN}/pcs/winifred/default.md", PERSONA)
    seat = {SID: [{"end": None, "start": 0}]}
    appearances = {
        "characters/mara": {"base": sha(MARA_DEFAULT), "presence": seat, "role": "npc", "scenes": [SID],
                            "version": "default"},
        "pcs/winifred": {"base": sha(PERSONA), "presence": seat, "role": "player", "scenes": [SID],
                         "version": "default"},
    }
    w(f"{CAMPAIGN}/appearances.json", json.dumps(appearances, indent=2, sort_keys=True) + "\n")
    w(f"{CAMPAIGN}/scenes/{SID}.md", SCENE)
    return root


def tree_digest(root: Path) -> dict[str, tuple]:
    """Every entry below `root`: type, bytes digest, mode and mtime (links not followed)."""
    seen = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            path = os.path.join(directory, name)
            info = os.lstat(path)
            content = None
            if os.path.isfile(path) and not os.path.islink(path):
                with open(path, "rb") as handle:
                    content = sha(handle.read())
            seen[os.path.relpath(path, root)] = (info.st_mode, info.st_size, info.st_mtime_ns, content)
    return seen
