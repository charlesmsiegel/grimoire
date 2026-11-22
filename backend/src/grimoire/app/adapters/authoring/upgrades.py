"""079's source upgrade previews and optional dependencies on the Authoring page: ``authoring.upgrades``,
``authoring.pin_plan`` and ``authoring.pin``.

``upgrades`` is a read of any source: 079's ``preview_source_removal`` (every reference from the rest of the library
that removing or disabling the source would leave missing, grouped by the file that holds it, with each reference's
status, the source's entities that would go, the problems that make the preview incomplete and ``complete``) and
079's ``dependency_report`` of the source's ``optional_dependencies`` (pinned, drifted, following, missing). Nothing
is written; a preview is one read, not a lock or an authorization.

**Pinning a dependency** is 079's ``pin_dependency``, reviewed first. ``pin_plan`` names the dependency's current
version and the declaration list before and after, and the exact bytes of the source manifest the pin would write
(staged in a store transaction that is never committed), against the manifest etag the page read. ``pin`` takes the
same dependency, the version and the etag that were reviewed: a manifest that moved meanwhile refuses ``changed``
(the page reads again), as does a dependency whose version moved since the review, and the pin is otherwise 079's own
guarded compare-and-swap. Retried after it was made, the manifest already declares that version: ``already_applied``,
nothing is written again. Re-pinning rewrites no reference anywhere.

Not here: previewing an update needs a candidate library (an unpacked update opened read-only), and this app has no
way to open one (package distribution is Sprint 103's); applying an update or a removal is not built in 079.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from grimoire.app.adapter import AdapterContext
from grimoire.core.identity import validate_source_id
from grimoire.reviewapi.views import file_views
from grimoire.sourceupgrades import (
    DEPENDENCIES_KEY,
    SourceUpgradeError,
    declared_dependencies,
    dependency_report,
    pin_dependency,
    preview_source_removal,
    source_version,
)
from grimoire.sourceupgrades.model import MAX_DEPENDENCIES
from grimoire.store import RecordNotFound, StoreError
from grimoire.textapi import ApiRefused

from .common import refused, source_of

UPDATE_UNAVAILABLE = ("079 previews an update against a candidate library (an unpacked update opened read-only), and "
                      "this app has no way to open one yet: installing a package is Sprint 103's.")
APPLY_UNAVAILABLE = "079 previews a change only: applying an update or removing a source is not built."
#: At most this many referring files are listed in full; the counts are always whole.
MAX_FILES = 200
#: Other sources offered to declare as a dependency.
MAX_OFFERED = 200


def _removal(ctx: AdapterContext, source: str) -> dict:
    try:
        preview = preview_source_removal(ctx.store, source)
    except SourceUpgradeError as error:
        raise refused(error, "upgrade_refused") from None
    groups: dict[str, dict] = {}
    for reference in preview.references:
        group = groups.setdefault(reference.path, {"path": reference.path, "record": reference.record,
                                                   "references": []})
        group["references"].append({"text": reference.text, "status": reference.status, "before": reference.before,
                                    "after": reference.after, "newer_head": reference.newer_head,
                                    "attention": reference.attention})
    files = list(groups.values())
    statuses: dict[str, int] = {}
    for reference in preview.references:
        statuses[reference.status] = statuses.get(reference.status, 0) + 1
    return {"action": preview.action, "complete": preview.complete, "before": preview.before,
            "references": len(preview.references), "missing": len(preview.missing()),
            "attention": len(preview.attention), "statuses": statuses, "files": files[:MAX_FILES],
            "more_files": max(0, len(files) - MAX_FILES),
            "entities": [{"ref": str(change.ref), "status": change.status, "before": change.before,
                          "after": change.after, "name": change.name_before} for change in preview.entities],
            "edited": [str(ref) for ref in preview.edited],
            "problems": [{"path": problem.path, "code": problem.code, "side": problem.side}
                         for problem in preview.problems]}


def _dependencies(ctx: AdapterContext, source: str) -> dict:
    view = ctx.store.load()
    stored = view.sources.get(source)
    if stored is None:
        return {"loaded": False, "etag": None, "declared": [], "problem": None, "offered": []}
    try:
        report = dependency_report(view, source)
        problem = None
    except SourceUpgradeError as error:
        report, problem = (), {"reason": error.reason, "message": str(error)}
    declared = {item.dependency.source for item in report}
    offered = sorted(other for other in view.sources if other != source and other not in declared)
    return {"loaded": True, "etag": stored.etag, "problem": problem,
            "declared": [{"source": item.dependency.source, "version": item.dependency.version,
                          "status": item.status, "current": item.current} for item in report],
            "offered": offered[:MAX_OFFERED], "limit": MAX_DEPENDENCIES}


def upgrades(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What removing (or disabling) the source would leave missing, and its declared dependencies. Reads only."""
    source = source_of(args)
    return {"source": source, "removal": _removal(ctx, source), "dependencies": _dependencies(ctx, source),
            "update": {"available": False, "reason": UPDATE_UNAVAILABLE},
            "apply": {"available": False, "reason": APPLY_UNAVAILABLE}, "written": False}


def _dependency_of(args: Mapping[str, object], source: str) -> str:
    value = args.get("dependency")
    if type(value) is not str:
        raise ApiRefused("invalid_field", "dependency must be a source id")
    if value == source:
        raise ApiRefused("self_dependency", "a source does not depend on itself")
    try:
        validate_source_id(value)
    except ValueError:
        raise ApiRefused("invalid_field", "dependency is not a source id") from None
    return value


def _staged(ctx: AdapterContext, source: str, dependency: str, expected: object) -> dict:
    """What 079's pin of ``dependency`` would write now, against the manifest etag ``expected``. Writes nothing."""
    if type(expected) is not str:
        raise ApiRefused("review_required", "name the manifest etag the page read (expected)")
    store = ctx.store
    try:
        stored = store.read_source(source)
    except (RecordNotFound, StoreError) as error:
        raise ApiRefused("missing_source", f"{source} cannot be read: {error}") from None
    if stored.etag != expected:
        raise ApiRefused("changed", "the source's manifest changed since it was read; read it again")
    version = source_version(store.load(), dependency)
    if version is None:
        raise ApiRefused("missing_dependency", f"{dependency} is not loaded in this library, so it has no version to "
                                               "pin")
    try:
        current = list(declared_dependencies(stored))
    except SourceUpgradeError as error:
        raise refused(error, "upgrade_refused", "the declarations are malformed: fix them by hand first") from None
    before = [{"source": item.source, "version": item.version} for item in current]
    at = next((index for index, item in enumerate(current) if item.source == dependency), None)
    after = list(before)
    entry: dict[str, str | None] = {"source": dependency, "version": version}
    if at is None:
        after.append(entry)
    else:
        after[at] = entry
    if len(after) > MAX_DEPENDENCIES:
        raise ApiRefused("limit_exceeded", f"a source declares at most {MAX_DEPENDENCIES} dependencies")
    extra = dict(stored.manifest.extra)
    extra[DEPENDENCIES_KEY] = after
    transaction = store.transaction()  # staged to show its exact bytes; never committed
    transaction.put(replace(stored.manifest, extra=extra), expected)
    files = file_views(transaction.preview(), store.read_exact)
    return {"dependency": dependency, "version": version, "expected": expected, "before": before, "after": after,
            "declared": at is not None, "files": files}


def pin_plan(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """What pinning one dependency at its current version would write. Writes nothing."""
    source = source_of(args)
    dependency = _dependency_of(args, source)
    return {"source": source, "plan": _staged(ctx, source, dependency, args.get("expected"))}


def pin(ctx: AdapterContext, args: Mapping[str, object]) -> dict:
    """Pin the dependency exactly as reviewed (079's guarded write), or answer that it already is."""
    source = source_of(args)
    dependency = _dependency_of(args, source)
    version, expected = args.get("version"), args.get("expected")
    if type(version) is not str or type(expected) is not str:
        raise ApiRefused("review_required", "name the version and the manifest etag that were reviewed")
    try:
        stored = ctx.store.read_source(source)
        declared = declared_dependencies(stored)
    except (RecordNotFound, StoreError) as error:
        raise ApiRefused("missing_source", f"{source} cannot be read: {error}") from None
    except SourceUpgradeError as error:
        raise refused(error, "upgrade_refused") from None
    if stored.etag != expected:
        if any(item.source == dependency and item.version == version for item in declared):
            return {"source": source, "done": {"already_applied": True, "version": version},
                    "dependencies": _dependencies(ctx, source)}
        raise ApiRefused("changed", "the source's manifest changed since the review; nothing was written")
    if source_version(ctx.store.load(), dependency) != version:
        raise ApiRefused("changed", "the dependency changed since the review, so its version is another; nothing was "
                                    "written. Review the pin again")
    try:
        pin_dependency(ctx.store, ctx.handle, source, dependency, expected=expected)
    except SourceUpgradeError as error:
        raise refused(error, "upgrade_refused") from None
    return {"source": source, "done": {"already_applied": False, "version": version},
            "dependencies": _dependencies(ctx, source)}


__all__ = ["APPLY_UNAVAILABLE", "UPDATE_UNAVAILABLE", "pin", "pin_plan", "upgrades"]
