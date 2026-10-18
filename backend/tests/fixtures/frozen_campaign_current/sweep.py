"""Snapshot readers over the newer vintage, in addition to the shared sweep.

The input stays frozen. This command deliberately refreshes only this vintage's
expected read output, using a disposable copy; semantic and input-digest tests
remain separate so a refreshed snapshot cannot authorize damaged data.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from grimoire import store
from grimoire.store import (
    assets,
    image_refs,
    image_store,
    inference_retired,
    knowledge_scene,
    proclock,
)
from grimoire.store.inference import migrate, resolve
from tests import frozen_copy
from tests.fixtures.frozen_campaign import sweep as legacy

HERE = Path(__file__).resolve().parent
HOME = HERE / "home"
SNAPSHOT = HERE / "snapshot.json"
CAMPAIGN = "the-drowned-ledger"
SCENE = "001--the-tide-comes-in"


def sweep(home: Path) -> dict:
    """The existing breadth plus readers of the new stored formats."""
    out = legacy.sweep(home)
    cfg = store.read_config()
    selection = resolve.resolve("scene", CAMPAIGN)
    token, _messages, facts = knowledge_scene.current(CAMPAIGN, SCENE)
    entries, intact = store.knowledge.read_checked(CAMPAIGN)
    audiences = knowledge_scene.audiences_for(CAMPAIGN)
    decisions = {}
    for actor in ("characters:seraphine", "characters:mara"):
        match = store.knowledge.decide(entries, actor=actor, scene=token or "", post=None,
                                       author=None, facts=facts, audiences=audiences)
        decisions[actor] = asdict(match) if match is not None else None
    directory = assets.version_dir(store.worlds.world_root("saltmarch"), "seraphine", "default")
    placement = image_refs.read(directory, assets.AVATAR)
    resolved = image_refs.resolve(directory, assets.AVATAR)
    objects = {}
    for image_id in image_store.iter_ids():
        obj = image_store.read_fresh(image_id)
        objects[image_id] = obj.raw if obj is not None else None
    modern = {
        # This vintage guards persisted inference. Unrelated missing settings
        # use runtime defaults (the test suite disables tracker globally), so
        # they describe the environment rather than these frozen input bytes.
        "config.inference": {k: cfg[k] for k in (*store.inference_keys.GLOBAL_KEYS,
                                                 store.inference_keys.RETIRED_KEY)},
        "llm_connections.list_connections": store.llm_connections.list_connections(),
        "inference_retired.read": inference_retired.read(strict=True),
        "inference.migrate.status": asdict(migrate.status()),
        "inference.resolve[scene]": {
            "role": selection.role, "via": selection.via, "scope": selection.scope,
            "attempts": [{"provider": a.provider_id, "model": a.model, "preset": a.preset_id}
                         for a in selection.attempts]},
        "image_store.objects": objects,
        "image_refs.read[seraphine/default/avatar]": asdict(placement) if placement else None,
        "image_refs.resolve[seraphine/default/avatar]": (
            {**asdict(resolved),
             "blob_path": "<HOME>/" + resolved.blob_path.relative_to(home).as_posix()}
            if resolved else None),
        "knowledge.read_checked": {"entries": entries, "intact": intact},
        "knowledge.decide[scene]": decisions,
    }
    out.update(legacy.normalize(modern, home))
    return out


def main() -> int:
    """Deliberately refresh this expected output; never write either input home."""
    with tempfile.TemporaryDirectory() as scratch:
        home = frozen_copy.copy_home(Path(scratch) / "home", source=HOME)
        with patch.dict(os.environ, {"GRIMOIRE_HOME": str(home)}), \
                patch.object(proclock, "lock_dir", return_value=Path(scratch) / "locks"):
            data = sweep(home)
    SNAPSHOT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT.name}: {len(data)} sections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
