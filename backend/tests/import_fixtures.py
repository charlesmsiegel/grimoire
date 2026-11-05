"""The 026 apply proof's V1 library: the 0.2.1 fixture plus a second NPC with a line in the scene.

``v1_fixture`` holds one NPC (Mara, two portrayals), one PC (Winifred), a scene and pictures. The
first apply proof needs two NPCs, so this adds Seraphine to Saltmarch and to the campaign, written
the way V1 writes a materialized appearance (a campaign copy locked at its base, an appearance
entry and a post). Every name and text is invented.
"""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.core.identity import SourceRef
from grimoire.import_v1 import plan_import
from grimoire.migrate_v1 import render_json, scan
from grimoire.store import LibraryStore
from grimoire.testing.fileops import snapshot

from image_fixtures import handle
from store_helpers import OWNER
from v1_fixture import (
    CAMPAIGN, MARA_DEFAULT, PERSONA, SID, STAMP, build_library, card, frontmatter, sha, tree_digest, write,
)

ARCHIVE = "fixture"
SERAPHINE_DEFAULT = card("Seraphine")
SALTMARCH, REALM = "world:saltmarch", "world:realm"
MARA_DEFAULT_REF = SourceRef(SALTMARCH, "character", "mara.default").at(f"v1-{sha(MARA_DEFAULT)[:16]}")
SERAPHINE_REF = SourceRef(SALTMARCH, "character", "seraphine.default").at(f"v1-{sha(SERAPHINE_DEFAULT)[:16]}")
WINIFRED_REF = SourceRef(SALTMARCH, "character", "winifred.default").at(f"v1-{sha(PERSONA)[:16]}")
CAMPAIGN_ID = "the-drowned-ledger"
CLOCK = "2026-11-05T00:00:00Z"
SCENE_WITH_SERAPHINE = frontmatter(
    [("title", "The tide comes in"), ("model", "''"), ("created", STAMP), ("updated", STAMP),
     ("identity", "00000000000000000000000000000001")],
    '**Winifred:** <!-- grimoire-response {"post_id": "00000000000000000000000000000011"} -->\n'
    "I knock on the door.\n\n"
    '**Mara:** <!-- grimoire-response {"post_id": "00000000000000000000000000000012"} -->\n'
    "The door opens.\n\n"
    '**Seraphine:** <!-- grimoire-response {"post_id": "00000000000000000000000000000013"} -->\n'
    "Someone waits inside.\n")


def build_two_npc_library(root: Path) -> Path:
    """The V1 fixture with Seraphine as a second NPC of Saltmarch, appearing in the scene."""
    build_library(root)
    w = lambda rel, data: write(root, rel, data)  # noqa: E731
    w("worlds/saltmarch/characters/seraphine/character.md",
      frontmatter([("name", "Seraphine"), ("default_version", "default")]))
    w("worlds/saltmarch/characters/seraphine/default.json", SERAPHINE_DEFAULT)
    w(f"{CAMPAIGN}/characters/seraphine/character.md",
      frontmatter([("name", "Seraphine"), ("default_version", "default")]))
    w(f"{CAMPAIGN}/characters/seraphine/default.json", SERAPHINE_DEFAULT)
    path = root / CAMPAIGN / "appearances.json"
    appearances = json.loads(path.read_text(encoding="utf-8"))
    appearances["characters/seraphine"] = {"base": sha(SERAPHINE_DEFAULT), "presence": {SID: [{"end": None, "start": 0}]},
                                           "role": "npc", "scenes": [SID], "version": "default"}
    w(f"{CAMPAIGN}/appearances.json", json.dumps(appearances, indent=2, sort_keys=True) + "\n")
    w(f"{CAMPAIGN}/scenes/{SID}.md", SCENE_WITH_SERAPHINE)
    return root


def reviewed_manifest(v1: Path, destination: Path, home: Path) -> dict:
    """The 011 manifest as a reviewer saves and reloads it (JSON round trip)."""
    result = scan(v1, archive_id=ARCHIVE, destination=destination, home=home, environ={})
    return json.loads(render_json(result.manifest))


class Env:
    """A two-NPC V1 library, its reviewed manifest and an empty V2 library to import it into."""

    def __init__(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.home.mkdir()
        self.v1 = build_two_npc_library(tmp / "v1")
        self.v1_before = tree_digest(self.v1)
        self.store = LibraryStore.create(tmp / "v2")
        self.manifest = reviewed_manifest(self.v1, self.store.root, self.home)

    def rescan(self) -> dict:
        """Review the V1 library again after a test changed it on purpose."""
        self.v1_before = tree_digest(self.v1)
        self.manifest = reviewed_manifest(self.v1, self.store.root, self.home)
        return self.manifest

    def plan(self, selection=None, *, clock=CLOCK, manifest=None, grant=None):
        return plan_import(self.store, self.v1, manifest or self.manifest, handle=grant or handle(), owner=OWNER,
                           selection=selection, clock=lambda: clock, home=self.home, environ={})

    def v2(self) -> dict:
        return {key: value for key, value in snapshot(self.store.root).items() if not key.startswith(".")}


__all__ = ["ARCHIVE", "Env", "CAMPAIGN_ID", "CLOCK", "MARA_DEFAULT_REF", "REALM", "SALTMARCH", "SERAPHINE_REF",
           "WINIFRED_REF", "build_two_npc_library", "reviewed_manifest"]
