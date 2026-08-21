"""User pins and excludes (#129): the two overrides the context builder is not
allowed to argue with. Stored at <campaign>/pins.json.

    {"<sid>:<kind>:<id>": {...},   # a scene rule, keyed by the scene it is for
     "*:<kind>:<id>":     {"ref": "<kind>:<id>",   # a campaign-wide one
                           "mode": "pin" | "exclude",
                           "scope": "scene" | "campaign",
                           "sid": "<sid>" | "",
                           "ttl_posts": int,       # 0 = no decay
                           "created_posts": int,   # posts in `sid` when written
                           "created": "<iso stamp>"}}

A **pin** forces its target into the assembled prompt: past the keyword rule,
past the owner gate, and past the packer, which may not drop a section a pin is
holding up. An **exclude** keeps it out just as absolutely. Between them they
are the reader's manual override of every automatic selection this codebase
makes -- `context.activate`'s keys and owners, semantic recall, and the tiered
budget packer (`context/pack.py`).

**Why it survives the packer.** The tiers answer "what gives way when the
context does not fit"; a pin answers "not this". Those are different questions,
so a pin is not a fifth tier: `pack.pack` skips a pinned section entirely, at
every tier, and the section keeps whatever tier it was declared with so the
inspector still says what kind of content it is. #126's packer promises the
reader visibility into what was cut; this is the primitive that lets them
overrule it.

**TTL is posts, not minutes.** The unit that matters is transcript growth: "keep
this in front of the model for the next three posts" is a thing a reader means,
"for the next three minutes" is not -- a scene can sit untouched for a week.
`ttl_posts` counts posts in the rule's own scene, from the length the transcript
had when it was written, and a rule whose window is spent is *gone*: `active`
stops reporting it, and a campaign-wide rule it was overriding takes effect
again on the next turn.

That is also why a **campaign-scoped rule cannot carry a TTL** and `set_rule`
refuses one. Its posts would have to be counted in some scene, and every
available answer is wrong: the scene it was written in (which the rule is
explicitly not about), or the scene being assembled (so one rule expires at a
different moment in every scene, having never been used in most of them).
Campaign scope means standing until removed.

**Scene beats campaign** for the same ref: the specific rule is the later
decision and the one the reader can see in front of them. So "never in this
campaign, except in this scene" is expressible, and so is its opposite.

**What a pin does not reach.** `secrecy: gm-only` (#49). That level says the
entry is not for the model at all, which is a property of the record rather than
a condition about this turn -- unlike the owner gate and the keyword rule, which
a pin exists to answer. Pinning a gm-only entry is inert, exactly like pinning a
record the campaign has since deleted; see `context.activate`, where the two
rules meet and the order is spelled out.

**What an exclude does not reach.** It governs SELECTION -- which records the
context builder pulls into the prompt -- not the text already written down. An
excluded character's name still appears in the transcript they spoke in, in the
recap of a scene they were in, and in an archived scene recalled by keyword;
"keep this out of the prompt" cannot rewrite history the reader played. Nor does
it reach the prompts other features build for their own purposes (absorb, the
rolling summary, dossiers): those summarize what happened, and a summary that
silently dropped a character would be wrong about the scene rather than tactful
about it.

**This module stores; it does not validate targets.** A ref names a `kind` this
codebase can pin (`KINDS`) and an id, and checking that the id still exists
means reading the campaign's overlay and roster -- which is the caller's, the
same split `scene_ideas` documents. A dangling ref is data: it selects nothing
and costs nothing, and an entry deleted today may be restored tomorrow.

Mutators serialize on `locks.campaign_lock(cid)`: the file is rewritten whole,
so two unlocked read-modify-writes lose one of them.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import atomic, locks, paths
from .campaigns import paths as campaigns_paths

PIN = "pin"
EXCLUDE = "exclude"
MODES = (PIN, EXCLUDE)

SCENE = "scene"
CAMPAIGN = "campaign"
SCOPES = (SCENE, CAMPAIGN)

#: What a rule may name. The world-info kinds `context._world_info` walks, plus
#: the two actor kinds, which reach the prompt through the cast loop instead and
#: so are the half `activate()` never sees.
KINDS = ("characters", "pcs", "lore", "locations", "items", "groups", "creatures")

#: Stands in for a scene id in a campaign-scoped key, which is `<sid>:<ref>`.
#:
#: Two things make that key unambiguous, and both are checked rather than
#: assumed: `paths.safe_id` REFUSES a colon in a scene id (it names an NTFS
#: alternate data stream and a Windows drive-relative path), and entity ids come
#: from `paths.slugify`, which emits neither a colon nor a `*`. So the first
#: colon always separates the scene from the ref, and no real scene id can be
#: mistaken for this marker.
_ANY_SCENE = "*"


def _path(cid: str) -> Path:
    return campaigns_paths.campaign_root(cid) / "pins.json"


def read(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _read_rules(cid: str) -> dict:
    """`read`, refusing a document that is valid JSON of the wrong shape.

    Only the mutators use this, for `scene_ideas._read_ledger`'s reason:
    substituting `{}` for a file holding `[]` would publish an empty rule set
    over whatever the reader really had.
    """
    data = read(cid)
    if not isinstance(data, dict):
        raise ValueError("pins.json does not hold a rule set")
    return data


def _write(cid: str, data: dict) -> None:
    atomic.write_text(_path(cid), json.dumps(data, indent=2, sort_keys=True) + "\n")


def _key(scope: str, sid: str, ref: str) -> str:
    """One key per (scope, scene, target), so setting a rule twice replaces it.

    That is the behaviour a toggle needs -- pin, then exclude, and the reader
    has one rule rather than two contradictory ones -- and it makes re-pinning
    restart the TTL clock, which is what asking for the pin again means.
    """
    return f"{sid if scope == SCENE else _ANY_SCENE}:{ref}"


def split_ref(ref: str) -> tuple[str, str] | None:
    """`"lore:tide-oath"` -> `("lore", "tide-oath")`, or None if it is not a
    pinnable ref. The one place the ref vocabulary is parsed; routes use it to
    answer 400 rather than storing something that can never match."""
    kind, _, entry_id = (ref or "").partition(":")
    if kind not in KINDS or not entry_id.strip():
        return None
    return kind, entry_id.strip()


def _rules(cid: str) -> list[tuple[str, dict]]:
    """Every stored rule that is shaped like one, as (key, record) pairs.

    Tolerant on BOTH counts, and this is the module's whole failure policy: an
    unparseable pins.json or a hand-edited record of the wrong shape yields
    nothing rather than raising, because every reader here sits on the path
    that builds a prompt. A rule set that cannot be read must cost the reader
    their overrides, never their turn.
    """
    try:
        data = read(cid)
    except Exception:  # noqa: BLE001 — unparseable pins.json: no rules, not a broken turn
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for key, rec in data.items():
        if not isinstance(rec, dict):
            continue
        ref = rec.get("ref")
        if not isinstance(ref, str) or split_ref(ref) is None:
            continue
        if rec.get("mode") not in MODES or rec.get("scope") not in SCOPES:
            continue
        out.append((key, rec))
    return out


def _int(value, fallback: int = 0) -> int:
    """A stored count as a non-negative int. pins.json is hand-editable, and a
    `ttl_posts` of `"soon"` must not raise inside the assembler."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else fallback


def _remaining(rec: dict, posts: int) -> int | None:
    """Posts this rule has left, or None when it never decays. <= 0 is spent.

    Capped at the window itself, because the transcript SHRINKS: a retry pops
    the last reply, an undo takes back a turn, and a trimmed scene loses several
    at once. The elapsed count is then negative and the raw arithmetic hands
    back more posts than the rule was ever given — a 3-post pin reporting "5
    posts left" after a reroll. Counting down again from the full window is the
    honest reading of a transcript that went backwards: those posts really did
    stop having happened.
    """
    ttl = _int(rec.get("ttl_posts"))
    if ttl <= 0:
        return None
    return min(ttl, ttl - (posts - _int(rec.get("created_posts"))))


def _live(rec: dict, sid: str, posts: int) -> bool:
    """Whether this rule applies to `sid` at `posts` posts.

    A campaign rule applies everywhere and never decays; a scene rule applies to
    its own scene while its window holds. `posts` is the length of the
    transcript being assembled, so a rule written at post 10 with `ttl_posts=3`
    covers posts 10, 11 and 12.
    """
    if rec["scope"] == CAMPAIGN:
        return True
    if rec.get("sid") != sid:
        return False
    left = _remaining(rec, posts)
    return left is None or left > 0


def _project(rec: dict, posts: int) -> dict:
    return {"ref": rec["ref"], "mode": rec["mode"], "scope": rec["scope"],
            "sid": rec.get("sid") if isinstance(rec.get("sid"), str) else "",
            "ttl_posts": _int(rec.get("ttl_posts")),
            "remaining": _remaining(rec, posts),
            "created": rec.get("created") if isinstance(rec.get("created"), str) else ""}


def records(cid: str, sid: str, posts: int) -> list[dict]:
    """Every rule in force for this scene, this scene's first.

    Spent rules are absent rather than listed as expired: they no longer do
    anything, and a panel that shows them invites the reader to remove a rule
    that is already gone. `remaining` is the countdown (None = standing).
    """
    rows = [_project(rec, posts) for _key, rec in _rules(cid) if _live(rec, sid, posts)]
    rows.sort(key=lambda r: (r["scope"] != SCENE, r["ref"]))
    return rows


def active(cid: str, sid: str, posts: int) -> dict:
    """The two sets the context builder consults: ``{"pinned", "excluded"}``.

    Scene rules override campaign ones for the same ref -- including by being
    the reason a campaign rule does NOT apply, which is why this resolves per
    ref rather than unioning the two scopes.
    """
    modes: dict[str, str] = {}
    for _key, rec in _rules(cid):
        if not _live(rec, sid, posts):
            continue
        if rec["scope"] == CAMPAIGN and rec["ref"] in modes:
            continue  # a scene rule already spoke for this ref
        if rec["scope"] == SCENE or rec["ref"] not in modes:
            modes[rec["ref"]] = rec["mode"]
    return {"pinned": frozenset(r for r, m in modes.items() if m == PIN),
            "excluded": frozenset(r for r, m in modes.items() if m == EXCLUDE)}


def _sweep(data: dict, sid: str, posts: int) -> None:
    """Drop this scene's spent rules, in place.

    Expiry is decided on read, so this is housekeeping rather than semantics --
    it runs when a mutator is already rewriting the file, which is the only
    moment both the post count and a write are in hand. Nothing here is load
    bearing: a rule this never reaches is inert either way.
    """
    for key in [k for k, rec in data.items()
                if isinstance(rec, dict) and rec.get("scope") == SCENE
                and rec.get("sid") == sid and not _live(rec, sid, posts)]:
        del data[key]


def set_rule(cid: str, ref: str, mode: str, scope: str = SCENE, sid: str = "",
             ttl_posts: int = 0, posts: int = 0) -> dict:
    """Pin or exclude `ref`, replacing any rule this scope already had for it.

    `posts` is the transcript length the TTL counts from and `ttl_posts` the
    window (0 = standing). Returns the stored rule, projected the way `records`
    projects it.
    """
    if mode not in MODES:
        raise ValueError(f"unknown pin mode: {mode}")
    if scope not in SCOPES:
        raise ValueError(f"unknown pin scope: {scope}")
    parts = split_ref(ref)
    if parts is None:
        raise ValueError(f"not a pinnable reference: {ref}")
    # Stored in the form the assembler will compare against -- `f"{kind}:{id}"`
    # -- rather than as typed. `split_ref` already tolerates the surrounding
    # whitespace a hand-written or copy-pasted ref arrives with, so accepting
    # `"lore: tide-oath "` and then storing it verbatim filed a rule that
    # validated, listed and matched NOTHING: inert, and indistinguishable in the
    # panel from one that works.
    ref = f"{parts[0]}:{parts[1]}"
    sid = sid.strip()
    if scope == SCENE and not sid:
        raise ValueError("a scene-scoped rule needs a scene")
    ttl_posts = max(int(ttl_posts), 0)
    if scope == CAMPAIGN and ttl_posts:
        # See the module docstring: there is no scene to count the posts in.
        raise ValueError("a campaign-scoped rule cannot carry a post TTL")
    posts = max(int(posts), 0)
    rec = {"ref": ref, "mode": mode, "scope": scope, "sid": sid if scope == SCENE else "",
           "ttl_posts": ttl_posts, "created_posts": posts, "created": paths.now_iso()}
    with locks.campaign_lock(cid):
        data = _read_rules(cid)
        if scope == SCENE:
            _sweep(data, sid, posts)
        data[_key(scope, sid, ref)] = rec
        _write(cid, data)
    return _project(rec, posts)


def remove(cid: str, ref: str, scope: str = SCENE, sid: str = "") -> bool:
    """Drop one rule. False when this scope had none for that ref.

    Scope-precise: removing a scene rule leaves the campaign-wide one standing,
    which is the difference between "not here" and "not anywhere".
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown pin scope: {scope}")
    with locks.campaign_lock(cid):
        data = _read_rules(cid)
        if data.pop(_key(scope, sid.strip(), ref), None) is None:
            return False
        _write(cid, data)
        return True


def drop_scene(cid: str, sid: str) -> None:
    """Forget a deleted scene's rules.

    Scene ids are recycled (`scenes.lifecycle.delete_scene` frees a number for
    the next scene to take), so rules left behind would be adopted by whatever
    scene takes the id next -- silently pinning one scene's lore into another's
    prompt. Campaign-scoped rules are untouched: they were never about this
    scene.
    """
    with locks.campaign_lock(cid):
        try:
            data = _read_rules(cid)
        except (ValueError, OSError, json.JSONDecodeError):
            return  # nothing readable to forget; see repoint_scenes
        keys = [k for k, rec in data.items()
                if isinstance(rec, dict) and rec.get("scope") == SCENE and rec.get("sid") == sid]
        if keys:
            for k in keys:
                del data[k]
            _write(cid, data)


def repoint_records(cid: str, mapping: dict[str, str]) -> None:
    """Follow reclassified records (#119), in both the `ref` field and the key
    it forms. `mapping` is in `<kind>/<id>` form, the one every other ledger
    uses; a pin ref is `<kind>:<id>`, so it is translated here rather than at
    each of this module's callers.

    Both halves, for `repoint_scenes`' reason one level down: the key carries
    the ref, so a rule left keyed by the old one is filed against a record that
    no longer exists and would be handed to the next record to take that slug --
    an *exclude* silently applying to a stranger, which is the direction of
    this file that fails quietly.

    Unreadable and malformed records are stepped over for the same reason as
    well: this runs after the record has already moved, and raising here would
    500 a reclassify that has happened.
    """
    mapping = {old.replace("/", ":", 1): new.replace("/", ":", 1)
               for old, new in mapping.items() if old != new}
    if not mapping:
        return
    with locks.campaign_lock(cid):
        try:
            data = read(cid)
        except Exception:  # noqa: BLE001 — unparseable pins.json: skip this store
            return
        if not isinstance(data, dict):
            return
        moved = {}
        for key, rec in list(data.items()):
            if not isinstance(rec, dict):
                continue
            ref = rec.get("ref")
            if not isinstance(ref, str) or ref not in mapping:
                continue
            rec["ref"] = mapping[ref]
            moved[key] = rec
        if not moved:
            return
        for key, rec in moved.items():
            del data[key]
            scope = rec.get("scope")
            sid = rec.get("sid")
            data[_key(scope if scope == SCENE else CAMPAIGN,
                      sid if isinstance(sid, str) else "", rec["ref"])] = rec
        _write(cid, data)


def repoint_scenes(cid: str, mapping: dict[str, str]) -> None:
    """Follow renamed scene ids, in both the `sid` field and the key it forms.
    Part of the `scene_refs.repoint` fan-out.

    An unreadable file and malformed records are stepped over rather than
    trusted, for the reason `scene_ideas.repoint_scenes` gives: this runs AFTER
    the scene file has been renamed, so raising here 500s the rename and leaves
    every store later in the sweep pointing at an id that no longer exists.
    """
    with locks.campaign_lock(cid):
        try:
            data = read(cid)
        except Exception:  # noqa: BLE001 — unparseable pins.json: skip this store
            return         # (the shape check below is the valid-JSON half of the same rule)
        if not isinstance(data, dict):
            return
        moved = {}
        for key, rec in list(data.items()):
            if not isinstance(rec, dict) or rec.get("scope") != SCENE:
                continue
            sid = rec.get("sid")
            if not isinstance(sid, str) or sid not in mapping:
                continue
            rec["sid"] = mapping[sid]
            # Re-keyed, not merely re-stamped: the key carries the scene id, so
            # leaving it would file the rule under a scene that no longer
            # exists and hand it to the next scene numbered that way.
            moved[key] = rec
        if not moved:
            return
        for key, rec in moved.items():
            del data[key]
            data[_key(SCENE, rec["sid"], rec["ref"])] = rec
        _write(cid, data)
