"""Compare-and-delete metadata folds, restoration checks and subject-scope unions against freshly reread values."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from .. import assets, atomic, image_store, image_surfaces, locks
from . import _contracts, _execution_state, _preview

# ---- folding keys (M9) ------------------------------------------------------------

def _sidecar_now(ex: _execution_state._Exec, side: Path) -> dict | None:
    """A sidecar as it is now, or None (reported) for one reached through a
    symlink, which is never followed, rewritten or deleted."""
    if side.is_symlink():
        ex.keep(side, None, "symlinked-sidecar")
        return None
    try:
        raw = json.loads(side.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError):
        ex.keep(side, None, "unreadable-sidecar")
        return None
    return raw if isinstance(raw, dict) else None


def _still_target(ex: _execution_state._Exec, side: Path, key: str, still: Callable[[], bool] | None) -> bool:
    """Whether a metadata-only key's target still places the picture it was
    folded into. A sync client takes no name lock, so it can replace the
    placement during the fold; the key then describes the new picture's URL
    and must stay for a later run, not be deleted behind the old one (#478)."""
    if still is None or still():
        return True
    ex.keep(side, key, "target-changed-during-fold")
    return False


def _settled_delete(ex: _execution_state._Exec, side: Path, key: str, value: object,
                    still: Callable[[], bool] | None) -> bool | None:
    """`_delete_key_if`, then the target checked once more. The check before
    the delete leaves a window a sync client can still replace the placement
    in; one after it does not, because a target still unchanged then was
    unchanged when the key went. A delete that lost that race puts the key
    back for a later run (#478)."""
    done = _delete_key_if(ex, side, key, value)
    if done and not _still_target(ex, side, key, still):
        _restore_key(ex, side, key, value)
        return None
    return done


def _restore_key(ex: _execution_state._Exec, side: Path, key: str, value: object) -> None:
    """Put a folded key back unless something wrote it again meanwhile."""
    if not ex.guard_dir("before-key-restore", side.parent):
        ex.keep(side, key, image_surfaces.SYMLINKED)
        return
    cur = _sidecar_now(ex, side)
    if cur is None or key in cur:
        return
    cur[key] = value
    atomic.write_text(side, json.dumps(cur, indent=2, sort_keys=True) + "\n")
    ex.wrote = True


def _delete_key_if(ex: _execution_state._Exec, side: Path, key: str, value: object) -> bool | None:
    """Compare-and-delete: drop `key` only while it still holds `value`; an
    emptied sidecar goes with it. True when deleted, None when the key is
    already gone, False when it now holds something else (fold again)."""
    if not ex.guard_dir("before-key-delete", side.parent):
        ex.keep(side, key, image_surfaces.SYMLINKED)
        return None
    cur = _sidecar_now(ex, side)
    if cur is None or key not in cur:
        return None if cur is not None else False
    if cur[key] != value:
        return False
    del cur[key]
    if cur:
        atomic.write_text(side, json.dumps(cur, indent=2, sort_keys=True) + "\n")
    else:
        side.unlink()
    ex.wrote = True
    return True


def _unfoldable(value: object) -> str | None:
    if not isinstance(value, str):
        return "not-a-string"
    return "too-long" if len(value) > image_store.MAX_DESCRIPTION else None


def _with_text(raw: dict, value: str, label: str, state: dict) -> dict | None:
    """The `update` callback folding one description key (§11.D): a pure edit."""
    decided = _preview._decide([*_preview._raw_texts(raw), (value, label)])
    new = dict(raw)
    if "conflicts" in decided:
        if len(decided["conflicts"]) > _contracts.MAX_CONFLICTS:
            state["outcome"] = "capped"
            return None
        new.pop("description", None)
        new["description_conflicts"] = decided["conflicts"]
    else:
        new["description"] = decided["text"]
        new.pop("description_conflicts", None)
    state["outcome"] = "held" if new == raw else "written"
    return None if new == raw else new


def _says(root: Path, image_id: str, value: str) -> bool:
    """Whether the object under `root` now carries `value`: as its text or a
    conflict -- or, for ``""``, any text at all."""
    obj = image_store.read_fresh(image_id, root=root)
    texts = {t for t, _src in _preview._raw_texts(obj.raw)} if obj is not None else set()
    return bool(texts) if value == "" else value in texts


def _fold_text_once(ex: _execution_state._Exec, side: Path, key: str, image_id: str, label: str,
                    still: Callable[[], bool] | None = None) -> bool | None:
    """One attempt: True folded and deleted, None stop (kept or gone), False
    the key moved under the delete."""
    cur = _sidecar_now(ex, side)
    if cur is None or key not in cur:
        return None
    value = cur[key]
    why = _unfoldable(value)
    if why is not None:
        ex.keep(side, key, why)
        return None
    assert isinstance(value, str)
    ex.guard("before-fold")
    state = {"outcome": "absent"}
    image_store.update(image_id, lambda raw: _with_text(raw, value, label, state))
    if state["outcome"] in ("absent", "capped"):
        ex.keep(side, key, "conflicts-capped" if state["outcome"] == "capped"
                else "object-absent")
        return None
    if not _says(ex.root, image_id, value):
        ex.keep(side, key, "fold-not-confirmed")
        return None
    if not _still_target(ex, side, key, still):
        return None
    done = _settled_delete(ex, side, key, value, still)
    if done:
        ex.out["descriptions_folded"] += 1
    return done


def _fold_description(ex: _execution_state._Exec, d: Path, key: str, image_id: str, label: str,
                      still: Callable[[], bool] | None = None) -> bool:
    """Fold `d`'s `descriptions.json[key]` into `image_id` under the sidecar's
    lock, held from the read through the delete (M9); whether it changed."""
    side = d / assets.DESCRIPTIONS_FILE
    with locks.image_sidecar_lock(d, assets.DESCRIPTIONS_FILE):
        for _ in range(_contracts._FOLD_TRIES):
            done = _fold_text_once(ex, side, key, image_id, label, still)
            if done is not False:
                return bool(done)
        ex.keep(side, key, "changed-during-fold")
    return False


def _ours(a: object, scope: str) -> bool:
    return (isinstance(a, dict) and a.get("kind") == _contracts._KIND
            and a.get("relation") == _contracts._RELATION and a.get("scope") == scope)


def _with_subjects(raw: dict, scope: str, cids: list[str]) -> dict | None:
    """The `update` callback unioning `cids` into `scope`'s subjects and
    marking it reviewed (§11.D); None when that changes nothing."""
    assoc = raw.get("associations")
    assoc = list(assoc) if isinstance(assoc, list) else []
    have = {a["id"] for a in assoc if _ours(a, scope) and isinstance(a.get("id"), str)}
    assoc += [{"kind": _contracts._KIND, "relation": _contracts._RELATION, "scope": scope, "id": c}
              for c in sorted(set(cids) - have)]
    reviews = raw.get("reviews")
    reviews = dict(reviews) if isinstance(reviews, dict) else {}
    done = reviews.get("subjects")
    done = [s for s in done if isinstance(s, str)] if isinstance(done, list) else []
    if set(cids) <= have and scope in done:
        return None
    reviews["subjects"] = sorted({*done, scope})
    return {**raw, "associations": assoc, "reviews": reviews}


def _tags(root: Path, image_id: str, scope: str) -> set[str] | None:
    """The object's subjects in `scope` under `root`, None when unreviewed."""
    got = _preview._object_subjects(image_store.read_fresh(image_id, root=root), scope)
    return None if got is None else set(got)


def _fold_subjects_once(ex: _execution_state._Exec, side: Path, key: str, image_id: str, scope: str,
                        still: Callable[[], bool] | None = None) -> bool | None:
    cur = _sidecar_now(ex, side)
    if cur is None or key not in cur:
        return None
    value = cur[key]
    if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
        ex.keep(side, key, "not-a-list-of-ids")
        return None
    ex.guard("before-fold")
    before = _tags(ex.root, image_id, scope)
    image_store.update(image_id, lambda raw: _with_subjects(raw, scope, value))
    after = _tags(ex.root, image_id, scope)
    if after is None or not set(value) <= after:
        ex.keep(side, key, "fold-not-confirmed")
        return None
    if before is not None and before != set(value):
        ex.plan.subject_disagreements.append(
            {"image_id": image_id, "scope": scope,
             "sets": sorted([sorted(before), sorted(set(value))])})
    if not _still_target(ex, side, key, still):
        return None
    done = _settled_delete(ex, side, key, value, still)
    if done:
        ex.out["subjects_folded"] += 1
    return done


def _fold_subjects(ex: _execution_state._Exec, d: Path, key: str, image_id: str, scope: str,
                   still: Callable[[], bool] | None = None) -> bool:
    """`_fold_description` for a world greeting's `subjects.json[key]`."""
    side = d / _contracts._SUBJECTS
    with locks.image_sidecar_lock(d, _contracts._SUBJECTS):
        for _ in range(_contracts._FOLD_TRIES):
            done = _fold_subjects_once(ex, side, key, image_id, scope, still)
            if done is not False:
                return bool(done)
        ex.keep(side, key, "changed-during-fold")
    return False


def _subjects_folded_here(occ: _contracts.Occurrence) -> bool:
    """Subjects fold only in a WORLD greeting (M5)."""
    return occ.kind == "greetings" and occ.scope.startswith("world:")


def _fold_keys(ex: _execution_state._Exec, occ: _contracts.Occurrence, image_id: str) -> bool:
    """A verified file's own keys, in its own directory."""
    changed = _fold_description(ex, occ.dir, occ.name, image_id, _preview._label(ex.plan, occ))
    if _subjects_folded_here(occ):
        changed = _fold_subjects(ex, occ.dir, occ.name, image_id, occ.scope) or changed
    return changed
