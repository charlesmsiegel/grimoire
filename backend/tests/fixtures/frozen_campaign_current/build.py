"""One-time provenance for a NEW current-format frozen input, never a test setup.

This starts with the shipped legacy fixture and uses today's store writers to
carry it forward and add newer records. Later tests copy the frozen result,
never this builder: a future format needs another vintage, not a replacement.
The deterministic clock/entropy is scoped to this synthetic build only.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import shutil
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from grimoire import store
from grimoire.store import (
    assets,
    cache_sync,
    knowledge,
    knowledge_scene,
    llm_connections,
    paths,
    proclock,
    usage,
)
from grimoire.store.inference import migrate
from grimoire.store.scenes import identity
from tests import frozen_copy

HERE = Path(__file__).resolve().parent
CAMPAIGN = "the-drowned-ledger"
SCENE = "001--the-tide-comes-in"
IMAGE = HERE.parent / "images" / "png_onepixel.png"
WRITER_REVISION = "6e68ef2837d83e6dd527e22b43cb396e350627f6"
BUILT_UTC = "2026-10-18"


class Clock:
    """Stable timestamps for the synthetic writes, independent of wall time."""

    def __init__(self) -> None:
        self.ticks = itertools.count()

    def now(self, tz: object = None) -> datetime:
        """The next fixed minute; the store asks for UTC timestamps."""
        return datetime(2026, 10, 18, 4, tzinfo=UTC) + timedelta(minutes=next(self.ticks))


def _knowledge() -> None:
    """Persist a real scene grant and a more specific withholding override."""
    token, _messages, facts = knowledge_scene.current(CAMPAIGN, SCENE)
    if token is None:
        raise RuntimeError("the synthetic scene needs an identity before its overrides")
    subject = {"kind": "scene", "scene": token, "digest": facts.digest}
    for kid, audience, name, status in (
        ("k-00000001", "*", "", "knows"),
        ("k-00000002", "characters:mara", "Mara", "unaware"),
    ):
        knowledge.add(CAMPAIGN, {"audience": audience, "audience_name": name,
                                 "status": status, "subject": subject, "note": "",
                                 "source": "manual"}, kid=kid)


def _discard_runtime(home: Path) -> None:
    """Freeze authoritative bytes only; these derived outputs remain scratch data."""
    for name in (".cache", "backups"):
        target = (home / name).resolve()
        if not target.is_relative_to(home):
            raise ValueError("a runtime output escaped the new synthetic home")
        if target.exists():
            shutil.rmtree(target)
    for lock in home.rglob("*.lock"):
        lock.unlink()


def build(out: Path) -> Path:
    """Mint a NEW isolated vintage; refuse existing or checked-in fixture paths.

    Imports above are read-free. The first store operation follows the explicit
    GRIMOIRE_HOME override; this never uses bootstrap, API startup or a provider.
    """
    out = out.resolve()
    if out.exists():
        raise FileExistsError(f"refusing to overwrite {out}")
    if any(out.is_relative_to(root) for root in (HERE, frozen_copy.HOME.parent)):
        raise ValueError("build in scratch, outside both checked-in frozen fixtures")
    revisions = itertools.count()
    identities = itertools.count()

    def token_hex(nbytes: int) -> str:
        """Deterministic provider revisions, scoped to this builder's writes."""
        return hashlib.shake_256(f"frozen-current:{next(revisions)}".encode()).hexdigest(nbytes)

    def uuid4() -> uuid.UUID:
        """Opaque tokens with stable, distinct prefixes, including revision tokens."""
        return uuid.uuid5(uuid.NAMESPACE_URL, f"grimoire:frozen-current:{next(identities)}")

    env = {"GRIMOIRE_HOME": str(out), "GRIMOIRE_INFERENCE_AUTOMIGRATE": "0",
           "GRIMOIRE_CACHE_WARM": "0"}
    with patch.dict(os.environ, env), patch.object(paths, "datetime", Clock()), \
            patch.object(proclock, "lock_dir", return_value=out / ".cache" / "machine-locks"), \
            patch.object(uuid, "uuid4", side_effect=uuid4), \
            patch.object(usage, "_now", return_value="2026-10-18T04:00:00Z"), \
            patch.object(llm_connections.secrets, "token_hex", side_effect=token_hex):
        frozen_copy.copy_home(out)
        store.migrations.migrate_scene_ids()
        migrated = migrate.ensure()
        if migrated.state != "done" or migrated.retirement["left"]:
            raise RuntimeError(f"incomplete synthetic migration: {migrated}")
        for scene in store.scenes.list_scenes(CAMPAIGN):
            identity.ensure_identity(CAMPAIGN, scene["id"])
        assets.put_image(store.worlds.world_root("saltmarch"), "seraphine", "default",
                         assets.AVATAR, IMAGE.read_bytes(), "png")
        _knowledge()
        # Migration also writes config, retirement and usage records. These have
        # no cache recipes; an explicit record scope selects the syncable paths.
        if not cache_sync.run(all_records=True, embed=False).ok:
            raise RuntimeError("the synthetic records did not sync successfully")
        if not cache_sync.run(all_records=True, embed=False, verify=True).ok:
            raise RuntimeError("the synthetic cache verification failed")
    _discard_runtime(out)
    return out


def main(argv: list[str]) -> int:
    """Build only the explicitly named, nonexistent scratch output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        home = build(args.out)
    except (FileExistsError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"home": str(home), "writer_revision": WRITER_REVISION,
                      "built_utc": BUILT_UTC, "entries": len(frozen_copy.digest(home))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
