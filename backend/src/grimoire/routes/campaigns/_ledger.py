"""Campaign HTTP endpoints: ledger domain."""


from __future__ import annotations

import contextlib
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, Response

from ... import store
from ...llm import LLMClient
from ...store import campaign_settings, compiled, overview
from ...store import paths as library_paths
from ...store.continuity import doc as continuity_doc
from ...store.continuity import effective as continuity_effective
from ...store.inference import settings as inference_settings
from .. import characters as character_routes
from .. import ledger as ledger_routes
from .. import runs
from ..common import (
    PC_HISTORY_MISSES,
    _campaign_root_or_404,
    _content_fields,
    _display_name_or_400,
    _dump,
    _page_of,
    _page_window,
    _response_body,
    _serve_image,
    _serve_image_file,
    _sheet_failure_status,
    _upload_image_ext,
    _with_descriptions,
    _write_response,
    computes_only,
    draft_completion,
    get_llm,
    image_draft_prompt,
    image_id_field,
    image_promote_guards,
    leaves_campaign_unchanged,
    pc_history_404,
    record_art_mutation,
    require_inference,
    stamps_campaign_write,
)
from ..models import (
    AdvanceTime,
    AvatarFocus,
    CalendarConfig,
    CampaignClimate,
    CharacterBirthdate,
    CharacterCreate,
    CharacterTags,
    CopyFromGreeting,
    DefaultVersion,
    ForkCampaign,
    GroupStateSave,
    ImageDescription,
    ImagePromote,
    NameBody,
    NewCampaign,
    NoticeMark,
    PCCreate,
    PCUpdate,
    PersonaVersionCreate,
    PersonaVersionUpdate,
    PickBody,
    PinRule,
    RefList,
    ResponseSettings,
    ScheduledEventCreate,
    ScheduledEventEdit,
    SyncPin,
    TaglineSave,
    VersionCreate,
    VersionUpdate,
    VoiceAnchorSave,
)
from ..overview_log import overview_read


from ._router import router

# The recent-facts tier of the ledger. Shorter than GET /chronicle's 50: that
# route feeds the per-scene recap, where the reader is looking for one entry,
# while this is a standing overview meant to be read top to bottom.
LEDGER_RECENT = 20

#: `relationships.set_feeling` writes 0–5; the ledger draws the number back.
FEELING_AXES = ("trust", "affection", "tension")


def _ledger_text(value, fallback: str = "") -> str:
    """A projected field as text. The ledger renders these directly, and React
    refuses an object as a child -- so a hand-edited record with a dict-valued
    `title` would blank the whole view rather than showing one odd row. The
    per-section tolerance below cannot catch that: the read SUCCEEDS and the
    failure happens in the browser. `commitments.open_commitments` normalizes
    its own rows; `plot.open_threads` does not, and hardening `plot` is one of
    the pre-existing items flagged on that PR, so the coercion sits here where
    the ledger owns the projection.

    Module level rather than nested in `get_ledger` because the relationships
    projection below needs the same rule, and two copies of it is two places
    for one of them to stop being applied. That argument holds one level up as
    well, which is why the body is now `store.fieldtext.text`: nine modules had
    written these three lines out, this one included.
    """
    return store.fieldtext.text(value, fallback)


def _ledger_relationships(cid: str) -> list[dict]:
    """Who stands where with whom, as flat rows the ledger can put in a table.

    relationships.json holds two shapes and both belong on this view. A
    *feeling* is directed and metered -- A's trust, affection and tension
    toward B, which B does not return by construction -- and a *bond* is
    symmetric, named ("kin", "sworn") and dated to the scene it formed in. One
    list with a `kind` rather than two sections: the reader's question is what
    stands between two people, and answering it in two tables makes them read
    both to find out.

    Names are resolved once per token and cached. `relationships.actor_name`
    reads a card per call, and a campaign with a dozen actors carries upwards of
    a hundred directed pairs, so the uncached version is that many file reads
    for a dozen answers. A token whose card will not parse falls back to its own
    id rather than emptying the section -- `casefile.build` makes the same trade
    for a cast member whose card is broken, and for the same reason: a name is
    the least of what this row says.

    The meters are clamped rather than trusted, like `casefile._pips`: the file
    is hand-editable, the client draws five pips, and a stored 9 would draw four
    that do not exist. A non-integer reads as 0 -- visibly nothing, rather than
    a crash.
    """
    data = store.relationships.read(cid)
    feelings = data.get("feelings") if isinstance(data.get("feelings"), dict) else {}
    bonds = data.get("bonds") if isinstance(data.get("bonds"), dict) else {}
    names: dict[str, str] = {}

    def _name(token: str) -> str:
        if token not in names:
            try:
                names[token] = store.relationships.actor_name(cid, token)
            except Exception:  # noqa: BLE001 — an unreadable card costs a name, not the section
                names[token] = token.partition(":")[2] or token
        return names[token]

    def _meter(value) -> int:
        return min(5, max(0, value)) if isinstance(value, int) and not isinstance(value, bool) else 0

    rows: list[dict] = []
    for key, f in feelings.items():
        if not isinstance(key, str) or not isinstance(f, dict):
            continue
        a, _, b = key.partition("->")
        rows.append({"id": key, "kind": "feeling", "a": a, "b": b,
                     "a_name": _name(a), "b_name": _name(b),
                     **{axis: _meter(f.get(axis)) for axis in FEELING_AXES},
                     "note": _ledger_text(f.get("note")), "type": "", "since_scene": ""})
    for key, b_rec in bonds.items():
        if not isinstance(key, str) or not isinstance(b_rec, dict):
            continue
        a, _, b = key.partition("|")
        rows.append({"id": key, "kind": "bond", "a": a, "b": b,
                     "a_name": _name(a), "b_name": _name(b),
                     **dict.fromkeys(FEELING_AXES, 0),
                     "note": "", "type": _ledger_text(b_rec.get("type")),
                     "since_scene": _ledger_text(b_rec.get("since_scene"))})
    # Bonds after feelings, each group by its own key, so the table has a stable
    # order across reads -- a row that moves between two identical-looking loads
    # is a row the reader stops trusting.
    rows.sort(key=lambda r: (r["kind"] == "bond", r["id"]))
    return rows


#: How many timeline rows a listing returns, newest first, after any pair
#: filter. A cap rather than paging, for `JOURNAL_PAGE`'s reason: this is a
#: "how did these two get here" view, and the store retains by age and by bytes
#: (`relationship_history.RETENTION` / `MAX_BYTES`), so what falls off its end
#: is not something a bare offset could name either. Higher than the journal's
#: because a row here carries two short standings rather than a rendered diff of
#: a record body, and because narrowing to one pair is the intended use.
RELATIONSHIP_HISTORY_PAGE = 200


@router.get("/campaigns/{cid}/relationships/history")
def get_relationship_history(cid: str, a: str | None = None, b: str | None = None):
    """How each standing on the ledger got there: one row per applied feeling or
    bond delta, newest first (#63).

    `_ledger_relationships` above answers where two people stand *now*; this
    answers what moved them, which `relationships.json` overwrites and cannot
    say. Pass `a` and `b` (actor tokens) for one pair — matched unordered, so a
    directed feeling is returned in both directions along with the bond, since
    "what has passed between these two" is one question. Both or neither: one
    alone is a 400 rather than a quiet fall back to everything.

    Every field is coerced and the names are resolved tolerantly, the two rules
    `_journal_row` and `_ledger_relationships` respectively state: the file is
    hand-editable and read by a bare `json.loads`, and a card that will not
    parse must cost a name rather than the view.
    """
    _campaign_root_or_404(cid)
    # A pair is both tokens or neither. One alone would silently answer with the
    # unfiltered timeline -- up to `RELATIONSHIP_HISTORY_PAGE` rows for pairs the
    # caller did not ask about, looking exactly like a filtered response.
    if bool(a) != bool(b):
        raise HTTPException(status_code=400,
                            detail="filtering by pair needs both actor tokens, "
                                   "`a` and `b` — half a pair names no pair")
    entries = (store.relationship_history.for_pair(cid, a, b)
               if a and b else store.relationship_history.read(cid))
    entries = entries[-RELATIONSHIP_HISTORY_PAGE:]
    try:
        scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
    except Exception:  # noqa: BLE001 — one unreadable scene header: titles degrade, no 500
        # `list_scenes` opens every scene's header, so a single file a sync
        # client mangled raises out of it — and would take this whole timeline
        # with it, including the rows of every healthy scene, over a label.
        scenes_by_id = {}
    try:
        chron = store.chronicle.read_chronicle(cid)
    except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
        chron = {}
    # And a chronicle.json that PARSES but is not an object degrades the same
    # way. `read_chronicle` hands back whatever `json.loads` returned, so a
    # hand-edited list reaches the `.get` below and raises past the handler
    # above — a date label nobody would miss, turning a valid request into a
    # 500. Each record is re-checked at the row for the same reason.
    if not isinstance(chron, dict):
        chron = {}
    names: dict[str, str] = {}

    def _name(token: str) -> str:
        if token not in names:
            try:
                names[token] = store.relationships.actor_name(cid, token)
            except Exception:  # noqa: BLE001 — an unreadable card costs a name, not the row
                names[token] = token.partition(":")[2] or token
        return names[token]

    out: list[dict] = []
    for e in reversed(entries):
        atok, btok = _ledger_text(e.get("a")), _ledger_text(e.get("b"))
        sid = _ledger_text(e.get("scene"))
        # A row whose scene has been DELETED is not resolved against either
        # join: ids are recycled, so whatever holds this one now is a different
        # scene, and lending the row its title and date would be the plainest
        # possible lie about where a standing came from. The id itself is kept
        # and shown, which is what an unresolvable id has always rendered as.
        gone = bool(e.get("scene_gone"))
        sc = {} if gone else scenes_by_id.get(sid, {})
        c = None if gone else chron.get(sid)
        c = c if isinstance(c, dict) else {}
        out.append({
            "id": _ledger_text(e.get("id")), "ts": _ledger_text(e.get("ts")),
            "source": _ledger_text(e.get("source")),
            # Anything but the two known kinds renders as a feeling would read
            # wrong; an unknown kind is passed through so the row still says
            # what the file says rather than being silently relabelled.
            "kind": _ledger_text(e.get("kind")),
            "a": atok, "b": btok, "a_name": _name(atok), "b_name": _name(btok),
            "label": _ledger_text(e.get("label")),
            "before": _ledger_text(e.get("before")), "after": _ledger_text(e.get("after")),
            "scene": {"id": sid, "title": sc.get("title", sid), "date": c.get("date", "")},
        })
    return out


@router.get("/campaigns/{cid}/provenance")
def get_provenance(cid: str):
    """Why each continuity line is there: the quote, speaker and certainty
    behind every edit that landed, keyed `"<kind>/<id>#<field>"`.

    `absorb/parse.py` has always asked the extractor to cite itself and
    `absorb/routing.py` has always weighed those citations into a band — and
    both were thrown away the moment the edit applied, so the citation existed
    for exactly as long as the review row you judged it on. Keeping it costs
    disk and no tokens.

    One flat read of one file: unlike the ledger this joins nothing, so it needs
    neither the lock nor the per-section tolerance. `store.provenance.read` is
    itself tolerant of a garbled file — this backs display markers, and one bad
    byte must cost the markers rather than the page.

    A field with no entry is the normal case and always will be: the later
    absorb phases rest on no transcript citation, records written before this
    existed have none, and a hand-edited record has none either. The client
    renders those as uncited rather than hiding them.
    """
    _campaign_root_or_404(cid)
    data = store.provenance.read(cid)
    if not data:
        return data
    # The recording scene is labelled here rather than at write time, for the
    # reason `get_ledger` labels its own: a scene can be renamed, and a title
    # frozen into the citation would then name a scene that no longer exists.
    # Tolerant — an unreadable scene list costs the labels, not the markers.
    try:
        titles = {s["id"]: s["title"] for s in store.scenes.list_scenes(cid)}
    except Exception:  # noqa: BLE001 — labels degrade, the citations do not
        titles = {}
    return {k: {**v, "scene_title": titles.get(v.get("scene"), v.get("scene", ""))}
            if isinstance(v, dict) else v
            for k, v in data.items()}


def _effective_or_physical(effective, physical) -> list:
    """The effective rows, or -- when the continuity side raises -- the
    physical rows with `aliases: []`, so one shape reaches the page either
    way. The physical read is not guarded here: a garbled plot.json or
    commitments.json raises out of both, and the caller's `_tolerant` empties
    that section exactly as it always has."""
    try:
        return effective()
    except Exception:  # noqa: BLE001 — a continuity-side failure costs the merge, never the section (spec §3.9)
        return [{**row, "aliases": []} if isinstance(row, dict) else row
                for row in physical()]


@router.get("/campaigns/{cid}/ledger")
def get_ledger(cid: str):
    """The continuity ledger (#117): what the campaign still owes, in one read.

    Six sections, and one route rather than six, because they are read together
    and share one failure policy — a garbled plot.json, commitments.json,
    facts.json or relationships.json costs its own section and nothing else, the
    same tolerance `plot.render_open` and `get_changes` already apply. Splitting
    them would put that policy in six places and make the page reason about six
    loading states to render one view.

    Each thread, commitment and standing fact carries the scene it came from,
    resolved the same way `get_changes` resolves its labels: the title from the
    scene list, the in-fiction date from the chronicle. For a thread or a
    commitment that is the scene that last moved it; for a fact it is the scene
    that recorded it, since a fact's text never changes after that (#114) — a
    fact that stopped being true is retired rather than rewritten.

    `retired` is that other half, and it is the reason this route grew: a
    retired fact and its `superseded_by` pointer have been on disk since #114
    and never left the server, so the one thing the ledger keeps that a snapshot
    cannot — the chain saying WHEN a truth stopped being true and what replaced
    it — was unreadable from the client. Its rows carry two resolved scenes, the
    one that recorded the fact and the one that ended it, because a retired row
    is dated twice and both dates are the point of it.

    Contradictions are the section this view is named for and are absent until
    #111 gives them a record.

    Every plot and commitment row carries an `aging` block (#103): `overdue`
    once a parseable `due` is behind the campaign's clock, `stale` once nothing
    has moved the record for longer than this campaign's `stale_after_days`.
    Computed at read time and never stored — see `store.aging`, which argues
    that — so a corrected clock or an edited scene date changes the answer on
    the next read rather than leaving a stamp nothing recomputes.

    The plot and commitment rows are the EFFECTIVE ones (capstone spec §12.5):
    each carries its canonical id, the group's merged `last_scene` and
    `latest_beat`, and `aliases: [{ref, title, status}]` naming what was merged
    into it -- empty for a record nobody merged, which is every record of a
    campaign with no aliases, so for those the rows are what they always were
    plus that one key. Aging runs on these rows, so a merged record is as stale
    as its group rather than as its oldest member. A merged-away source is not
    a top-level row: it is the same obligation under another name, and listing
    it twice would show the campaign owing it twice, and hand the reader a
    second row whose edit would land on a record the projection no longer
    shows. It stays reachable through its canonical's `aliases`. Edit, quick
    close and delete already address the canonical (`routes/ledger.py`'s
    `_live_target` and `_refuse_unsafe_delete`), so the row ids served here
    are the ones those writes expect. A continuity-side failure degrades to
    the physical rows (spec §3.9): it costs the merge, never the section.
    """
    _campaign_root_or_404(cid)

    def _tolerant(read):
        try:
            return read()
        except Exception:  # noqa: BLE001 — a garbled file empties its section, not the view
            return []

    # All seven sources are read under ONE campaign lock, because they are seven
    # files and a save writes them one after another: `put_chronicle` holds this
    # same lock while it records the chronicle and then applies the absorb's
    # plot, commitment, fact and relationship edits. Reading without it can catch
    # that sequence half done and return a new fact beside the still-open
    # commitment the very same save fulfilled — or, since #114, beside the
    # standing fact that same save retired -- and the page keeps that
    # contradictory snapshot until something else bumps its revision. A
    # continuity view that contradicts itself is worse than one that is a moment
    # stale, which is the whole reason the writer takes the lock across the
    # sequence rather than per file.
    #
    # The two halves of the fact ledger are read here for a sharper version of
    # the same reason: `facts.record` retires a fact and files its replacement in
    # ONE write, so an unlocked pair of reads is exactly where a supersession
    # chain shows up with both ends standing, or with neither.
    #
    # It is a read, so it holds the lock only for the reads: the projections
    # below work on data already in hand.
    with store.locks.campaign_lock(cid):
        scenes_by_id = {s["id"]: s for s in store.scenes.list_scenes(cid)}
        try:
            chron = store.chronicle.read_chronicle(cid)
        except Exception:  # noqa: BLE001 — garbled chronicle.json: labels degrade, no 500
            chron = {}
        # The ledger can show finished rows on demand; prompt context still
        # uses the default open-only projections from these store readers.
        open_threads = _tolerant(lambda: _effective_or_physical(
            lambda: continuity_effective.threads(cid, include_closed=True),
            lambda: store.plot.open_threads(cid, include_closed=True)))
        owed = _tolerant(lambda: _effective_or_physical(
            lambda: continuity_effective.commitments(cid, include_resolved=True),
            lambda: store.commitments.open_commitments(cid, include_resolved=True)))
        standing = _tolerant(lambda: store.facts.active(cid))
        ended = _tolerant(lambda: store.facts.retired(cid))
        # The whole projection sits inside `_tolerant`, not just the read: it
        # resolves a name per actor off the cards, so a broken card empties this
        # section rather than 500ing the view around it.
        bonds = _tolerant(lambda: _ledger_relationships(cid))
        # Read under the lock with everything else, so the moment the rows are
        # aged against is the same present the rows were read in. It is a read
        # of one small file, and it does no calendar work -- which is why the
        # provider resolution that `aging.prepare` does waits until the lock is
        # released, the cut every other calendar caller in this app makes.
        clock_now = store.clock.now(cid)
    # Unparseable is not the only way that file can be wrong. `read_chronicle`
    # is a bare `json.loads`, so a chronicle.json holding `[]` -- valid JSON of
    # the wrong shape -- returns a list and raises nothing, and the `.get` below
    # would then 500 the whole view for any campaign with one open thread. The
    # shape is checked where it is used rather than trusted from the read.
    if not isinstance(chron, dict):
        chron = {}

    # The text coercion these projections run on now lives at module scope
    # (`_ledger_text`), because the relationships projection needs the same rule
    # and it has to be the same rule. Bound to the local name the projections
    # below already use.
    _txt = _ledger_text

    def _scene(sid) -> dict:
        # The row's OWN scene id is the third place a wrong shape can arrive,
        # and the one the two checks above do not reach: these projections run
        # outside `_tolerant`, so a commitment whose `last_scene` is `[]` reaches
        # `scenes_by_id.get` as an unhashable key and 500s the view. Coerced per
        # row rather than guarded per section, so one malformed record loses its
        # scene label instead of emptying the section around it.
        #
        # The label's own fields go through `_txt` for the reason the row fields
        # do: `LedgerPanel.sceneNote` interpolates them, so a non-string `date`
        # in a hand-edited chronicle reaches the panel and renders as
        # `[object Object]`. This nested projection feeds the plot and
        # commitment sections, which the recent-facts coercion never touched.
        if not isinstance(sid, str):
            sid = ""
        s = scenes_by_id.get(sid, {})
        c = chron.get(sid)
        c = c if isinstance(c, dict) else {}   # a per-scene entry can be wrong too
        return {"id": sid, "title": _txt(s.get("title"), sid), "date": _txt(c.get("date"))}

    threads = [{**t, "id": _txt(t.get("id")), "title": _txt(t.get("title"), _txt(t.get("id"))),
                "status": _txt(t.get("status"), "open"),
                "last_scene": _txt(t.get("last_scene")),
                "latest_beat": _txt(t.get("latest_beat"))}
               for t in open_threads if isinstance(t, dict)]
    # One context for both lists (#103), so a thread and a commitment last moved
    # by the same scene cannot report different ages. `_tolerant` around it for
    # the reason every other read here has one: the aging pass resolves a
    # calendar provider, and a campaign whose provider is a broken plugin should
    # lose its badges, not its ledger.
    try:
        ctx = store.aging.prepare(cid, clock_now)
    except Exception:  # noqa: BLE001 — a broken calendar plugin costs the badges
        ctx = None     # (`_tolerant` is for the section reads; this empties no section)
    if ctx is not None:
        threads = store.aging.annotate(ctx, threads)
        owed = store.aging.annotate(ctx, owed)
    # Derived from `chron` rather than `chronicle.recent`, which sorts on the raw
    # `id` of every record: one list-valued id makes that comparison raise and
    # `_tolerant` empties the entire recent-facts section, losing every good fact
    # to one bad row. Sorting on a coerced key keeps the rest. It also saves a
    # second read of the same file.
    recent = sorted((r for r in chron.values() if isinstance(r, dict)),
                    key=lambda r: _txt(r.get("id")))[-LEDGER_RECENT:]
    return {
        # `stale_after` beside the rows rather than inside each one: it is a
        # property of the campaign, and a panel that says "40 days untouched"
        # needs to be able to say what this campaign calls stale.
        "stale_after_days": ctx["stale_after"] if ctx is not None
        else store.calendars.STALE_AFTER_DAYS,
        "plot": [{**t, "scene": _scene(t["last_scene"])} for t in threads],
        "commitments": [{**c, "scene": _scene(c["last_scene"])} for c in owed],
        # The fact ledger (#114). `facts.active` normalizes its own rows, like
        # `open_commitments` does, so only the scene label is resolved here.
        "facts": [{**f, "scene": _scene(f["scene"])} for f in standing],
        # The other half of the same file: the facts that stopped being true.
        # BOTH scenes are resolved and neither is redundant -- `scene` is still
        # the one that RECORDED the fact, so a retired row keeps its dated place
        # in the ledger, and `retired_scene` is the one that ended it, which is
        # the only thing on the row saying when it stopped. `superseded_by` stays
        # a bare fact id: it points INTO this same response, and inlining the
        # replacement's text here would ship one sentence twice and let the two
        # copies disagree the moment either is hand-edited.
        "retired": [{**f, "scene": _scene(f["scene"]),
                     "retired_scene": _scene(f["retired_scene"])} for f in ended],
        # `since_scene` is a bond's only date and is blank for every feeling,
        # which resolves to the empty label -- the same row shape the sections
        # above carry, so one renderer serves all of them.
        "relationships": [{**r, "scene": _scene(r["since_scene"])} for r in bonds],
        # Newest first: `chronicle.recent` returns the tail in ascending order,
        # which is right for a recap read forward and backwards for a ledger.
        # `one_line or summary`, the fallback every other chronicle consumer
        # uses (`context/story.py:_story_entries`): a save may leave `one_line`
        # empty, and a row with only its scene metadata is a blank line in the
        # panel rather than a fact.
        # `_scene` carries the same id coercion for the two sections above; a
        # chronicle record's `id` needs it for the same reason, and its text
        # needs `_txt` for the same reason the plot rows above do.
        "chronicle": [{**_scene(r.get("id")),
                       "one_line": _txt(r.get("one_line")) or _txt(r.get("summary")),
                       "date": _txt(r.get("date"))}
                      for r in reversed(recent)],
    }


