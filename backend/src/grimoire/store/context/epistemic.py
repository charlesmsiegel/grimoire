"""Per-actor access to retrieved history: the deterministic stage (11-C1).

09 retrieves scene evidence for a turn; this module says, for the actor the
turn is written for, which of it that actor has and how. Each excerpt post is
classed by the first rule that settles it (spec 11 §4.2): a user override
(`store/knowledge.py`), authorship, a presence interval, the scene lists, or
nothing. A summary or title is never shown to an actor on presence alone (S1):
absorb writes it from the narrator's chair, so only a whole-scene grant whose
fingerprint still holds can vouch for it.

Pure and read-only. `classify` takes one best-effort hold
(`locks.best_effort_campaign_lock`, `assemble._assemble`'s policy), reads the
presence, override and chronicle files once and each source scene once, calls
no model and writes nothing, so it is declared in no lock list. (The cast's
names and each scene's player label re-read `appearances.json` through their
own helpers; they feed R2' and re-matching, and a stale read only narrows.) Every post 09 handed over is re-matched against this read before a
rule sees it, because a cut between retrieval and classification renumbers
posts; whatever no longer matches is dropped for everyone (`moved`).

The rule this module exists to keep: every failure narrows. An unreadable
file, an override that cannot be checked, a lock not acquired, a group that
cannot be expanded -- each makes less visible to an actor, never more. The
narrator is never narrowed by any of it: the narrator sees what 09 returned.

11-S3 calls it on what each compose step's retrieval returned
(`routes/history_recall.gather_view`), and compose renders only what the view
allows (`context/history.parts`). The basis codes are inspector data; they
never reach a prompt.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace

from .. import chronicle, export, knowledge, knowledge_scene, locks, pins, responses
from ..appearances import cast as appearances_cast
from ..appearances import paths as appearances_paths
from ..history import budget as history_budget
from ..history import model
from ..regex import view as regex_view
from ..scenes import identity as scenes_identity
from ..scenes import paths as scenes_paths
from ..scenes import read as scenes_read
from ..scenes import serialize as scenes_serialize
from ..searchdocs import expand as searchdocs_expand

log = logging.getLogger(__name__)

ACCESS = ("witnessed", "known", "suspected", "narrator_only", "unknown")
VISIBLE = ("witnessed", "known", "suspected")
#: The bases an `unknown` part may carry and still be asked about (spec 11
#: §5.1 rule 2): nothing proven either way. An override's `narrator_only`, an
#: unverifiable or moved post, a keyless post under a post-listed override
#: and a group nobody can expand are never asked about.
ELIGIBLE_BASES = frozenset({"not_recorded_present", "scene_cast", "no_record",
                            "summary_needs_grant"})
#: What a Decision answer may set (§5.3): a model never produces `witnessed`
#: or `narrator_only`, and an answer that withholds stays `unknown`.
DECIDED_ACCESS = ("known", "suspected", "unknown")
DECIDED_BASES = frozenset({"decision", "decision_abstained", "decision_failed",
                           "decision_skipped"})
STATES = ("attributed", "withheld", "unattributed")
#: The closed set of §3.5, plus `unkeyed` (a keyless post in a scene with a
#: post-listed override) and `narrator` (the narrator's own slices).
BASES = frozenset({
    "override", "override_group", "override_all", "authored", "pc_speaker",
    "presence_interval", "not_recorded_present", "scene_cast", "no_record",
    "summary_needs_grant", "decision", "decision_abstained", "decision_failed",
    "decision_skipped", "unverifiable", "moved", "unkeyed", "narrator",
})
NARRATOR = "narrator"
_ACTOR_PREFIX = "actor:"
_STATUS = {knowledge.KNOWS: "known", knowledge.SUSPECTS: "suspected",
           knowledge.UNAWARE: "narrator_only"}
#: A scene digest no stored fingerprint can equal: what `SceneFacts.digest`
#: reads when the chronicle could not be read, so no whole-scene grant holds.
_NO_DIGEST = "\x00unreadable"
_ELLIPSIS = "…"


# ---------------------------------------------------------------- vocabulary


@dataclass(frozen=True)
class Perspective:
    """Who a compose step is written for (§3.1). `actor` is a ``characters:``
    ref for an NPC call and None for the narrator; `present` is every actor
    and PC ref on stage this turn, from the post-exclude cast."""
    cid: str
    sid: str
    identity: str | None
    actor: str | None
    present: tuple[str, ...]

    def to_string(self) -> str:
        """09-C1's and 10-C1's ``perspective: str``."""
        return NARRATOR if self.actor is None else _ACTOR_PREFIX + self.actor

    @staticmethod
    def of_string(s: str, *, cid: str, sid: str, identity: str | None,
                  present: tuple[str, ...]) -> Perspective:
        if s == NARRATOR:
            return Perspective(cid, sid, identity, None, tuple(present))
        if s.startswith(_ACTOR_PREFIX) and knowledge.valid_audience(s[len(_ACTOR_PREFIX):]) \
                and s[len(_ACTOR_PREFIX):].startswith("characters:"):
            return Perspective(cid, sid, identity, s[len(_ACTOR_PREFIX):], tuple(present))
        raise ValueError(f"not a perspective: {s!r}")

    @staticmethod
    def of(cid: str, sid: str, actor_ref: str | None, present: Sequence[str], *,
           identity: str | None = None) -> Perspective:
        """`actor_ref` as compose spells it: None and ``"grimoire"`` are the
        narrator, exactly `assemble`'s ``actor_scoped`` test."""
        if actor_ref in (None, "grimoire"):
            return Perspective(cid, sid, identity, None, tuple(present))
        return Perspective.of_string(_ACTOR_PREFIX + str(actor_ref), cid=cid, sid=sid,
                                     identity=identity, present=tuple(present))


@dataclass(frozen=True)
class Slice:
    """A contiguous run of one excerpt's posts in one access class, or an
    item's summary (`part="summary"`, no posts). `text` is set only on a slice
    its perspective may see; an invisible slice is for the inspector."""
    part: str
    posts: tuple[int, ...]
    access: str
    basis: tuple[str, ...]
    text: str
    #: The excerpt posts the text was (or, withheld, would be) rendered
    #: from: for a section to mark and fit (`context/history.section`), and
    #: for the Decision stage to excerpt a withheld part (11-S6). Empty for
    #: moved posts and the summary. Never in `detail()`, and never rendered
    #: from an invisible slice: every renderer reads `visible()`.
    source: tuple[model.EvidencePost, ...] = ()
    #: Each post's fingerprint as classified (`knowledge.shown_fingerprint`),
    #: for the capture's accept to send as `expect` (11-S3); empty for a
    #: moved slice and for the summary.
    keys: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class Classified:
    """One evidence item as one perspective may have it. For an actor,
    `item` is a redacted copy (header fields it may see, no summary, no
    excerpts): a renderer reads `slices`, never the evidence it came from."""
    item: model.EvidenceItem
    slices: tuple[Slice, ...]
    header: Mapping[str, str]
    state: str
    known_to: tuple[str, ...]
    suspected_by: tuple[str, ...]
    admitted: bool = True
    #: Each roster ref's own classes, for the inspector and the capture: per
    #: re-matched post index `(access, basis)`, and the whole scene's under
    #: "summary". Empty when no rule ran.
    by_ref: Mapping[str, Mapping[str, tuple[str, str]]] = field(default_factory=dict)
    #: The source scene's id when classified. An actor's `item` blanks it;
    #: the inspector and an accept still need to name the scene.
    sid: str = ""
    #: For an actor, whether it stood in the item's scene: the appearance
    #: record's scene list, the chronicle cast, or any presence interval
    #: there (spec 11 §5.1 (a)). The Decision stage's first channel.
    stood: bool = False


@dataclass(frozen=True)
class EpistemicView:
    perspective: Perspective
    items: tuple[Classified, ...]
    #: Parts the Decision stage (11-S6) made visible, and every part it was
    #: eligible to ask about, as `(scene key, part, posts)`; 0 and () when
    #: it did not run.
    decided: int = 0
    pending: tuple[tuple[str, str, tuple[int, ...]], ...] = ()
    #: The retrieval the items came from: its ceiling and depth size the
    #: sections, its coverage is the inspector's row. Its items are the
    #: narrator's; an actor's sections read `visible()`, never these.
    evidence: model.Evidence = field(default_factory=model.Evidence)
    #: What history cost the round this view's step belongs to, so far
    #: (spec 11 §14 Q7): `retrievals` and the query `embeds` they sent.
    #: Set by the turn path; empty for a preview or hand-built view.
    round_cost: Mapping[str, int] = field(default_factory=dict)

    def visible(self) -> tuple[Classified, ...]:
        """For an actor, the items with a visible slice, holding only those
        slices; an item with none is omitted, header and all. For the
        narrator, every admitted item without its dropped (`moved`) posts."""
        return tuple(shown for _c, shown in self.visible_pairs())

    def visible_pairs(self) -> tuple[tuple[Classified, Classified], ...]:
        """`visible()`, each beside the item it was cut from."""
        out = []
        for c in self.items:
            if not c.admitted:
                continue
            shown = tuple(s for s in c.slices if s.access in VISIBLE)
            if self.perspective.actor is not None and not shown:
                continue
            out.append((c, replace(c, slices=shown)))
        return tuple(out)

    def detail(self) -> dict:
        """The JSON form for the inspector and the prompt-log capture. Texts
        are left out; basis codes are in, and never reach a prompt."""
        return {
            "perspective": self.perspective.to_string(),
            "decided": self.decided,
            **({"pending": [[k, p, list(posts)] for k, p, posts in self.pending]}
               if self.pending else {}),
            **({"round": dict(self.round_cost)} if self.round_cost else {}),
            "items": [{
                "key": c.item.scene.key, "scene": c.sid or c.item.scene.sid,
                "admitted": c.admitted, "state": c.state, "header": dict(c.header),
                **({"stood": True} if c.stood else {}),
                "known_to": list(c.known_to), "suspected_by": list(c.suspected_by),
                "slices": [{"part": s.part, "posts": list(s.posts), "access": s.access,
                            "basis": list(s.basis), "keys": [dict(k) for k in s.keys]}
                           for s in c.slices],
                "by_ref": {ref: {part: list(cls) for part, cls in parts.items()}
                           for ref, parts in c.by_ref.items()},
            } for c in self.items],
        }


# ---------------------------------------------------------------- the reads


@dataclass(frozen=True)
class _Source:
    """One campaign's files, read once under one hold. A None field is a file
    that could not be read; `held` False is a hold not acquired, under which
    presence and transcript may be a moment apart."""
    cid: str
    held: bool
    presence: dict | None
    entries: dict | None
    audiences: knowledge.Audiences | None
    chron: dict | None
    names: Mapping[str, tuple[str, str]]   # present ref -> (name, role)


@dataclass(frozen=True)
class _Scene:
    sid: str
    identity: str
    messages: list
    texts: tuple[str, ...]                 # each post's prompt view, as 08 sends it
    views: tuple[dict, ...]                # each post's prompt-view copy, unaltered
    variants: Mapping
    speakers: tuple[str, ...]              # each post's speaker, as 08 renders it
    authors: Mapping[str, str] | None      # response_id -> actor ref; None unreadable
    facts: knowledge.SceneFacts
    title: str
    record: dict | None
    chron_ok: bool


_GONE = "gone"              # the identity resolves to no scene: dropped for everyone
_UNREADABLE = "unreadable"  # dropped for actors, rendered as 09 did for the narrator
_UNVERIFIABLE = "unverifiable"


def _read_source(cid: str, held: bool, present: Sequence[str], sid: str) -> _Source:
    try:
        presence = appearances_paths.record(cid)
        if not isinstance(presence, dict):
            presence = None
    except Exception:  # noqa: BLE001 -- an unreadable file is the narrowing row, never the turn
        presence = None
    entries: dict | None
    try:
        read, intact = knowledge.read_checked(cid)
        entries = read if intact else None
    except Exception:  # noqa: BLE001 -- as above
        entries = None
    try:
        audiences = knowledge_scene.audiences_for(cid)
    except Exception:  # noqa: BLE001 -- as above
        audiences = None
    try:
        chron = chronicle.read_chronicle(cid)
        chron = chron if isinstance(chron, dict) else None
    except Exception:  # noqa: BLE001 -- as above
        chron = None
    try:
        cast = appearances_cast.scene_cast(cid, sid, include_departed=True)
        names = {f"{a['kind']}:{a['id']}": (str(a.get("name") or ""), str(a.get("role") or ""))
                 for a in cast}
    except Exception:  # noqa: BLE001 -- without names R2' cannot fire, which only narrows
        names = {}
    names = {ref: names[ref] for ref in present if ref in names}
    return _Source(cid, held, presence, entries, audiences, chron, names)


def _current_sid(cid: str, ref: model.SceneRef) -> str | None:
    """The scene's sid now: 08 follows an identity across a rename, and 09
    keeps the sid it retrieved under."""
    if scenes_identity.scene_identity(cid, ref.sid) == ref.identity:
        return ref.sid
    return scenes_identity.find_by_identity(cid, ref.identity or "")


def _guarded(fn, *args, **kwargs):
    """`fn`'s answer, or None when it raises: one unreadable file narrows its
    own rule and never takes the scene with it."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001 -- the caller reads None as unreadable, which narrows
        return None


def _read_scene(src: _Source, ref: model.SceneRef) -> _Scene | str:
    """One read of a source scene, with its prompt view computed over the
    whole transcript as 08 computes it (depth-ranged rules see the right
    depth). The identity is checked again after every read, so a scene
    deleted and re-created under the sid in between is not mistaken for it.
    Anything unexpected is the unreadable-scene row: dropped for actors,
    09's rendering for the narrator. Nothing here may fail the turn."""
    if not ref.identity:
        return _UNVERIFIABLE
    try:
        sid = _current_sid(src.cid, ref)
        if sid is None:
            return _GONE
        scene = scenes_read.read_scene(src.cid, sid)
        messages = scene["messages"]
        authors = _guarded(responses.actor_refs, src.cid, sid)
        variants = _guarded(responses.variants_by_response, src.cid, sid,
                            token=ref.identity) or {}
        if scenes_identity.scene_identity(src.cid, sid) != ref.identity:
            gone = scenes_identity.find_by_identity(src.cid, ref.identity) is None
            return _GONE if gone else _UNREADABLE
        label = _guarded(appearances_cast.player_label, src.cid, sid) or ""
        shown = regex_view.view(messages, cid=src.cid, phase="prompt")
        texts = tuple(searchdocs_expand._encodable(export.drop_images(
            m["content"] if isinstance(m.get("content"), str) else "")) for m in shown)
        speakers = tuple(searchdocs_expand._encodable(searchdocs_expand._speaker(m, label))
                         for m in messages)
        title = str(scene["meta"].get("title") or "")
        record = (src.chron or {}).get(sid)
        record = record if isinstance(record, dict) else None
        facts = knowledge_scene.scene_facts(messages, variants, title=title, record=record)
    except scenes_paths.SceneNotFound:
        return _GONE
    except Exception:  # noqa: BLE001 -- an unreadable scene drops its item for actors
        return _UNREADABLE
    chron_ok = src.chron is not None
    if not chron_ok:
        facts = replace(facts, digest=_NO_DIGEST)
    return _Scene(sid, ref.identity, messages, texts, tuple(shown), variants, speakers,
                  authors, facts, title, record, chron_ok)


# ---------------------------------------------------------------- re-matching


def _rematch(sc: _Scene, post: model.EvidencePost) -> int | None:
    """The transcript index `post` still names, or None (`moved`): the same
    key, the same rendered speaker, still in context, and its text inside the
    post's prompt view now. 08 clips a long post with a leading or trailing
    ellipsis and 09 can trim the last post again, so the evidence text, its
    ellipses removed, is matched as a substring."""
    i = post.index
    if not 0 <= i < len(sc.messages):
        return None
    m = sc.messages[i]
    if scenes_serialize.is_excluded(m) or scenes_serialize.is_director_note(m):
        return None
    if knowledge.post_key(m) != post.key or sc.speakers[i] != post.speaker:
        return None
    core = post.text.strip().strip(_ELLIPSIS).strip()
    return i if core in sc.texts[i] else None


# ---------------------------------------------------------------- overrides


@dataclass(frozen=True)
class _Overrides:
    """What the scene's override entries mean beyond `knowledge.decide`: the
    posts an unresolvable group's `unaware` covers (withheld from everyone),
    whether it covers the whole scene, and whether any post-listed entry
    exists (for the keyless rule)."""
    blind: frozenset[tuple[str, str]]
    blind_all: bool
    post_listed: bool


def _overrides(entries: Mapping[str, dict], audiences: knowledge.Audiences,
               identity: str) -> _Overrides:
    blind: set[tuple[str, str]] = set()
    blind_all = post_listed = False
    for entry in entries.values():
        subject = entry["subject"]
        if subject["scene"] != identity:
            continue
        posts = subject["kind"] == "posts"
        post_listed = post_listed or posts
        who = entry["audience"]
        if entry["status"] == knowledge.UNAWARE and who.startswith("groups:") \
                and who not in audiences.groups:
            if posts:
                blind.update((p["key"], p["part"]) for p in subject["posts"])
            else:
                blind_all = True
    return _Overrides(frozenset(blind), blind_all, post_listed)


def _reaches(entry: dict, ref: str, audiences: knowledge.Audiences) -> bool:
    """Whether a withholding entry may concern `ref`, failing closed: ``*``
    and an unresolvable group reach everyone."""
    who = entry["audience"]
    if who in (ref, knowledge.EVERYONE):
        return True
    if who.startswith("groups:"):
        group = audiences.groups.get(who)
        return group is None or ref in group[1]
    return False


def _withheld_within(entries: Mapping[str, dict], audiences: knowledge.Audiences,
                     identity: str, ref: str) -> bool:
    """A post-listed `unaware` in this scene reaching `ref`: a whole-scene
    grant does not then show the summary or title, which may paraphrase the
    very post withheld (§7.2's point, carried to S1)."""
    return any(e["subject"]["scene"] == identity and e["subject"]["kind"] == "posts"
               and e["status"] == knowledge.UNAWARE and _reaches(e, ref, audiences)
               for e in entries.values())


# ---------------------------------------------------------------- the rules


@dataclass(frozen=True)
class _Env:
    src: _Source
    sc: _Scene
    ov: _Overrides | None    # None: overrides cannot be trusted (unreadable)


def _r1(env: _Env, ref: str, m: dict, author: str | None) -> tuple[str, str] | None:
    """R1, an override, and the unresolvable-group withholding beside it.
    The withholding is stricter than 7.2: it loses only to an entry on the
    post naming the actor, or (for a whole-scene withholding) to any entry on
    the post. Recency is not consulted -- a group nobody can expand cannot be
    ranked by date against one that can, so the withholding stands."""
    key, part = knowledge.post_key(m), knowledge.post_part(m)
    match = knowledge.decide(env.src.entries or {}, actor=ref, scene=env.sc.identity,
                             post=(key, part), author=author, facts=env.sc.facts,
                             audiences=env.src.audiences or knowledge.Audiences({}, {}))
    on_post = match is not None and \
        (env.src.entries or {})[match.kid]["subject"]["kind"] == "posts"
    ov = env.ov
    if ov is not None and key and (key, part) in ov.blind \
            and not (on_post and match is not None and match.basis == "override"):
        return "unknown", "override_group"
    if ov is not None and ov.blind_all and not on_post \
            and not (match is not None and match.basis == "override"):
        return "unknown", "override_group"
    if match is not None:
        return _STATUS[match.status], match.basis
    return None


def _intervals(entry: dict, sid: str) -> tuple[bool, list | None]:
    """``(well_formed, intervals)`` for the actor in `sid`: intervals None
    when the record has none; not well formed is read as nothing proven."""
    presence = entry.get("presence")
    raw = presence.get(sid) if isinstance(presence, dict) else None
    if raw is None:
        return True, None
    if not isinstance(raw, list) or not all(
            isinstance(r, dict) and type(r.get("start")) is int and r["start"] >= 0
            and (r.get("end") is None or type(r.get("end")) is int) for r in raw):
        return False, None
    return True, raw


def _presence(env: _Env, ref: str, i: int) -> tuple[str, str]:
    """R3 to R6, from the appearance record and the chronicle cast."""
    entry = env.src.presence.get(ref.replace(":", "/", 1)) if env.src.presence else None
    if not isinstance(entry, dict):
        entry = {}
    ok, found = _intervals(entry, env.sc.sid)
    if not ok:
        return "unknown", "no_record"
    if found is not None and env.src.held:
        if any(r["start"] <= i and (r.get("end") is None or i < r["end"]) for r in found):
            return "witnessed", "presence_interval"
        return "unknown", "not_recorded_present"
    scenes = entry.get("scenes")
    cast = (env.sc.record or {}).get("cast")
    slash = ref.replace(":", "/", 1)
    if (isinstance(scenes, list) and env.sc.sid in scenes) or \
            (isinstance(cast, list) and slash in cast):
        return "unknown", "scene_cast"
    return "unknown", "no_record"


def _stood(env: _Env, ref: str) -> bool:
    """Whether `ref` stood in the scene at all (§5.1 (a)): in its appearance
    record's scene list, the chronicle cast, or with any interval there. An
    unreadable record is False, which only closes a channel."""
    entry = env.src.presence.get(ref.replace(":", "/", 1)) if env.src.presence else None
    if not isinstance(entry, dict):
        entry = {}     # the chronicle cast may still name it, as `_presence` reads
    ok, found = _intervals(entry, env.sc.sid)
    scenes = entry.get("scenes")
    cast = (env.sc.record or {}).get("cast")
    return bool((ok and found) or (isinstance(scenes, list) and env.sc.sid in scenes)
                or (isinstance(cast, list) and ref.replace(":", "/", 1) in cast))


def _pc_spoke(env: _Env, ref: str, m: dict) -> bool:
    """R2': a user post stamped with a name that resolves, among everyone
    present, to this PC's."""
    name, role = env.src.names.get(ref, ("", ""))
    speaker = m.get("speaker")
    if role != "player" or not name or m.get("role") != "user" or not speaker:
        return False
    everyone = [n for n, _r in env.src.names.values() if n]
    return scenes_serialize.match_name(str(speaker), everyone) == name


def _post_class(env: _Env, ref: str, i: int) -> tuple[str, str]:
    """The class of post `i` for `ref`: the first rule that settles it."""
    if env.ov is None:
        return "unknown", "unverifiable"
    m = env.sc.messages[i]
    rid = m.get("response_id")
    author = (env.sc.authors or {}).get(rid) if rid else None
    found = _r1(env, ref, m, author)
    if found is not None:
        return found
    if not knowledge.post_key(m) and env.ov.post_listed:
        return "unknown", "unkeyed"
    if env.src.presence is None:
        return "unknown", "no_record"
    if author is not None and author == ref:
        return "witnessed", "authored"
    if _pc_spoke(env, ref, m):
        return "witnessed", "pc_speaker"
    return _presence(env, ref, i)


def _scene_class(env: _Env, ref: str) -> tuple[str, str]:
    """S1: what `ref` has of the scene's summary and title. Only a
    whole-scene grant whose digest holds shows them, and not past a
    post-listed `unaware` reaching `ref`."""
    if env.ov is None:
        return "unknown", "unverifiable"
    entries = env.src.entries or {}
    audiences = env.src.audiences or knowledge.Audiences({}, {})
    match = knowledge.decide(entries, actor=ref, scene=env.sc.identity, post=None,
                             author=None, facts=env.sc.facts, audiences=audiences)
    if match is not None and match.status == knowledge.UNAWARE:
        return "narrator_only", match.basis
    if env.ov.blind_all and not (match is not None and match.basis == "override"):
        return "unknown", "override_group"
    if match is None:
        return "unknown", "summary_needs_grant"
    if _withheld_within(entries, audiences, env.sc.identity, ref):
        # The summary may paraphrase a post the user withheld from `ref`: an
        # override's doing, so never put to the Decision stage (11-S6).
        return "unknown", "override"
    return _STATUS[match.status], match.basis


# ---------------------------------------------------------------- per item


@dataclass(frozen=True)
class _Classes:
    """One ref's classes on one admitted item: per re-matched post index, and
    for the whole scene (summary and title)."""
    posts: Mapping[int, tuple[str, str]]
    scene: tuple[str, str]


def _classes(env: _Env, ref: str, indices: Sequence[int]) -> _Classes:
    return _Classes({i: _post_class(env, ref, i) for i in indices}, _scene_class(env, ref))


def _runs(excerpt: model.Excerpt, posts: Sequence[tuple[model.EvidencePost, int | None]],
          classes: _Classes | None) -> tuple[Slice, ...]:
    """An excerpt split into contiguous runs of one access; `classes` None is
    the narrator, who has every re-matched post. A run that is the whole
    excerpt keeps 09's own text, so an unsplit item renders as 09 rendered it."""
    out: list[Slice] = []
    run: list[tuple[model.EvidencePost, int, str]] = []
    access = ""

    def flush():
        if not run:
            return
        basis = tuple(sorted({b for _p, _i, b in run}))
        shown = access in VISIBLE
        source = tuple(p for p, _i, _b in run)
        text = ""
        if shown:
            text = (excerpt.text if source == excerpt.posts
                    else history_budget._excerpt(source).text)
        # Every re-matched run keeps its posts, shown or not: the Decision
        # stage (11-S6) excerpts a withheld part from them. A renderer reads
        # `visible()`, so an invisible slice's posts reach no prompt.
        out.append(Slice("posts", tuple(i for _p, i, _b in run), access, basis, text,
                         () if "moved" in basis else source))
        run.clear()

    for post, i in posts:
        if i is None:
            a, b = "unknown", "moved"
        elif classes is None:
            a, b = "known", "narrator"
        else:
            a, b = classes.posts[i]
        if a != access:
            flush()
            access = a
        run.append((post, post.index if i is None else i, b))
    flush()
    return tuple(out)


def _keyed(sc: _Scene, s: Slice) -> Slice:
    """`s` with each post's fingerprint as classified, through the helper the
    Knowledge ledger's own scene read uses, so an accept's `expect` is what
    the route would compute now if nothing moved."""
    if s.part != "posts" or "moved" in s.basis:
        return s
    return replace(s, keys=tuple(
        knowledge.shown_fingerprint(sc.messages[i], sc.views[i], sc.variants)
        for i in s.posts))


def _summary_slice(item: model.EvidenceItem, sc: _Scene | None,
                   scene: tuple[str, str] | None) -> Slice | None:
    """The summary's slice. For an actor a grant shows only the summary the
    chronicle holds now: a rolling summary, or one changed since retrieval,
    is text no grant covered."""
    if not item.summary or item.summary_omission:
        return None
    if scene is None:
        return Slice("summary", (), "known", ("narrator",), item.summary)
    access, basis = scene
    stale = (sc is None or not sc.chron_ok or sc.record is None
             or sc.record.get("summary") != item.summary)
    if stale and access in VISIBLE:
        access, basis = "unknown", "summary_needs_grant"
    if stale and access == "unknown" and basis in ELIGIBLE_BASES:
        # No grant and no answer could show this text (11-S6): it is not the
        # chronicle's summary now, so it is never asked about.
        basis = "moved"
    return Slice("summary", (), access, (basis,), item.summary if access in VISIBLE else "")


def _aggregate(per_ref: Mapping[str, _Classes], present: Sequence[str]) -> tuple:
    """The narrator's state, `known_to` and `suspected_by`, over `present`."""
    known, suspected, withheld = [], [], False
    for ref in present:
        cls = per_ref.get(ref)
        if cls is None:
            continue
        found = {a for a, _b in cls.posts.values()} | {cls.scene[0]}
        if found & {"witnessed", "known"}:
            known.append(ref)
        elif "suspected" in found:
            suspected.append(ref)
        if any(a == "narrator_only" and b.startswith("override")
               for a, b in [*cls.posts.values(), cls.scene]):
            withheld = True
    state = "attributed" if known or suspected else "withheld" if withheld else "unattributed"
    return state, tuple(known), tuple(suspected)


def _redacted(item: model.EvidenceItem, header: Mapping[str, str]) -> model.EvidenceItem:
    """What an actor may hold of the item itself. The sid is blanked too: it
    is the scene's slugged title. The identity stays, as the scene's key."""
    return replace(item, scene=replace(item.scene, sid=""), title=header.get("title", ""),
                   date=header.get("date", ""), location=header.get("location", ""),
                   summary="", excerpts=(), summary_omission="", evidence_ids=())


def _unclassified(item: model.EvidenceItem, perspective: Perspective,
                  admitted: bool) -> Classified:
    """An item no rule can be run on (no identity, another campaign, an
    unreadable or vanished scene): nothing for an actor; for the narrator,
    09's rendering when `admitted`, nothing when the scene is gone."""
    if perspective.actor is not None or not admitted:
        slices = tuple(
            Slice("posts", tuple(p.index for p in e.posts), "unknown", ("unverifiable",), "")
            for e in item.excerpts)
        return Classified(_redacted(item, {}), slices, {}, "unattributed", (), (),
                          admitted=admitted and perspective.actor is None)
    slices = tuple(Slice("posts", tuple(p.index for p in e.posts), "known", ("narrator",),
                         e.text, e.posts) for e in item.excerpts)
    summary = _summary_slice(item, None, None)
    header = {"title": item.title, "date": item.date, "location": item.location}
    return Classified(item, (*slices, *([summary] if summary else [])), header,
                      "unattributed", (), ())


def eligible(s: Slice) -> bool:
    """Whether the Decision stage may ask about `s` by its class alone (§5.1
    rule 2): `unknown`, on bases that prove nothing either way. The channel
    (rule 3) is the caller's to read."""
    return s.access == "unknown" and bool(s.basis) and set(s.basis) <= ELIGIBLE_BASES


def _slices(item: model.EvidenceItem, sc: _Scene, excerpts, own: _Classes | None) -> list:
    slices = [_keyed(sc, s) for e, posts in zip(item.excerpts, excerpts, strict=True)
              for s in _runs(e, posts, own)]
    summary = _summary_slice(item, sc, own.scene if own else None)
    if summary:
        slices.append(summary)
    return slices


def _answered(key: str, own: _Classes, slices: Sequence[Slice],
              decided: Mapping[tuple[str, str, tuple[int, ...]], tuple[str, str]]) -> _Classes:
    """`own` with the Decision stage's answers (§5.3) set on the parts they
    were asked about, as this read cuts them: a part whose posts moved since
    it was asked matches no key and keeps its class. Only an eligible part is
    ever changed, and only to `known`, `suspected` or `unknown`."""
    posts = dict(own.posts)
    scene = own.scene
    for s in slices:
        found = decided.get((key, s.part, s.posts))
        if found is None or not eligible(s):
            continue
        access, basis = found
        if access not in DECIDED_ACCESS or basis not in DECIDED_BASES:
            raise ValueError(f"not a Decision answer: {found!r}")
        if s.part == "summary":
            # Only a scene class that is itself open: an answer never moves
            # what an override settled, even one the summary's own staleness
            # withheld.
            if own.scene[0] == "unknown" and own.scene[1] in ELIGIBLE_BASES:
                scene = (access, basis)
        else:
            posts.update((i, (access, basis)) for i in s.posts)
    return _Classes(posts, scene)


def _classify_item(item: model.EvidenceItem, perspective: Perspective,
                   roster: Sequence[str], env: _Env,
                   decided: Mapping[tuple[str, str, tuple[int, ...]], tuple[str, str]]
                   | None = None) -> Classified:
    sc = env.sc
    excerpts = [[(p, _rematch(sc, p)) for p in e.posts] for e in item.excerpts]
    indices = sorted({i for e in excerpts for _p, i in e if i is not None})
    per_ref = {ref: _classes(env, ref, indices) for ref in roster}
    state, known_to, suspected_by = _aggregate(per_ref, roster)
    own = per_ref.get(perspective.actor) if perspective.actor else None
    if perspective.actor is not None and own is None:
        own = _classes(env, perspective.actor, indices)
    slices = _slices(item, sc, excerpts, own)
    if own is not None and decided:
        own = _answered(item.scene.key, own, slices, decided)
        slices = _slices(item, sc, excerpts, own)
    by_ref = {ref: {**{str(i): cls for i, cls in c.posts.items()}, "summary": c.scene}
              for ref, c in per_ref.items()}
    if own is None:
        header = {"title": item.title, "date": item.date, "location": item.location}
        return Classified(item, tuple(slices), header, state, known_to, suspected_by,
                          by_ref=by_ref, sid=sc.sid)
    stood = _stood(env, perspective.actor) if perspective.actor else False
    # 09's date is the scene's first moment and its location the scene's
    # last: neither belongs to a post, so presence for part of the scene
    # vouches for neither. Like the title, they ride a whole-scene grant.
    header = {}
    if own.scene[0] in VISIBLE:
        header = {"date": item.date, "location": item.location}
        if item.title == sc.title:
            header["title"] = item.title
    return Classified(_redacted(item, header), tuple(slices), header, state, known_to,
                      suspected_by, by_ref=by_ref, sid=sc.sid, stood=stood)


# ---------------------------------------------------------------- entry point


def present_roster(cid: str, sid: str) -> tuple[str, ...]:
    """Who is on stage for a compose of `sid` now, as `_assemble` reads it:
    the scene's cast (PCs included) minus the pins' excluded refs."""
    posts = len(scenes_read.read_scene(cid, sid)["messages"])
    excluded = pins.active(cid, sid, posts)["excluded"]
    return tuple(ref for a in appearances_cast.scene_cast(cid, sid)
                 if (ref := f"{a['kind']}:{a['id']}") not in excluded)


_READ = object()


def narrator_view(cid: str, sid: str, evidence: model.Evidence, present: Sequence[str] = (), *,
                  identity: str | object | None = _READ) -> EpistemicView:
    """`evidence` as the narrator has it, unclassified: 09's rendering, every
    item `unattributed`. For hand-built evidence (a preview, an eval, a
    template check) and the turn path's pass-through when classification
    fails -- which costs the narrator its annotations and nothing else.
    `identity` defaults to the scene's own, which is what compose checks."""
    if identity is _READ:
        identity = scenes_identity.scene_identity(cid, sid)
    perspective = Perspective(cid, sid, identity if isinstance(identity, str) else None, None,
                              tuple(present))
    return EpistemicView(perspective, tuple(_unclassified(item, perspective, admitted=True)
                                            for item in evidence.items), evidence=evidence)


def _local(item: model.EvidenceItem, cid: str) -> bool:
    """Whether the item is from the played campaign. Another campaign's ref
    proves nothing about the same-named actor here, so 09's tier-3 sources
    are never classified (no turn retrieves them)."""
    return item.scene.campaign in ("", cid)


def classify(perspective: Perspective, items: Sequence[model.EvidenceItem], *,
             roster: Sequence[str] = (),
             decided: Mapping[tuple[str, str, tuple[int, ...]], tuple[str, str]]
             | None = None) -> EpistemicView:
    """Each item's slices for `perspective`, in 09's order. `roster` is who
    the narrator aggregate is computed over: by default the actor, or for the
    narrator everyone present. `decided` is the Decision stage's answers
    (11-S6), `(scene key, part, posts) -> (access, basis)`, applied to the
    actor's own eligible parts as this read cuts them; the narrator ignores
    it."""
    roster = tuple(roster) or ((perspective.actor,) if perspective.actor
                               else perspective.present)
    cid = perspective.cid
    reads: dict[str, _Scene | str] = {}
    local = [item for item in items if _local(item, cid)]
    src = None
    if local:
        with locks.best_effort_campaign_lock(cid) as held:
            src = _read_source(cid, bool(held), perspective.present, perspective.sid)
            for item in local:
                if item.scene.key not in reads:
                    reads[item.scene.key] = _read_scene(src, item.scene)
    out = []
    for item in items:
        sc = reads.get(item.scene.key) if _local(item, cid) else _UNVERIFIABLE
        if not isinstance(sc, _Scene) or src is None:
            out.append(_unclassified(item, perspective, admitted=sc != _GONE))
            continue
        ov = (_overrides(src.entries, src.audiences, sc.identity)
              if src.entries is not None and src.audiences is not None else None)
        try:
            out.append(_classify_item(item, perspective, roster, _Env(src, sc, ov), decided))
        except Exception:  # noqa: BLE001 -- an item that cannot be classified narrows to nothing
            log.warning("Could not classify history item %s", item.scene.key)
            out.append(_unclassified(item, perspective, admitted=True))
    shown = sum(1 for c in out for s in c.slices
                if "decision" in s.basis and s.access in VISIBLE)
    return EpistemicView(perspective, tuple(out), decided=shown if decided else 0)


def classify_evidence(perspective: Perspective, evidence: model.Evidence) -> EpistemicView:
    """`classify` over a retrieval, the view carrying it (11-S3's turn path)."""
    return replace(classify(perspective, evidence.items), evidence=evidence)


# ---------------------------------------------------------------- the record

#: `to_record`'s format. A record of another version reads as nothing.
RECORD_VERSION = 1


def _post_record(p: model.EvidencePost) -> list:
    return [p.index, p.key, p.part, p.speaker, p.text]


def _post_of(r) -> model.EvidencePost:
    index, key, part, speaker, text = r
    if not (type(index) is int and isinstance(key, str) and (part is None or type(part) is int)
            and isinstance(speaker, str) and isinstance(text, str)):
        raise ValueError("not a post")
    return model.EvidencePost(index, key, part, speaker, text)


def _strings(value) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError("not a list of strings")
    return tuple(value)


def _ints(value) -> tuple[int, ...]:
    if not isinstance(value, list) or not all(type(v) is int for v in value):
        raise ValueError("not a list of integers")
    return tuple(value)


def to_record(view: EpistemicView) -> dict:
    """`view` as JSON, everything a compose renders from it: what a roll
    resume in a later request composes with (spec 11 §5.5), stored in the
    response's frozen snapshot. The retrieval keeps its sizing and coverage;
    its items and candidates are left out, since every section renders from
    the classified items alone."""
    p = view.perspective
    ev = view.evidence
    return {
        "version": RECORD_VERSION,
        "perspective": {"cid": p.cid, "sid": p.sid, "identity": p.identity,
                        "actor": p.actor, "present": list(p.present)},
        "decided": view.decided,
        "pending": [[k, part, list(posts)] for k, part, posts in view.pending],
        "round": dict(view.round_cost),
        "evidence": {"ceiling": ev.ceiling, "depth": ev.depth, "digest": ev.query_digest,
                     "coverage": {**asdict(ev.coverage), "tiers": list(ev.coverage.tiers)}},
        "items": [{
            "scene": [c.item.scene.sid, c.item.scene.identity, c.item.scene.campaign],
            "title": c.item.title, "date": c.item.date, "location": c.item.location,
            "summary": c.item.summary,
            "excerpts": [{"posts": [_post_record(q) for q in e.posts], "text": e.text}
                         for e in c.item.excerpts],
            "evidence_ids": list(c.item.evidence_ids), "tokens": c.item.tokens,
            "summary_omission": c.item.summary_omission,
            # A withheld part keeps its class and keys, never its text: the
            # record is what the contribution was shown.
            "slices": [{"part": s.part, "posts": list(s.posts), "access": s.access,
                        "basis": list(s.basis),
                        "text": s.text if s.access in VISIBLE else "",
                        "source": [_post_record(q) for q in s.source]
                        if s.access in VISIBLE else [],
                        "keys": [dict(k) for k in s.keys]} for s in c.slices],
            "header": dict(c.header), "state": c.state, "known_to": list(c.known_to),
            "suspected_by": list(c.suspected_by), "admitted": c.admitted,
            "by_ref": {ref: {part: list(cls) for part, cls in parts.items()}
                       for ref, parts in c.by_ref.items()},
            "sid": c.sid, "stood": c.stood,
        } for c in view.items],
    }


def _slice_of(r: dict) -> Slice:
    if r["access"] not in ACCESS or r["part"] not in ("posts", "summary"):
        raise ValueError("not a slice")
    keys = r["keys"]
    if not isinstance(keys, list) or not all(
            isinstance(k, dict) and all(isinstance(v, str) for v in k.values()) for k in keys):
        raise ValueError("not a slice's keys")
    if not isinstance(r["text"], str):
        raise ValueError("not a slice's text")
    return Slice(r["part"], _ints(r["posts"]), r["access"], _strings(r["basis"]), r["text"],
                 tuple(_post_of(q) for q in r["source"]), tuple(dict(k) for k in keys))


def _item_of(r: dict) -> Classified:
    sid, identity, campaign = r["scene"]
    if not (isinstance(sid, str) and (identity is None or isinstance(identity, str))
            and isinstance(campaign, str)):
        raise ValueError("not a scene ref")
    for name in ("title", "date", "location", "summary", "summary_omission", "state", "sid"):
        if not isinstance(r[name], str):
            raise ValueError(f"not an item's {name}")
    item = model.EvidenceItem(
        model.SceneRef(sid, identity, campaign), r["title"], date=r["date"],
        location=r["location"], summary=r["summary"],
        excerpts=tuple(model.Excerpt(tuple(_post_of(q) for q in e["posts"]), e["text"])
                       for e in r["excerpts"]),
        evidence_ids=_strings(r["evidence_ids"]), tokens=int(r["tokens"]),
        summary_omission=r["summary_omission"])
    header = r["header"]
    if not isinstance(header, dict) or not all(isinstance(v, str) for v in header.values()):
        raise ValueError("not a header")
    by_ref = {str(ref): {str(part): (str(cls[0]), str(cls[1])) for part, cls in parts.items()}
              for ref, parts in r["by_ref"].items()}
    return Classified(item, tuple(_slice_of(s) for s in r["slices"]), dict(header),
                      r["state"], _strings(r["known_to"]), _strings(r["suspected_by"]),
                      admitted=r["admitted"] is True, by_ref=by_ref, sid=r["sid"],
                      stood=r["stood"] is True)


def from_record(record) -> EpistemicView | None:
    """`to_record`'s view again, or None for anything that is not one (an
    older format, a hand edit): a resume then composes with no history, which
    narrows. Compose still checks the view against its own scene and speaker."""
    try:
        if not isinstance(record, dict) or record.get("version") != RECORD_VERSION:
            return None
        p = record["perspective"]
        actor = p["actor"]
        if actor is not None and not isinstance(actor, str):
            return None
        perspective = Perspective(str(p["cid"]), str(p["sid"]),
                                  p["identity"] if isinstance(p["identity"], str) else None,
                                  actor, _strings(p["present"]))
        ev = record["evidence"]
        coverage = dict(ev["coverage"])
        coverage["tiers"] = _ints(coverage.get("tiers", [1]))
        evidence = model.Evidence(coverage=model.Coverage(**coverage), ceiling=int(ev["ceiling"]),
                                  depth=int(ev["depth"]), query_digest=str(ev["digest"]))
        pending = tuple((str(k), str(part), _ints(posts)) for k, part, posts in record["pending"])
        round_cost = {str(k): int(v) for k, v in record["round"].items()}
        return EpistemicView(perspective, tuple(_item_of(c) for c in record["items"]),
                             decided=int(record["decided"]), pending=pending,
                             evidence=evidence, round_cost=round_cost)
    except Exception:  # noqa: BLE001 -- a record that does not read is no history, never a failed resume
        log.warning("A stored history view could not be read")
        return None
