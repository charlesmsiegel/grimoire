"""Read a grimoire transcript back in as a scene (#92).

Two shapes, both of them grimoire's own: the scene file the store writes under
``<campaign>/scenes/`` (frontmatter, then ``**Speaker:**`` blocks) and the
chapter file ``export.build_markdown_bundle`` puts in a bundle (a ``# 1.
Title`` heading, an italic date/location line, a ``**Cast:**`` line, then the
same blocks). Anything else -- a SillyTavern log, a Claude.ai export -- is a
segmentation and speaker-attribution problem, not a parsing one: that is the
``ingest-campaign-log`` skill's judgement call, and #92 chose deliberately not
to guess at it here.

Split in two like ``lorebook``, and for the same reason: ``parse`` reads a file
into a draft and writes **nothing**, ``commit`` writes the draft a reviewer
approved. Everything this format cannot settle on its own -- a speaker who
names nobody in this campaign, a location this campaign does not have, a date
its calendar cannot read even in the form the exporter wrote -- is reported by
``parse`` as a warning or an unmatched label instead of being guessed at, so
the review step is a real gate rather than a confirmation dialog over work
already done.

What it does *not* do is treat "I cannot parse this" as "this cannot be read".
A bundle chapter carries the calendar's friendly rendering ("2 January 2026"),
which no provider's ``parse`` accepts -- but ``calendars.resolve`` reads back
any form the calendar itself renders, and the header line's one genuinely
ambiguous case (a lone bit that could be a date or a place) is settled by
asking the two authorities that know, the calendar and the location roster.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Sequence

# `scenes` is reached for three private names -- `_parse_messages`, `_markers`
# and `_MARKER`. Deliberate, and the same call `store.alternates` makes for the
# first of them: the marker grammar has exactly one definition, and a second
# copy of it here would be a parser that agrees with the store's reader only
# until one of the two changes. They are re-exported by the package's `__init__`
# rather than reached into a submodule for.
from . import appearances, calendars, clock, context, locks, overlay, scenes
from .campaigns import paths as campaigns_paths
from .frontmatter import parse_frontmatter


class SceneImportError(Exception):
    """The upload is not a grimoire transcript."""


class TranscriptTooLargeError(SceneImportError):
    """The upload is bigger than `MAX_BYTES` (HTTP 413).

    Spelled with the suffix ruff's N818 asks for, unlike its older siblings
    (`covers.CoverTooLarge`, `image_library.ImageTooLarge`) which predate the
    widened rule selection and sit in the lint baseline. The baseline may only
    shrink, so a new one has to be named the way the rule wants.
    """


#: Bounds the ALLOCATION, not the receipt: `parse` materializes the whole
#: upload as one `bytes` and then again as one `str`, and this backend is
#: packaged verbatim into the Android app (Chaquopy), where an unbounded read
#: OOMs the process before a 413 could be composed -- the same reasoning, and
#: the same belt-and-braces shape, as `covers.MAX_BYTES`: the route checks
#: `UploadFile.size` before reading, and this re-checks the bytes, because
#: `size` is Optional in the ASGI contract.
#:
#: 16 MB is far past any real transcript. The longest scene in a played
#: campaign is a few hundred KB of text.
MAX_BYTES = 16 * 1024 * 1024
TOO_LARGE = "transcript is too large (max 16 MB)"

#: Bounds the COMMIT, whose body is plain JSON and so never met `MAX_BYTES`.
#: Every message is a block in one scene file that the app renders, absorbs and
#: rewrites whole; past a few thousand the store's own advice is to break the
#: log into scenes, which is what the ingest skill does. Without a cap one
#: request can hold the campaign lock for minutes.
MAX_MESSAGES = 5000
TOO_MANY = f"too many messages for one scene (max {MAX_MESSAGES}) — split the log into scenes"


#: The chapter heading `build_markdown_bundle` writes: `# 3. The Long Quay`.
#: The number is the bundle's own chapter marker (`toc_label`), not part of the
#: title, and re-importing it into the title is how "3. The Long Quay" becomes a
#: scene name.
_CHAPTER_NUMBER = re.compile(r"^\d+\.\s+")
#: How `export._header_lines` joins a chapter's date to its location.
_META_JOIN = " — "
_CAST_LABEL = "**Cast:**"


def _decoded(data: bytes) -> str:
    """The upload as text, with line endings normalized.

    ``utf-8-sig`` because an editor on Windows writes the BOM and a leading
    ``\\ufeff`` stops ``parse_frontmatter`` recognising the opening fence, which
    turns the whole frontmatter block into transcript. ``\\r\\n`` for the same
    reason one step later: ``_MARKER`` is anchored per line, but ``_SAFE_LABEL``
    and the blank-line rule in ``_markers`` are both written against ``\\n``.
    """
    if len(data) > MAX_BYTES:
        raise TranscriptTooLargeError(TOO_LARGE)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SceneImportError(f"not a UTF-8 text file: {exc}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_chapter_header(body: str, roster: list[str]) -> tuple[dict, str]:
    """A bundle chapter's header, and the transcript under it.

    Only entered when the body opens with a ``# `` heading, which a stored
    scene file never does -- its body starts at the first marker. That
    condition is what makes it safe to read a ``**Cast:**`` line as a header
    line at all: it matches ``_MARKER`` as cleanly as any speaker does, so
    outside this shape it stays what it looks like, a message from someone
    called Cast.
    """
    lines = body.split("\n")
    if not lines or not lines[0].startswith("# "):
        return {}, body
    head: dict = {"title": _CHAPTER_NUMBER.sub("", lines[0][2:].strip())}
    i = 1
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("> "):
            i += 1                      # blank, or the epigraph absorb rewrites anyway
        elif line.startswith(_CAST_LABEL):
            head["cast"] = _cast_names(line[len(_CAST_LABEL):], roster)
            i += 1
        elif line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            head["meta"] = line.strip("*").strip()
            i += 1
        else:
            break                       # the first marker: the transcript starts here
    return head, "\n".join(lines[i:])


def _cast_names(line: str, roster: list[str]) -> list[str]:
    """The names in a `**Cast:**` line, whose separator is also a legal
    character in a name.

    `export._header_lines` joins with ", " and escapes nothing, so a character
    called "Winifred, the Grey" is written into the middle of the list and a
    bare split shreds it into "Winifred" (which may mis-resolve onto a
    different Winifred by word-boundary prefix) plus a phantom "the Grey" for
    the reviewer to puzzle over. Kept whole where the whole line is a name;
    otherwise split, because the ambiguity is real and the split is right for
    every name that does not contain a comma. Fully unambiguous parsing would
    need the exporter to quote, which is a change to the written format.
    """
    whole = line.strip()
    if whole and "," in whole and any(n.strip().lower() == whole.lower() for n in roster):
        return [whole]                    # the whole line IS one roster name
    return [n.strip() for n in line.split(",") if n.strip()]


def _meta_bits(cid: str, meta: str) -> tuple[str, str, list[str]]:
    """(date, location id, warnings) from a chapter's italic header line.

    Two bits are unambiguous -- `_header_lines` writes date then location.

    ONE bit is the interesting case, and it is answerable rather than a guess:
    `_header_lines` drops whichever of the two the scene lacked, so "2 January
    2026" and "Saltmarch" arrive in the same position. Both authorities are
    right here -- the campaign's calendar knows whether a string is a date it
    could have written (that is what `calendars.resolve` is), and the location
    roster knows whether it is a place. So the bit is offered to each in turn
    and placed by whichever one claims it; only a bit that neither recognises
    is reported for the reviewer to place, which is the honest remainder rather
    than the whole case.

    Deliberately date-first: a location NAMED like a date would otherwise have
    to be distinguished from a date, and no evidence available here could.
    """
    parts = [p.strip() for p in meta.split(_META_JOIN)]
    if len(parts) >= 2:
        # The separator is legal inside a location NAME ("The Quay — Lower
        # Deck"), and `_header_lines` escapes nothing, so a dateless scene at
        # such a place writes a line that splits into two bogus halves. Asked
        # whole first, exactly as `_cast_names` asks about commas.
        whole, _ = _resolve_location(cid, meta)
        if whole:
            return "", whole, []
        date, hints = _canonical_date(cid, parts[0])
        location, place_hints = _resolve_location(cid, _META_JOIN.join(parts[1:]))
        return date, location, hints + place_hints
    date, _ = _canonical_date(cid, parts[0])
    if date:
        return date, "", []
    location, _ = _resolve_location(cid, parts[0])
    if location:
        return "", location, []
    return "", "", [(f"“{meta}” is neither a date this campaign's calendar can read "
                     "nor a location it has — set the scene's date and place below.")]


def _anchor(cid: str) -> str:
    """A date to read a friendly rendering NEAR.

    `resolve` scans a window rather than parsing, because "2 Tevet" is only
    unambiguous near a year -- so it needs somewhere to look. The campaign's
    own clock first (one "now" for the whole campaign, moved deliberately),
    then the most recent dated scene.

    A campaign with neither has no window, and the friendly form of a date then
    genuinely cannot be recovered: nothing here knows what year "2 January"
    belongs to. That is the honest limit and `_canonical_date` reports it --
    and it is the FIRST import into an empty campaign, where there is also
    nothing to be inconsistent with, so the reviewer simply sets the date.
    """
    if clock.now(cid):
        return clock.now(cid)
    for meta in scenes.list_scenes(cid):          # most-recently-updated first
        if meta.get("date"):
            return meta["date"]
    return ""


def _canonical_date(cid: str, candidate: str) -> tuple[str, list[str]]:
    """`candidate` in the campaign's primary calendar, or "" and a warning.

    `resolve`, not `normalize`, and the difference is the feature's headline
    round trip. A stored scene's ``time_history`` is already canonical and
    either reads back; a bundle chapter carries the calendar's *friendly*
    rendering ("2 January 2026"), which no provider's `parse` accepts -- so
    `normalize` lost the date on every exported chapter, for every calendar
    including the default. `resolve` exists for exactly that ("accepting any
    form THIS calendar renders"), and it is calendar-agnostic by construction:
    the only strings it adds are ones the provider itself produced.

    Settled here, where nothing is written and no lock is held, so the draft
    the reviewer edits only ever holds a date the commit can actually set --
    and so a hand-written provider's code never runs under the campaign lock
    (see `scenes.lifecycle._date_hint`).
    """
    if not candidate:
        return "", []
    try:
        cfg = calendars.read_calendar(campaigns_paths.campaign_root(cid))
        provider = calendars.get_provider(cfg["primary"])
        return calendars.resolve(provider, candidate, near=_anchor(cid)), []
    except (calendars.CalendarError, KeyError):
        return "", [(f"“{candidate}” is not a date this campaign's calendar can read — "
                     "set the scene's date below.")]


def _actors(cid: str) -> list[dict]:
    """Every actor a speaker label could name, with the role they are already
    locked to. A character seated as the player in an earlier scene keeps that
    role here: `appear` refuses a role that disagrees with the lock, so
    defaulting them to "npc" would drop them from the cast at commit time."""
    locked = {f"{r['kind']}/{r['id']}": r["role"] for r in appearances.roster(cid)}
    out: list[dict] = []
    for kind, listing in (("characters", overlay.list_characters(cid)),
                          ("pcs", overlay.list_pcs(cid))):
        out.extend({"kind": kind, "id": a["id"], "name": a.get("name") or a["id"],
                    "role": locked.get(f"{kind}/{a['id']}",
                                       "player" if kind == "pcs" else "npc")}
                   for a in listing)
    return out


def _speaker_labels(messages: list[dict], declared: list[str]) -> list[str]:
    """The names this transcript casts, in the order they first speak.

    Sub-speaker parentheticals are stripped (``Mara (aside)`` is Mara) and the
    two synthetic speakers are skipped: ``⁣Scene`` and ``⁣Roll`` tag lines no
    actor said, and offering either as cast would seat a character named after
    the store's own internal metadata.
    """
    labels: list[str] = []
    for name in ([m.get("speaker") for m in messages] + declared):
        if not name or name in scenes.SYNTHETIC_SPEAKERS:
            continue
        base = scenes.speaker_base(name)
        if base and base not in labels:
            labels.append(base)
    return labels


def _suggest_cast(actors: list[dict], labels: list[str]) -> tuple[list[dict], list[str]]:
    """(matched cast, labels that matched nobody) for this campaign's roster.

    `scenes.match_name` is the resolver the transcript itself is read with, so
    a label lands on the actor the scene will actually attribute it to -- and
    an ambiguous one ("Winifred", with two Winifreds in the campaign) matches
    nobody here rather than being handed to whichever came first.
    """
    names = [a["name"] for a in actors]
    cast, unmatched, seated = [], [], set()
    for label in labels:
        name = scenes.match_name(label, names)
        hits = [a for a in actors if a["name"] == name] if name else []
        if len(hits) != 1:
            unmatched.append(label)
            continue
        # One actor, one seat. A transcript that writes both "Mara" and "Mara
        # Tidewright" resolves both to the same character, and two entries for
        # one actor are two rows the reviewer has to tick, two seats the commit
        # asks for (the second a no-op), and -- since the review form keys its
        # rows by actor -- two rows that toggle each other. The first label
        # wins, which is the one the transcript uses first.
        ref = f"{hits[0]['kind']}/{hits[0]['id']}"
        if ref not in seated:
            seated.add(ref)
            cast.append({"label": label, **hits[0]})
    return cast, unmatched


def _resolve_location(cid: str, name: str) -> tuple[str, list[str]]:
    """A campaign location id for the *name* a bundle chapter carries."""
    if not name:
        return "", []
    low = name.strip().lower()
    hits = [e["id"] for e in overlay.list_entities(cid, "locations")
            if (e.get("name") or "").strip().lower() == low]
    if len(hits) == 1:
        return hits[0], []
    return "", [(f"this campaign has {'no' if not hits else 'more than one'} location "
                 f"called “{name}” — pick the scene's location below.")]


def _known_location(cid: str, eid: str) -> tuple[str, list[str]]:
    """A location ID from a scene file's frontmatter, checked against THIS
    campaign.

    A stored scene carries the id of a location in the campaign it came from,
    and an import is exactly the case where that campaign is a different one.
    Unchecked, the id reaches the review form as a value the `<select>` cannot
    offer -- so the form shows no location, the reviewer is told nothing, and
    the scene is imported placeless. Dropped and named here, the same way an
    unresolvable location NAME is.
    """
    if not eid:
        return "", []
    if any(e["id"] == eid for e in overlay.list_entities(cid, "locations")):
        return eid, []
    return "", [(f"this campaign has no location “{eid}” — it is probably from the "
                 "campaign this scene was exported from. Pick the scene's location below.")]


def _dropped_text(transcript: str) -> list[str]:
    """Warnings for text the marker grammar will not read as its own message.

    Two ways to lose a line silently, and an import is where both actually
    happen: prose before the first marker (a bundle's own preamble, a note
    somebody typed at the top) is not part of any message, and a marker that
    is not preceded by a blank line is not a marker at all -- `_markers`
    requires the separator the serializer always writes, so a label pasted
    directly under the previous line is read as that speaker's content. Neither
    is an error: the transcript still imports. But the reviewer is told, since
    the alternative is finding out by reading the scene afterwards.

    The second reading is deliberately ambiguous and says so. A line of prose
    that opens `**Note:** ...` is a real bold label inside a real message, and
    counting it is a false positive -- but the same shape is what a file that
    lost its blank lines looks like, and only the reader knows which they have.
    """
    warnings = []
    markers = scenes._markers(transcript)
    if markers and transcript[:markers[0].start()].strip():
        warnings.append("the text before the first **Speaker:** block is not part of "
                        "any message and was left out.")
    folded = len(scenes._MARKER.findall(transcript)) - len(markers)
    if folded > 0:
        warnings.append(f"{folded} bold label(s) sit directly under the line above rather "
                        "than after a blank line, so they were read as part of that "
                        "message instead of starting a new one — right if they are part "
                        "of the text, wrong if the file lost its blank lines.")
    return warnings


def _untag_rolls(messages: list[dict]) -> list[str]:
    """Strip the `⁣Roll` tag from imported messages, in place.

    That tag is a PROMISE, not decoration: `edit_message` refuses a tagged
    message outright (`RollMessageImmutable`) and reroll refuses to step past
    one, both because "its transcript line must stay in lockstep with
    rolls.json" (`serialize.py`). An import creates no `rolls.json` entry --
    there is no roll to be in lockstep with -- so importing the tag makes that
    promise false and leaves the message permanently uneditable and the scene
    permanently unrerollable past it, for a line that is now just text.

    The line itself is kept verbatim; only the internal tag goes, which is what
    the text always looked like to a reader (the tag is never displayed). The
    transition tag is deliberately NOT stripped: it carries no such guarantee,
    it is drift metadata, and the scene's own transitions belong to it.
    """
    rolls = [m for m in messages if m.get("speaker") == scenes.ROLL_SPEAKER]
    for m in rolls:
        m.pop("speaker", None)
    if not rolls:
        return []
    return [(f"{len(rolls)} manual dice-roll line(s) import as ordinary text: this "
             "campaign's roll log has no entry for them, and keeping them tagged "
             "would freeze those messages against editing.")]


def _carryable_turn_sizes(meta: dict, messages: list[dict]) -> list[int] | None:
    """The source's reply boundaries, when they still describe these messages.

    A stored scene file carries `turn_sizes` (written by `append_reply`), and it
    is what tells a reroll how many blocks the LAST generation had. Dropped, the
    scene is untracked and the first reroll takes the untracked branch, which
    removes the whole trailing model run -- so importing a scene that ends in a
    three-speaker reply and rerolling it eats all three.

    Carried only when `_tracked_suffix_fits` says the list can still describe
    this transcript, which is the same check every repair path uses. A list
    that does not fit is worse than none (`TurnSizesDesynced` exists to say
    so), so it is dropped rather than guessed at.
    """
    # The store's own strict parse, not a second copy of it: `_parse_turn_sizes`
    # is where "a field that cannot be trusted end to end means no tracking"
    # lives, and two copies is two places for that rule to drift -- the same
    # argument `parse` makes about `histories` a few lines above.
    sizes = scenes._parse_turn_sizes(str(meta.get("turn_sizes", "")))
    if not sizes:
        return None
    return sizes if scenes._tracked_suffix_fits(messages, sizes) else None


def _macro_notice(messages: list[dict]) -> list[str]:
    """Say that `{{...}}` text will not survive the import verbatim.

    The import resolves macros once at write time (#137), which for
    `{{roll:1d20}}` is the whole point -- but the same pass DROPS any braced
    token the scene cannot resolve, so `{{ignis}}` in imported prose simply
    disappears. The review pane shows the reviewer the unexpanded text and
    tells them the posts import unchanged, so the one case where that is untrue
    has to be named rather than discovered afterwards.
    """
    n = sum(1 for m in messages if "{{" in m.get("content", ""))
    return [] if not n else [
        (f"{n} post(s) contain {{{{…}}}} macros: dice and random macros are rolled once "
         "on import, and any other braced token is dropped — the rest of the text is "
         "imported exactly as it is.")]


def _moves_left_behind(times: list[str], places: list[str]) -> list[str]:
    """A warning when the file records a scene that MOVED.

    Only the first entry of each history is carried: it is the scene's start,
    which is what the id is stamped from and what the rail shows. Replaying the
    rest is not available -- `set_location` and `set_datetime` append their own
    transition line for every move after the first, so importing a three-stop
    history would write three transitions into a transcript that already
    contains them.

    The transitions the file's own transcript carries still import and still
    read, so nothing in the scene is lost; what the imported copy does not have
    is the metadata behind them. Said out loud, because the alternative is a
    reader noticing months later that a scene whose text moves to the quay is
    filed at the keep.
    """
    moves = [(len(h) - 1, what) for h, what in ((times, "moment"), (places, "location"))
             if len(h) > 1]
    return [f"this scene changed {what} {n} more time(s) after it began — only the "
            f"first is carried, though the transitions still read in the transcript."
            for n, what in moves]


def parse(cid: str, data: bytes) -> dict:
    """`data` as a reviewable draft. Writes nothing, in either scope.

    Returns the fields the review form edits (``title``/``date``/``location``/
    ``pcless``), the transcript exactly as it will be written, the cast the
    speaker labels resolve to, the labels that resolved to nobody, and the
    warnings for everything this file could not settle. The draft is a
    proposal: nothing here is committed until `commit` is called with it.
    """
    text = _decoded(data)
    meta, body = parse_frontmatter(text)
    actors = _actors(cid)
    head, transcript = _split_chapter_header(body, [a["name"] for a in actors])

    warnings: list[str] = []
    # The store's own reader, not a second copy of the split: `histories` exists
    # because four copies of `[x for x in raw.split(",") if x]` is four places
    # to forget the empty entry a bare `"".split(",")` produces.
    history = scenes.histories(meta)
    times, places = history["times"], history["locations"]
    date, location = (times[0].strip() if times else ""), (places[0].strip() if places else "")
    warnings += _moves_left_behind(times, places)
    if date or location:
        location, place_hints = _known_location(cid, location)
        warnings += place_hints
    elif head.get("meta"):
        date, location, hints = _meta_bits(cid, head["meta"])
        warnings += hints
    if date and not head.get("meta"):
        # A frontmatter date is already canonical; run it through anyway, so a
        # hand-edited one that the calendar cannot read is caught here rather
        # than deep inside the commit.
        date, date_hints = _canonical_date(cid, date)
        warnings += date_hints

    messages = scenes._parse_messages(transcript, frozenset())
    if not messages:
        raise SceneImportError(
            "no **Speaker:** blocks found — this is not a grimoire transcript")
    warnings += _dropped_text(transcript)
    labels = _speaker_labels(messages, head.get("cast", []))
    # After the labels are read off them, and before the sizes are measured
    # against them: an untagged roll line is an ordinary assistant block, which
    # is what `_model_blocks` counts.
    warnings += _untag_rolls(messages)
    warnings += _macro_notice(messages)

    cast, unmatched = _suggest_cast(actors, labels)
    return {
        "title": meta.get("title") or head.get("title") or "Imported scene",
        "date": date,
        "location": location,
        "pcless": meta.get("pcless") == "true",
        "messages": messages,
        # The reply boundaries, when the source had some that still fit. Not a
        # field the review form shows -- there is nothing for a reader to decide
        # about it -- but it rides the draft because only the parse has seen the
        # frontmatter it comes from.
        "turn_sizes": _carryable_turn_sizes(meta, messages),
        "cast": cast,
        "unmatched": unmatched,
        "warnings": warnings,
    }


def _discard(cid: str, sid: str, seated: list[dict]) -> None:
    """Remove the scene a commit could not finish filling, and the seats it took.

    Two halves, and the second was missing. Deleting the scene leaves every
    `appearances` record this commit wrote still naming it -- `delete_scene`
    retires `prompt_log`, `commits`, `turnstate`, `pins` and the alternates
    sidecar for the recycled-id hazard, but never appearances, because until
    now nothing deleted a scene it had already cast. Scene ids ARE recycled:
    `_numbering` reads the files on disk, so retrying a failed import of the
    same file lands on the same id and the retry's brand-new scene opens with
    the failed run's cast already on stage.

    So the seats come off first, while the scene still exists (`leave` narrates
    into a transcript that is about to be deleted, which costs nothing and
    keeps the call the store's own idempotent one). What `leave` does not undo
    is the campaign-wide version lock a first appearance takes -- there is no
    API for that by design, "the actor stays appeared campaign-wide" -- so a
    failed import can still leave an actor on the roster with no scenes. That
    is a state the app already has (cast someone, remove them) rather than a
    phantom cast on a live scene, which is the one that misattributes.

    Best effort throughout: the caller is already raising the failure worth
    reading, and a delete that fails on top of it would replace that error with
    a less useful one.
    """
    # The transcript goes FIRST, and the order is the point: `leave` appends a
    # "*X leaves the scene.*" line to a non-empty transcript, which on a large
    # import is a full parse, read and write per actor -- into a file that is
    # about to be unlinked. With the scene already gone it takes the record off
    # and returns at its own `SceneNotFound`, which is all this needs.
    with contextlib.suppress(OSError, scenes.SceneNotFound, locks.StoreBusy):
        scenes.delete_scene(cid, sid)
    for a in seated:
        with contextlib.suppress(OSError, appearances.AppearError, locks.StoreBusy):
            appearances.leave(cid, sid, a["kind"], a["id"])


def _expanded(cid: str, sid: str, messages: list[dict]) -> list[dict]:
    """The messages as they will be stored, with macros resolved once.

    Outside the campaign lock, deliberately: `expand_macros` resolves the
    campaign's calendar provider -- user-authored plugin code -- and doing that
    per message under a campaign-wide hold is the thing `commit` keeps
    `set_datetime` outside the lock to avoid.

    And only for text that actually carries a macro. Most imported posts carry
    none, so the common case does no calendar work at all and the stored text
    is byte-identical to the file's; without the test, a 5000-post import
    resolved the provider 5000 times to change nothing.
    """
    subs = None
    out = []
    for m in messages:
        content = m.get("content", "")
        if "{{" in content:
            if subs is None:
                subs = context.scene_substitutions(cid, sid)
            content = context.expand_macros(content, subs, cid, sid)
        out.append({"role": m.get("role") or "assistant",
                    "speaker": m.get("speaker") or None, "content": content})
    return out


def commit(cid: str, sid: str, messages: list[dict], date: str = "", location: str = "",
           cast: Sequence[dict] = (), turn_sizes: list[int] | None = None) -> dict:
    """Write a reviewed draft into the freshly created, still-empty scene `sid`.

    **All or nothing, as far as a delete can make it**: either the scene ends
    up holding the whole draft, or it and the seats it took are removed -- and
    where the removal itself fails (see `_discard`) what is left is an empty
    scene rather than a fragment. A scene holding half an imported transcript
    reads exactly like a scene, and nothing downstream -- not the reviewer, not
    absorb -- can tell which half is missing.

    Three things make that true rather than merely intended:

    - **The scene is followed by IDENTITY, not by id.** `set_datetime` renames
      the scene on the first date it sets, and a concurrent rename or a
      width-crossing repad can move it at any point besides; a cleanup that
      deletes the id the caller created deletes nothing and leaves the
      half-scene standing. `scene_identity` is the token the rest of the app
      already follows a moving scene by.
    - **One hold spans the writes.** Not one per message: between two separate
      acquisitions another writer can rename the scene out from under the rest
      of the transcript, which is exactly the fragment above. The calendar step
      stays OUTSIDE it, because resolving a provider runs user-authored plugin
      code and that must never happen under a campaign-wide lock (the same cut
      `scenes.moment.set_datetime` and `lifecycle._date_hint` make).
    - **The transcript is one write.** `append_message` per message is a whole
      file read and write each, so a long import was quadratic and held the
      lock once per post; `scenes.append_messages` does the batch in one.

    Ordered so the transcript comes out exactly as it went in. The moment and
    the place go first: both `set_datetime` and `set_location` append a
    transition line when they CHANGE a setting the scene already had, and are
    silent only on the first one. The cast goes last and never narrates -- an
    imported log never contained "*X joins the scene.*" -- and after the
    rename, so the appearance record is written against the id the scene keeps.

    Macros are resolved once, here, for the reason `post_chat` resolves them at
    persist time (#137): a `{{roll:1d20}}` sitting in an imported transcript
    would otherwise roll a different number into the prompt on every later
    context build. Resolved after the moment is set, so `{{date}}` reads as the
    imported scene's own.

    Returns the scene's id, which is not necessarily the one that came in.
    """
    identity = scenes.scene_identity(cid, sid)
    seated: list[dict] = []
    try:
        if date:
            # Outside the hold: this resolves and runs the campaign's calendar
            # provider, which may be user-authored code.
            sid = scenes.set_datetime(cid, sid, date)["id"]  # raises calendars.CalendarError
        written = _expanded(cid, sid, messages)
        with locks.campaign_lock(cid):
            if location:
                scenes.set_location(cid, sid, location)      # raises entities.EntityNotFound
            # Re-checked here, not trusted from the caller: `parse` and this are
            # two requests over a body the client controls, and boundaries that
            # do not fit make every future reroll on the scene raise
            # `TurnSizesDesynced` with no UI path to repair it.
            fits = turn_sizes if turn_sizes and scenes._tracked_suffix_fits(
                written, turn_sizes) else None
            scenes.append_messages(cid, sid, written, turn_sizes=fits)
            for a in cast:
                appearances.appear(cid, sid, a["kind"], a["id"], a["version"], a["role"],
                                   narrate=False)            # raises appearances.AppearError
                seated.append(a)
    except BaseException:
        # Including the ones this module cannot name -- an OSError mid-write, a
        # store gone busy, a cancellation. The id is re-read from the identity
        # first: by here the scene may have been renamed by this very call.
        # Suppressed, and that matters: `find_by_identity` RAISES
        # `UnreadableError` (an OSError) when it could not read every candidate
        # -- the sync-client and sharing-violation cases it was hardened for.
        # Raised from inside this handler it would skip the discard entirely
        # and replace the real failure with itself, which is the opposite of
        # what this path is for.
        current = sid
        if identity:
            with contextlib.suppress(OSError):
                current = scenes.find_by_identity(cid, identity) or sid
        _discard(cid, current, seated)
        raise
    return {"id": sid, "messages": len(messages), "cast": len(cast)}
