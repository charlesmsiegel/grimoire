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
its calendar cannot read -- is reported by ``parse`` as a warning or an
unmatched label instead of being guessed at, so the review step is a real gate
rather than a confirmation dialog over work already done.
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
from . import appearances, calendars, locks, overlay, scenes
from .campaigns import paths as campaigns_paths
from .frontmatter import parse_frontmatter


class SceneImportError(Exception):
    """The upload is not a grimoire transcript."""


class TranscriptTooLargeError(SceneImportError):
    """The upload is bigger than `MAX_BYTES` (HTTP 413).

    Spelled with the suffix ruff's N818 asks for, unlike its older siblings
    (`covers.CoverTooLarge`, `campaign_images.ImageTooLarge`) which predate the
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


def _split_chapter_header(body: str) -> tuple[dict, str]:
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
            head["cast"] = [n.strip() for n in line[len(_CAST_LABEL):].split(",") if n.strip()]
            i += 1
        elif line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            head["meta"] = line.strip("*").strip()
            i += 1
        else:
            break                       # the first marker: the transcript starts here
    return head, "\n".join(lines[i:])


def _meta_bits(meta: str) -> tuple[str, str, list[str]]:
    """(date, location name, warnings) from a chapter's italic header line.

    `_header_lines` drops whichever of the two the scene lacks, so one bit is
    genuinely ambiguous -- "Saltmarch" and "2 January 2026" arrive in the same
    position, and reading a location as a date (or the reverse) puts a value in
    a field the reviewer then has to notice is wrong. Two bits are unambiguous,
    one is reported and left for the reviewer to place.
    """
    parts = [p.strip() for p in meta.split(_META_JOIN)]
    if len(parts) >= 2:
        return parts[0], _META_JOIN.join(parts[1:]), []
    return "", "", [(f"“{meta}” is either the date or the location — "
                     "the file does not say which, so neither was filled in.")]


def _canonical_date(cid: str, candidate: str) -> tuple[str, list[str]]:
    """`candidate` in the campaign's primary calendar, or "" and a warning.

    A stored scene's ``time_history`` is already canonical and passes through
    untouched; a bundle chapter carries the calendar's *friendly* rendering
    ("2 January 2026"), which most providers do not read back. Settled here,
    where nothing is written and no lock is held, so the draft the reviewer
    edits only ever holds a date the commit can actually set -- and so a
    hand-written provider's code never runs under the campaign lock (see
    `scenes.lifecycle._date_hint`).
    """
    if not candidate:
        return "", []
    try:
        cfg = calendars.read_calendar(campaigns_paths.campaign_root(cid))
        return calendars.normalize(calendars.get_provider(cfg["primary"]), candidate), []
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


def _suggest_cast(cid: str, labels: list[str]) -> tuple[list[dict], list[str]]:
    """(matched cast, labels that matched nobody) for this campaign's roster.

    `scenes.match_name` is the resolver the transcript itself is read with, so
    a label lands on the actor the scene will actually attribute it to -- and
    an ambiguous one ("Winifred", with two Winifreds in the campaign) matches
    nobody here rather than being handed to whichever came first.
    """
    actors = _actors(cid)
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
    """
    warnings = []
    markers = scenes._markers(transcript)
    if markers and transcript[:markers[0].start()].strip():
        warnings.append("the text before the first **Speaker:** block is not part of "
                        "any message and was left out.")
    folded = len(scenes._MARKER.findall(transcript)) - len(markers)
    if folded > 0:
        warnings.append(f"{folded} speaker label(s) are not separated from the line above "
                        "by a blank line, so they were read as part of the previous "
                        "message rather than starting a new one.")
    return warnings


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
    head, transcript = _split_chapter_header(body)

    warnings: list[str] = []
    times = [t for t in meta.get("time_history", "").split(",") if t.strip()]
    places = [p for p in meta.get("location_history", "").split(",") if p.strip()]
    date, location = (times[0].strip() if times else ""), (places[0].strip() if places else "")
    if date or location:
        location, place_hints = _known_location(cid, location)
        warnings += place_hints
    elif head.get("meta"):
        date, location_name, hints = _meta_bits(head["meta"])
        warnings += hints
        location, place_hints = _resolve_location(cid, location_name)
        warnings += place_hints
    date, date_hints = _canonical_date(cid, date)
    warnings += date_hints

    messages = scenes._parse_messages(transcript, frozenset())
    if not messages:
        raise SceneImportError(
            "no **Speaker:** blocks found — this is not a grimoire transcript")
    warnings += _dropped_text(transcript)

    cast, unmatched = _suggest_cast(cid, _speaker_labels(messages, head.get("cast", [])))
    return {
        "title": meta.get("title") or head.get("title") or "Imported scene",
        "date": date,
        "location": location,
        "pcless": meta.get("pcless") == "true",
        "messages": messages,
        "cast": cast,
        "unmatched": unmatched,
        "warnings": warnings,
    }


def _discard(cid: str, sid: str) -> None:
    """Remove the scene a commit could not finish filling.

    Best effort, deliberately: the caller is already raising the failure worth
    reading, and a delete that fails on top of it would replace that error with
    a less useful one. What it leaves behind then is an empty, listed scene the
    reviewer can delete -- not a fragment of a transcript.
    """
    with contextlib.suppress(OSError, scenes.SceneNotFound, locks.StoreBusy):
        scenes.delete_scene(cid, sid)


def commit(cid: str, sid: str, messages: list[dict], date: str = "", location: str = "",
           cast: Sequence[dict] = ()) -> dict:
    """Write a reviewed draft into the freshly created, still-empty scene `sid`.

    **All or nothing, as far as a delete can make it**: either the scene ends
    up holding the whole draft, or it is removed -- and where the removal itself
    fails (see `_discard`) what is left is an empty scene rather than a
    fragment. A scene holding half an imported transcript reads exactly like a
    scene, and nothing downstream -- not the reviewer, not absorb -- can tell
    which half is missing. The caller creates the scene (only it can take the
    repad guard, which needs the app's run registry) and this removes it again
    on any failure, because only this can: `set_datetime` RENAMES the scene on
    the first date it sets, so after that step the id the caller holds is not
    the id the scene has.

    Ordered so the transcript comes out exactly as it went in. The moment and
    the place go first: both `set_datetime` and `set_location` append a
    transition line when they CHANGE a setting the scene already had, and are
    silent only on the first one. The cast goes last and never narrates, for the
    same reason -- `appear` appends "*X joins the scene.*" to a transcript that
    already has messages, a line the imported log never contained -- and after
    the rename, so the appearance record is written against the id the scene
    keeps rather than repointed onto it.

    Returns the scene's id, which is not necessarily the one that came in.
    """
    try:
        if date:
            sid = scenes.set_datetime(cid, sid, date)["id"]  # raises calendars.CalendarError
        if location:
            scenes.set_location(cid, sid, location)          # raises entities.EntityNotFound
        for m in messages:
            scenes.append_message(cid, sid, m.get("role") or "assistant", m.get("content", ""),
                                  speaker=m.get("speaker") or None)
        for a in cast:
            appearances.appear(cid, sid, a["kind"], a["id"], a["version"], a["role"],
                               narrate=False)                # raises appearances.AppearError
    except BaseException:
        # Including the ones this module cannot name -- an OSError mid-append, a
        # store gone busy, a cancellation. Every one of them leaves the same
        # half-scene, and the id to remove is the one tracked above.
        _discard(cid, sid)
        raise
    return {"id": sid, "messages": len(messages), "cast": len(cast)}
