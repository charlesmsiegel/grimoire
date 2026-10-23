"""Expression placement namespace using the existing validated actor resolver."""

from pathlib import Path

from .. import assets, characters, overlay, pcs
from ..overlay import _identity
from ..record_art import targets
from .contracts import ExpressionTarget


def resolved(target: ExpressionTarget):
    return targets.resolve(target.scope, target.owner_id, target.kind, target.actor_id, target.version_id)


def directory(target: ExpressionTarget) -> Path:
    address = resolved(target)
    return assets.version_dir(address.root, target.actor_id, target.version_id, target.kind) / "expressions"


def incarnation(target: ExpressionTarget) -> list:
    """Local concurrency identity; deleting/recreating a slug invalidates previews.

    Native file identity is included only in the opaque save token, never stored
    as story metadata. A card replacement also conservatively requires a reload.
    """
    address = resolved(target)
    root = (overlay.actor_root(target.owner_id, target.kind, target.actor_id, v=address.view)
            if address.view else address.root)
    reader = characters.require_version if target.kind == "characters" else pcs.require_version
    card = reader(root, target.actor_id, target.version_id)
    meta = card.parent / ("character.md" if target.kind == "characters" else "pc.md")
    result = []
    for path in (meta, card):
        stat = path.stat()
        result.append([str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns])
    return result


def layers(target: ExpressionTarget) -> list[tuple[str, Path]]:
    address = resolved(target)
    result = []
    if (address.view and _identity._inherits_world(target.owner_id, f"{target.kind}/{target.actor_id}")
            and _identity.world_identity_matches_owned(target.owner_id, target.kind, target.actor_id, address.view.wroot)):
        try:
            reader = characters.require_version if target.kind == "characters" else pcs.require_version
            reader(address.view.wroot, target.actor_id, target.version_id)
        except (characters.CharacterNotFound, characters.VersionNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
            pass
        else:
            result.append(("inherited", assets.version_dir(address.view.wroot, target.actor_id,
                                target.version_id, target.kind) / "expressions"))
    result.append(("local", assets.version_dir(address.root, target.actor_id,
                                target.version_id, target.kind) / "expressions"))
    return result
