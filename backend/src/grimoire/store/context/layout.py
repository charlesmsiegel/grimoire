"""The user's prompt layout: which sections render, in what order, under what
inspector label (#29).

The catalog is `context.assemble.SECTIONS` and stays there. This module holds
the stored OVERRIDES and the merge, and it takes the catalog as an argument
rather than importing it — `assemble` imports this, so reaching back would
close a cycle the import guard exists to catch.

What is overridable, and what is not
------------------------------------
Order, presence, and the inspector's label. Not the tier, and not the three
render selectors.

The tier is `pack.py`'s business, and it documents at length why `RECALLED`
sits below `ARCHIVE`: semantic recall promises to be purely additive, and
within a tier the largest section is dropped first, so sharing a tier let
recalled lore evict the Earlier-scenes section — swapping context the prompt
already had for context it never did. A user-editable tier is a control whose
only function is to break that promise. Prompt order and drop order are two
axes, and only the first is a preference.

`except_opener` is the same kind of thing rather than a taste: the opener is
streamed unpersisted into a box the reader adopts by hand, so the
machine-readable tracker block must not render into it, and there is no reply
after it to strip it from.

The label is the INSPECTOR's row name and never reaches the model — each
section template emits its own `# Heading`. Editing the text a section sends is
already possible and always was: `prompts.py` loads `templates/` from disk with
auto-reload precisely so prompts are editable without touching code. Threading
a heading through all thirty templates would also put a user-typed string
inside a contract other things depend on — `evals/run.py` requires the budget,
reply-format and roll-protocol sections verbatim in the assembled prompt.

The upgrade rule
----------------
A layout saved today does not know about a section a later version ships.
Appending the strangers would put a new Response format block below everything
— the one place it must not go — so a catalog section the layout never
mentioned is inserted after its nearest preceding catalog neighbour that
survived the merge, at the front when it has none. The mirror rule is that an
id the catalog no longer has is ignored, which retires a removed section with
no migration.

Off by default, and off is byte-identical: `apply` hands back the catalog
untouched, so an install that never opens the editor sends what it always sent.
Same discipline as `context_budget: 0` and `semantic_recall_depth: 0`.
"""

from __future__ import annotations

import json

from .. import atomic, config, locks
from ..paths import ensure_home, home

#: Longest stored label. A row name, not prose — and the cap is also what stops
#: a hand-edited file from putting a paragraph in the inspector's rail.
MAX_LABEL = 60


def _path():
    return home() / "prompt_layout.json"


def _clean_label(entry: dict, default: str) -> str:
    label = entry.get("label")
    if isinstance(label, str) and label.strip():
        return label.strip()[:MAX_LABEL]
    return default


#: Section ids that were RENAMED, old -> new. A stored layout naming the old id
#: is rewritten before the merge sees it.
_RENAMED = {"message_examples": "voice_examples"}


def _migrate(stored: list) -> list:
    """`stored` with renamed section ids rewritten, position and `enabled` kept.

    The ordinary upgrade rule retires an unknown id silently, which is right for
    a section that was REMOVED and wrong for one that was renamed: it would
    discard a stored `enabled: false`, and the section would come back switched
    on. That is a change to what the model receives, not a preference to
    re-toggle.

    Applied at the head of BOTH `merge` and `describe`, which share `_ordered`.
    Migrating on one path only would let the renderer honour the disable while
    the editor showed the new section enabled -- and reversed the reader's
    choice the moment they saved.

    The stored LABEL is dropped with the rename. A label is the reader's name
    for a section whose meaning has now narrowed, so carrying it forward would
    caption the new section with a description of the old one.

    A read-time alias, not a persisted rewrite: nothing here writes the file.
    When both ids are present the NEW one wins -- it is what the current editor
    produced, so it is the more recent statement of intent.

    Returns `[]` for anything that is not a list, because `merge` and
    `describe` promise a malformed file merges as empty rather than raising,
    and running ahead of `_ordered` moves that promise here.
    """
    if not isinstance(stored, list):
        return []
    have = {e.get("id") for e in stored if isinstance(e, dict)}
    out, seen = [], set()
    for entry in stored:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if sid in _RENAMED:
            new_id = _RENAMED[sid]
            if new_id in have:
                continue          # the newer entry is authoritative
            entry = {k: v for k, v in entry.items() if k != "label"}
            entry["id"] = new_id
            sid = new_id
        if sid in seen:
            continue
        seen.add(sid)
        out.append(entry)
    return out


def _ordered(catalog: list, stored: list) -> list[tuple]:
    """`(section, enabled)` for every catalog section, in render order.

    The shared spine of `merge` (which drops the disabled ones) and `describe`
    (which shows them, switched off, so there is a way back on). One
    implementation, because two would be free to disagree about where a section
    sits — and the editor claiming an order the renderer does not use is
    exactly the inspector/prompt split `SECTIONS` was restructured to remove.
    """
    by_id = {s.id: s for s in catalog}
    order = [s.id for s in catalog]
    out: list[tuple] = []
    seen: set[str] = set()
    for entry in stored:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if not isinstance(sid, str) or sid not in by_id or sid in seen:
            continue
        seen.add(sid)
        out.append((by_id[sid]._replace(label=_clean_label(entry, by_id[sid].label)),
                    entry.get("enabled") is not False))

    # Catalog sections the layout never mentioned, walked in catalog order so
    # two consecutive newcomers keep their relative order: each is findable as
    # the next one's predecessor once inserted.
    #
    # The anchor search reads `out`, which holds the DISABLED sections too, so
    # a newcomer lands beside its catalog neighbour whether that neighbour is
    # switched on or off. Searching only the enabled ones would make switching
    # a section off silently move the next one up.
    for sid in [s for s in order if s not in seen]:
        idx = order.index(sid)
        pos = 0
        for prev in reversed(order[:idx]):
            at = next((n for n, (s, _on) in enumerate(out) if s.id == prev), None)
            if at is not None:
                pos = at + 1
                break
        out.insert(pos, (by_id[sid], True))
    return out


def merge(catalog: list, stored: list) -> list:
    """The catalog, reordered / filtered / relabelled by `stored`. Pure.

    Defensive throughout: `stored` comes off disk and may be anything. A
    malformed entry is skipped on its own and a malformed file merges as empty,
    because the alternative — raising — takes scene generation down over a
    preference.

    A newcomer anchors to its catalog neighbour wherever the layout PUT that
    neighbour, switched on or off — `_ordered` holds the disabled ones, so they
    can still anchor. That is the property worth having: toggling a section off
    must not also move a different section, and it does not. Anchoring to
    survivors only would drag every newcomer up the message each time the
    reader switched something off above it.
    """
    stored = _migrate(stored)
    return [s for s, on in _ordered(catalog, stored) if on]


def describe(catalog: list, stored: list) -> list[dict]:
    """Every catalog section as the editor shows it: `{id, label,
    default_label, tier, enabled}`, in render order, disabled ones included.

    Merged rather than raw, so a section this version added appears at its
    author's position instead of being invisible until the reader saves — and
    a switched-off one appears at all, or there would be no way back on.

    `label` is the STORED override and is `""` when there is none — NOT the
    effective label, which is what `merge` computes and `default_label` already
    carries. The difference is not cosmetic: the editor binds its input to
    `label` and placeholders it with `default_label`, so returning the
    effective one would refill every blank input with the default, and the next
    save would pin all thirty sections to labels the reader never typed. A
    pinned label then survives a release that renames the section.
    """
    stored = _migrate(stored)
    defaults = {s.id: s.label for s in catalog}
    #: The overrides as stored, so a blank stays blank. `_ordered` deliberately
    #: does not carry this: it answers "what renders", and merge wants the
    #: label resolved.
    #:
    #: FIRST occurrence wins, because that is the one `_ordered` keeps. A
    #: last-wins dict would let a hand-edited file with a repeated id show one
    #: label in the editor and render another — the two functions reading the
    #: same file and disagreeing about it. `sanitize` already dedupes on the
    #: way in, so this only bites a file written by hand, which is exactly the
    #: file that gets no other protection.
    overrides: dict[str, str] = {}
    for e in stored:
        if isinstance(e, dict) and isinstance(e.get("id"), str):
            overrides.setdefault(e["id"], e.get("label", ""))
    return [{"id": s.id, "label": overrides.get(s.id, "") or "",
             "default_label": defaults[s.id], "tier": s.tier, "enabled": on}
            for s, on in _ordered(catalog, stored)]


def sanitize(entries) -> list[dict]:
    """The write path's shape check: `[{"id", "label", "enabled"}]`, ids unique.

    Ids are NOT checked against the catalog. An id this version does not know
    is kept on write and ignored on read, so saving a layout from a build one
    version behind cannot silently delete the newer build's sections from the
    file.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("id")
        if not isinstance(sid, str) or not sid.strip() or sid in seen:
            continue
        seen.add(sid)
        label = entry.get("label")
        out.append({"id": sid,
                    "label": label.strip()[:MAX_LABEL] if isinstance(label, str) else "",
                    "enabled": entry.get("enabled") is not False})
    return out


def enabled() -> bool:
    """Whether the stored layout is applied.

    Turning it off KEEPS the file. It is a bypass, not a delete, so a reader
    can A/B their own ordering against the default without rebuilding it.
    """
    return config.read_config().get("prompt_layout_enabled") == "on"


def read_layout() -> list[dict]:
    """The stored entries, sanitized. Never raises.

    Every failure — no file, a truncated one, a hand-edit that made it a list
    of strings, an unreadable one — is the same answer: no layout, which means
    the catalog. A preference must not be able to take a scene's generation
    down with it, which is the posture `turnstate.py` takes for the same reason.
    """
    ensure_home()
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    sections = raw.get("sections")
    return sanitize(sections) if isinstance(sections, list) else []


def write_layout(entries) -> list[dict]:
    """Replace the stored layout. Returns what was stored.

    Under `config_lock` and through `atomic`: the file is rewritten whole, so
    two unlocked read-modify-writes lose one of them, and it belongs to the
    same global-settings lock domain `config.md` sits in.
    """
    clean = sanitize(entries)
    ensure_home()
    with locks.config_lock():
        atomic.write_text(_path(), json.dumps({"sections": clean}, indent=2) + "\n")
    return clean


def apply(catalog: list) -> list:
    """The section list to render: the catalog, or the merge when the toggle is
    on. One `read_config` and at most one small file read per call.

    Per CALL, not per assemble pass, which is what this used to claim:
    `_assemble` asks as well, through `_section_on`, so that a section whose
    data is expensive to gather can be skipped when the reader has switched it
    off. Two small reads per turn rather than one, and the alternative --
    threading the resolved list from `_assemble` into `_render_sections` --
    changes four signatures to save a `stat`."""
    return merge(catalog, read_layout()) if enabled() else list(catalog)
