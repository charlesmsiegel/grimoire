"""Retirement: the legacy settings layout, persisted away (inference slice I).

The migration (`migrate`) moved a store to format 2 and left the legacy keys
in place, frozen, for older builds; the planner (`legacy_plan`) went on
reading the GLM reasoning effort a legacy connection carried, in memory, so
the wire did not change. Retirement persists what the planner plans and then
removes what it read from, so nothing reads the legacy layout again. It is the
last stage of `migrate.ensure`, run after the marker on every `ensure` of a
current store, and this module is its writes and its accounting; the pass
itself -- the archive and the order -- is `migrate._retire`'s (ruling 6):

0. **The archive.** `pass_plan` plans every unit of the pass before anything
   is written, and `needs_archive` says whether any of them deletes or
   replaces a stored value. When one does, `migrate` takes (or reuses) a
   `pre-retirement-grimoire-<stamp>.zip` before the pass's first write of any
   kind; no archive, no write.
1. **Global** (`retire_global`): in one hold of `llm_connections.LOCK` --
   `config_lock` -- the derived presets the plan names
   (`sampler_presets.put_derived`), then ONE `config.md` write
   (`config.retire_write`): each repointed slot, every legacy key deleted, and
   the retirement marker.
2. **Each campaign** (`retire_campaign`), under the caller's
   `campaign_lock_nowait`: the marker re-read inside the hold, an unmarked
   campaign migrated in the same write (`legacy_plan.campaign_plan` maps it as
   `migrate.campaign` does), its derived presets, then ONE `campaign.md` write
   holding the repoint, the deletion of its `route_<k>` keys and both markers,
   and its write token bumped. A newer build's campaign is never touched.
3. **The strip** (`strip_connection`), once `config.md` and every campaign
   read, are marked and retired, and every connection reads: per connection,
   in one hold of `llm_connections.LOCK`, the precondition re-checked, a
   `fact_not_carried` note for any model behaviour its model's facts do not
   state (N9), its non-empty legacy model fields recorded in the retirement
   record (`retired`), and the file rewritten without them, `rev` kept.

Each scope's notes -- what no preset can carry -- are recorded in the
retirement record in the same hold as its write, so `/models` keeps showing
them once the planner stops planning that scope.

A scope that already carries the marker and holds a legacy key again -- an
older build wrote it back (N5) -- gets a deletion-only write: nothing is
derived, and nothing is read from the key.

**Only a non-empty legacy value is work** (N3). A `config.md` this build or a
C-H build creates holds every legacy key as "" (`config.read_config`'s
defaults); an empty or whitespace value is an absent key wherever retirement
counts work -- `left` never names it, `needs_archive` never counts it, and no
write is made for it alone -- and a write made anyway drops it with the rest.

**Fail closed.** Every read that feeds a write is strict: `config.md` and each
`campaign.md` through `frontmatter.read_record` (a file that holds no record
raises `RecordUnreadableError`; `config.md` must hold its format marker), each
connection through `legacy_plan.lookup(mode="retire")` (a file that is there
but cannot be read raises `ConnectionUnreadableError`, never "absent"), and
each sampler preset through `sampler_presets.read_preset_strict`. Planning
comes before any write, so a scope whose plan raises writes nothing -- not a
preset, not a marker -- and is left for the next start. `left` is the
fail-soft reader: it never raises.

**What a write keeps.** The strip rewrites a connection's raw frontmatter
minus its legacy fields, so every `rev` is kept; a campaign's `updated` stamp is left alone (an upgrade is not
something that happened in the campaign). Every value is derived
deterministically, so two devices retiring one synced store write the same
bytes (the write token excepted: it is unique by design).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from .. import (
    atomic,
    config,
    frontmatter,
    llm_connections,
    locks,
    paths,
    revision,
    routing,
    sampler_presets,
)
from .. import inference_keys as keys
from ..campaigns import paths as campaign_paths
from ..campaigns import read as campaign_read
from . import facts, legacy_plan, retired

#: The retirement marker (ruling 15), "1" once a scope is retired.
RETIRED_KEY = keys.RETIRED_KEY
#: `config.md`'s legacy keys: the active, fallback and embeddings choices and
#: every legacy route's `route_<k>` (which is the global `route_*` set).
LEGACY_GLOBAL_KEYS: frozenset[str] = frozenset(keys.LEGACY_GLOBAL_KEYS)
#: A `campaign.md`'s legacy keys: its `route_<k>` choices. Its `preset_<k>`
#: keys are the same keys in both layouts and stay.
LEGACY_CAMPAIGN_KEYS: frozenset[str] = frozenset(routing.CONFIG_KEYS)

#: What a read may raise that `left` reports rather than raises: a file that
#: is there but cannot be read (`RecordUnreadableError`,
#: `ConnectionUnreadableError` and the read's own errors are all OSErrors), a
#: malformed value, and a campaign id that names no campaign directory.
_UNREADABLE: tuple[type[BaseException], ...] = (OSError, UnicodeDecodeError, ValueError,
                                                campaign_paths.CampaignNotFound)
#: What a plan or a write may raise that stops only its own unit: those, a
#: lock that stayed held, and a value the writers refuse (a ValueError).
UNIT_ERRORS: tuple[type[BaseException], ...] = (*_UNREADABLE, locks.StoreBusy)
#: What stops a whole pass before it is planned: those, on `config.md`, and a
#: newer build's marker on it.
_CONFIG_STOPS: tuple[type[BaseException], ...] = (*UNIT_ERRORS, config.NewerFormatError)


class ArchiveNeededError(Exception):
    """A unit whose plan, made again inside its own hold, now deletes or
    replaces a stored value although the pass took no archive (R3-1): it
    writes nothing and is left for the next run, which takes the archive
    first."""


def _filled(value: object) -> bool:
    return bool(str(value or "").strip())


def _retired(meta: Mapping) -> bool:
    return str(meta.get(RETIRED_KEY, "") or "").strip() == "1"


def _config_path() -> Path:
    return paths.home() / "config.md"


# ---- one scope's change ----
class Change(NamedTuple):
    """What retirement would do to one scope: its plan, and the frontmatter
    before and after its one write."""

    plan: legacy_plan.Plan
    before: dict[str, str]
    after: dict[str, str]
    #: The scope's legacy keys, whose empty values are not work.
    legacy: frozenset[str]

    @property
    def work(self) -> bool:
        """Whether the write changes anything but empty legacy keys."""
        kept = {k: v for k, v in self.before.items()
                if not (k in self.legacy and not _filled(v))}
        return kept != self.after

    @property
    def replaces(self) -> bool:
        """Whether the write deletes or replaces a stored non-empty value --
        what needs the archive. A preset file is only ever added
        (`put_derived`), and a marker is only ever added."""
        return any(_filled(v) and self.after.get(k) != v for k, v in self.before.items())


def _global_change(cfg: dict[str, str], lookup: legacy_plan.Lookup,
                   presets: legacy_plan.PresetRead) -> Change:
    """`config.md`'s change: at format 2 the plan maps nothing, so it is the
    derivation's repoint (none on a retired `config.md`), every legacy key
    deleted, and the marker."""
    plan = legacy_plan.global_plan(cfg, lookup, presets)
    after = {k: v for k, v in cfg.items() if k not in LEGACY_GLOBAL_KEYS}
    after.update(plan.mapped)
    after.update(plan.repoint)
    after[RETIRED_KEY] = "1"
    return Change(plan, dict(cfg), after, LEGACY_GLOBAL_KEYS)


def _campaign_change(meta: dict[str, str], cfg: dict[str, str], cid: str,
                     lookup: legacy_plan.Lookup,
                     presets: legacy_plan.PresetRead) -> Change | None:
    """Campaign `cid`'s change, or None for a campaign a newer build marked.

    Planned as `legacy_plan.campaign_plan` plans it: an unmarked campaign is
    mapped as the migration maps it (step 8's work, in the same write) and
    derived; a marked one derived; a retired one nothing. Then its `route_<k>`
    keys deleted and both markers set.

    The global view the campaign's notes are judged against is `config.md` as
    it stands. Below format 2 that would not do -- the overlay hands the
    planner the global settings as format 2 sees them -- but retirement runs
    on a current `config.md` only, whose plan maps nothing, and the derived
    repoint it would lay over the view names only selection presets
    (`role_*_preset`, `use_<k>_preset`), which the notes never read: they
    read each route's route-level preset (`preset_<k>`) and the providers and
    models the cascade chooses. So the notes are the overlay's, and a
    connection the global plan alone cannot read does not stop a campaign
    that never names it."""
    if keys.is_newer(meta):
        return None
    plan = legacy_plan.campaign_plan(meta, glob=cfg, global_current=True, lookup=lookup,
                                     presets=presets, cid=cid)
    after = {k: v for k, v in meta.items() if k not in LEGACY_CAMPAIGN_KEYS}
    after.update(plan.mapped)
    after.update(plan.repoint)
    after[keys.FORMAT_KEY] = keys.CURRENT_FORMAT
    after[RETIRED_KEY] = "1"
    return Change(plan, dict(meta), after, LEGACY_CAMPAIGN_KEYS)


def _current_config(path: Path) -> dict[str, str]:
    """`config.md`, read strictly, at the current format; raising
    `RecordUnreadableError` (no record, no marker), `config.NewerFormatError`
    (a newer build's) or `ValueError` (not yet migrated) otherwise."""
    cfg, _ = frontmatter.read_record(path, "config.md", require=keys.FORMAT_KEY)
    if keys.is_newer(cfg):
        raise config.NewerFormatError("a newer build wrote this store's model settings")
    if not keys.is_current(cfg):
        raise ValueError("config.md is not at the current settings format yet")
    return cfg


# ---- the writes ----
def retire_global(lookup: legacy_plan.Lookup, *,
                  archived: bool = True) -> tuple[retired.Note, ...]:
    """Retire `config.md`: its derived presets, then one write repointing its
    GLM slots, deleting every legacy key and stamping `RETIRED_KEY`. Returns
    the global plan's notes (what was not carried over), () when there was
    nothing to do. The notes are recorded in the retirement record
    (`retired.record_notes`) before anything else is written -- the derived
    presets, then the write -- so what was not carried over is on `/models`
    from the moment the scope stops being planned, and a record that cannot
    be read leaves the scope untouched.

    All of it in one hold of `llm_connections.LOCK`, which is `config_lock`:
    `config.retire_write`'s `format_hold` and each `put_derived`'s re-enter
    it, so the read the plan is made from, the presets and the write are one
    span no other model-settings writer can land inside.

    A retired `config.md` that holds a legacy key again gets the deletion
    alone (N5). `archived` is whether the pass took an archive: when it did
    not and this plan, made in the hold, would delete or replace a stored
    value, `ArchiveNeededError` is raised and nothing is written (R3-1). A
    connection the plan cannot read (`ConnectionUnreadableError`), a preset it
    cannot read and a `config.md` that holds no record raise before any
    write."""
    with llm_connections.LOCK:
        change = _global_change(_current_config(_config_path()), lookup,
                                sampler_presets.read_preset_strict)
        if not change.work:
            return ()
        if change.replaces and not archived:
            raise ArchiveNeededError("config.md")
        # The notes first: they read the record strictly, and a record that
        # cannot be read must leave the scope with nothing written -- not
        # even a derived preset (review M-1). `put_derived` and the notes are
        # both idempotent by id, so a run killed between them resumes clean.
        retired.record_notes(change.plan.notes)
        for made in change.plan.presets:
            sampler_presets.put_derived(made.id, made.name, made.params)
        config.retire_write({**change.plan.mapped, **change.plan.repoint, RETIRED_KEY: "1"},
                            drop=LEGACY_GLOBAL_KEYS)
    return change.plan.notes


def retire_campaign(cid: str, lookup: legacy_plan.Lookup, *,
                    archived: bool = True) -> tuple[retired.Note, ...] | None:
    """Retire campaign `cid`; returns its notes (() when there was nothing to
    do), or None for a campaign a newer build marked, which is never written.

    The CALLER holds `campaign_lock(cid)` -- `migrate`, under its no-wait
    hold -- and this refuses with RuntimeError otherwise, as
    `migrate.campaign` does. Inside it, and inside one `config.format_hold()`
    (so a newer build switching the store meanwhile raises
    `config.NewerFormatError` and nothing is written), the campaign's record is
    read strictly and its marker judged there:

    - unmarked: migrated and derived in the same write (`_campaign_change`);
    - marked: derived;
    - retired: its `route_<k>` keys deleted, if one holds a value again (N5).

    Its notes into the retirement record first, then its derived presets
    under `config_lock` (the hold above, N19), then one atomic `campaign.md` write with `updated` left alone, then the write
    token. `archived` is `retire_global`'s."""
    if not locks.holds_campaign(cid):
        raise RuntimeError(f"retire.retire_campaign({cid!r}) needs the caller to hold its lock")
    mp = campaign_paths.campaign_meta_path(cid)
    with config.format_hold():
        cfg = _current_config(_config_path())
        meta, body = frontmatter.read_record(mp, f"campaign {cid}'s campaign.md")
        change = _campaign_change(meta, cfg, cid, lookup, sampler_presets.read_preset_strict)
        if change is None:
            return None
        if not change.work:
            return ()
        if change.replaces and not archived:
            raise ArchiveNeededError(f"campaign {cid}")
        # The notes first: they read the record strictly, and a record that
        # cannot be read must leave the scope with nothing written -- not
        # even a derived preset (review M-1). `put_derived` and the notes are
        # both idempotent by id, so a run killed between them resumes clean.
        retired.record_notes(change.plan.notes)
        for made in change.plan.presets:
            sampler_presets.put_derived(made.id, made.name, made.params)
        atomic.write_text(mp, frontmatter.dump_frontmatter(change.after, body))
    revision.bump(cid)
    return change.plan.notes


# ---- the pass, planned whole before any write ----
class Unit(NamedTuple):
    """One unit of a pass: a scope -- `GLOBAL_SCOPE`, or a campaign's scope
    (`legacy_plan.campaign_scope`) -- or one connection's strip
    (`connection:<id>`)."""

    scope: str
    #: The campaign's id; "" for `config.md` and for a strip.
    cid: str
    #: Whether its write changes anything (`Change.work`).
    work: bool
    #: Whether its write deletes or replaces a stored non-empty value.
    replaces: bool
    notes: tuple[retired.Note, ...]
    #: The connection a strip unit strips; "" for a scope.
    conn_id: str = ""


class PassPlan(NamedTuple):
    """Every unit of a pass that could be planned, and why each other one
    could not (`scope: reason`)."""

    units: tuple[Unit, ...]
    dropped: tuple[str, ...]


def campaign_ids() -> list[str]:
    """Every campaign on disk whose id is safe to name, as the migration
    lists them."""
    return [cid for cid, _name, _world in campaign_read.world_refs() if paths.safe_id(cid)]


def _with_virtual(made: Mapping[str, legacy_plan.Derived]) -> legacy_plan.PresetRead:
    """The strict preset reader, answering first with the global plan's
    derived presets -- which the pass writes before any campaign's, so a
    campaign's plan reads them as its in-hold plan will (the overlay's rule)."""
    def read(pid: str) -> dict | None:
        got = made.get(pid)
        if got is not None:
            return {"id": got.id, "name": got.name, "params": dict(got.params),
                    "notes": "", "source": ""}
        return sampler_presets.read_preset_strict(pid)
    return read


def pass_plan(lookup: legacy_plan.Lookup) -> PassPlan:
    """The whole pass, planned before any write (R2-3): `config.md`, then
    each campaign, each through `lookup` (`legacy_plan.lookup(mode="retire")`,
    memoised for the pass). Writes nothing.

    A unit whose plan raises is dropped with its reason and the others are
    still planned: a `ConnectionUnreadableError` drops the units that read
    that connection, not the pass (R3-1). A `config.md` that cannot be read,
    or is not current, drops every unit: each campaign's write is judged in a
    hold that reads it. A campaign a newer build marked is not a unit at all
    (`left` names it).

    Then the strip's units: each connection whose file holds a non-empty
    legacy model field, a deletion of stored values. Planned only when the
    strip can follow this pass -- no unit was dropped, no campaign is a newer
    build's and every connection file reads -- since otherwise its
    precondition cannot hold by the pass's end (`strip_connection` checks it
    again in its own hold either way)."""
    try:
        cfg = _current_config(_config_path())
    except _CONFIG_STOPS as exc:
        return PassPlan((), (f"config.md: {exc}",))
    units: list[Unit] = []
    dropped: list[str] = []
    virtual: dict[str, legacy_plan.Derived] = {}
    try:
        g = _global_change(cfg, lookup, sampler_presets.read_preset_strict)
    except UNIT_ERRORS as exc:
        dropped.append(f"config.md: {exc}")
    else:
        units.append(Unit(legacy_plan.GLOBAL_SCOPE, "", g.work, g.replaces, g.plan.notes))
        virtual = {made.id: made for made in g.plan.presets}
    newer = False
    for cid in campaign_ids():
        try:
            meta, _ = frontmatter.read_record(campaign_paths.campaign_meta_path(cid),
                                              f"campaign {cid}'s campaign.md")
            change = _campaign_change(meta, cfg, cid, lookup, _with_virtual(virtual))
        except UNIT_ERRORS as exc:
            dropped.append(f"campaign {cid}: {exc}")
            continue
        if change is None:
            newer = True
            continue
        units.append(Unit(legacy_plan.campaign_scope(cid), cid, change.work,
                          change.replaces, change.plan.notes))
    if not dropped and not newer and not llm_connections.unreadable_connections():
        units.extend(Unit(f"connection:{conn_id}", "", True, True, (), conn_id)
                     for conn_id in sorted(llm_connections.legacy_fields_on_disk()))
    return PassPlan(tuple(units), tuple(dropped))


def needs_archive(plan: PassPlan) -> bool:
    """Whether any unit of the pass deletes or replaces a stored non-empty
    value: a legacy key, a connection's legacy model field (a strip unit), a
    repointed preset key. False only when the
    whole pass adds markers (and derived presets nothing yet names), plus the
    deletion of keys whose values are empty (N3, R2-3)."""
    return any(unit.replaces for unit in plan.units)


# ---- what is left ----
def left() -> tuple[str, ...]:
    """What retirement has still to do, one human-readable item each, for
    `migrate.status()`'s `retirement.left`. Fail-soft: reads only, never
    seeds or writes, never raises.

    - `config.md` not retired, not readable, or retired but holding a
      non-empty legacy key (N5);
    - a campaign not migrated, not retired, not readable, written by a newer
      build (which holds the strip, N18), or retired but holding a non-empty
      `route_<k>` key (N5);
    - a connection whose file cannot be read, or that holds a non-empty
      legacy model field (`llm_connections.MODEL_FIELDS`) -- the strip's,
      including one an older build wrote back after it (N5)."""
    return (*_config_left(), *_campaigns_left(), *_connections_left())


def _config_left() -> list[str]:
    try:
        cfg, _ = frontmatter.read_record(_config_path(), "config.md")
    except FileNotFoundError:
        return []
    except _UNREADABLE as exc:
        return [f"config.md: {exc}"]
    if keys.is_newer(cfg):
        return ["config.md: written by a newer build"]
    if not (keys.is_current(cfg) and _retired(cfg)):
        return ["config.md: not retired yet"]
    stray = sorted(k for k in LEGACY_GLOBAL_KEYS if _filled(cfg.get(k)))
    if stray:
        return [f"config.md: holds legacy settings again ({', '.join(stray)})"]
    return []


def _campaigns_left() -> list[str]:
    try:
        cids = campaign_ids()
    except _UNREADABLE as exc:
        return [f"campaigns: {exc}"]
    out: list[str] = []
    for cid in cids:
        try:
            meta, _ = frontmatter.read_record(campaign_paths.campaign_meta_path(cid),
                                              f"campaign {cid}'s campaign.md")
        except _UNREADABLE as exc:
            out.append(f"campaign {cid}: {exc}")
            continue
        if keys.is_newer(meta):
            out.append(f"campaign {cid}: written by a newer build; the strip waits for it")
        elif not keys.is_current(meta):
            out.append(f"campaign {cid}: not migrated yet")
        elif not _retired(meta):
            out.append(f"campaign {cid}: not retired yet")
        elif stray := sorted(k for k in LEGACY_CAMPAIGN_KEYS if _filled(meta.get(k))):
            out.append(f"campaign {cid}: holds legacy settings again ({', '.join(stray)})")
    return out


def _connections_left() -> list[str]:
    try:
        out = list(llm_connections.unreadable_connections().values())
        out += [f"connection {conn_id}: holds legacy model settings ({', '.join(held)})"
                for conn_id, held in sorted(llm_connections.legacy_fields_on_disk().items())]
        return out
    except _UNREADABLE as exc:
        return [f"connections: {exc}"]


# ---- the strip (ruling 6(c)) ----
#: The legacy model fields the C migration copied into a model's facts, and
#: how a note names each.
_FACT_LABELS: dict[str, str] = {"vision": "image input", "prefill": "prefill",
                                "post_process": "post-processing"}


def strip_blocked() -> str:
    """Why the strip may not run now; "" when it may. Its precondition:
    `config.md` reads, is current and retired; every campaign reads, is
    marked current and retired (a newer build's holds it, N18); and every
    connection file reads. Reads each `campaign.md` WITHOUT its campaign lock:
    the strip checks this inside `config_lock`, which is never held around a
    campaign lock (`locks.config_lock`)."""
    try:
        cfg = _current_config(_config_path())
    except _CONFIG_STOPS as exc:
        return f"config.md: {exc}"
    if not _retired(cfg):
        return "config.md is not retired yet"
    for cid in campaign_ids():
        try:
            meta, _ = frontmatter.read_record(campaign_paths.campaign_meta_path(cid),
                                              f"campaign {cid}'s campaign.md")
        except _UNREADABLE as exc:
            return f"campaign {cid}: {exc}"
        if keys.is_newer(meta):
            return f"campaign {cid} was written by a newer build"
        if not (keys.is_current(meta) and _retired(meta)):
            return f"campaign {cid} is not retired yet"
    unreadable = llm_connections.unreadable_connections()
    if unreadable:
        return next(iter(unreadable.values()))
    return ""


def _fact_notes(raw: Mapping) -> tuple[retired.Note, ...]:
    """N9: each `vision`, `prefill` or `post_process` the connection states
    (`legacy_plan.stated`, what differs from the defaults) that its model's
    facts state nothing for -- the C migration's copy failed, or was taken
    back -- as a `fact_not_carried` note. The facts are read strictly
    (`facts.FactsUnreadableError` stops this connection's strip) and never
    written here: the user's word is never overwritten, and a stated value
    that differs is theirs."""
    stated = legacy_plan.stated(raw)
    if not stated:
        return ()
    conn_id = str(raw["id"])
    model = facts.model_of(dict(raw))
    known = facts.of(conn_id, model, str(raw.get("rev") or ""), strict=True)
    out: list[retired.Note] = []
    for field, value in stated.items():
        unstated = known["prefill"] is None if field == "prefill" else not known[field]
        if not unstated:
            continue
        shown = "on" if value is True else str(value)
        name = str(raw.get("name") or conn_id)
        text = (f"The provider “{name}” had {_FACT_LABELS[field]} set to “{shown}” for "
                f"{f'the model “{model}”' if model else 'its model'}, and that model's "
                "settings do not say so — this was not carried over.")
        kind = "fact_not_carried"
        out.append(retired.Note(retired.note_id(legacy_plan.GLOBAL_SCOPE, field, conn_id,
                                                "", kind),
                                legacy_plan.GLOBAL_SCOPE, field, conn_id, "", kind, text))
    return tuple(out)


def strip_connection(conn_id: str, *, archived: bool = True) -> tuple[retired.Note, ...] | None:
    """Strip one connection's legacy model fields for good, in one hold of
    `llm_connections.LOCK` (`config_lock`), so a connection edit waits rather
    than being overwritten (N4). Returns the notes it recorded, or None when
    the strip's precondition does not hold (`strip_blocked`, re-checked in
    this hold) -- nothing written.

    In the hold: the precondition; the facts check (`_fact_notes`, N9), its
    notes recorded BEFORE the field goes; then
    `llm_connections.strip_model_fields`, which records the fields in the
    retirement record and rewrites the file without them, `rev` kept. A file
    that cannot be read -- the connection, its facts, the record -- raises
    and nothing is written over it. `archived` is `retire_global`'s:
    `ArchiveNeededError` without one."""
    with llm_connections.LOCK:
        if strip_blocked():
            return None
        if not llm_connections.legacy_fields_on_disk().get(conn_id):
            return ()
        if not archived:
            raise ArchiveNeededError(f"connection {conn_id}")
        raw = llm_connections.read_connection_strict(conn_id)
        if raw is None:
            return ()
        found = _fact_notes(raw)
        retired.record_notes(found)
        llm_connections.strip_model_fields(conn_id, retired.record_fields)
    return found


def strip() -> list[retired.Note]:
    """The strip, whole: `strip_connection` for each connection holding a
    legacy model field, stopping at the first whose precondition fails.
    Returns the notes recorded. For a caller outside a pass (a test, a
    script); the pass runs `strip_connection` unit by unit."""
    notes: list[retired.Note] = []
    for conn_id in sorted(llm_connections.legacy_fields_on_disk()):
        got = strip_connection(conn_id)
        if got is None:
            break
        notes += got
    return notes
