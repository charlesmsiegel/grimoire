"""Which staged proposals a reviewer should look at first, and which the panel
should stop pre-approving.

Two signals, deliberately computed apart and multiplied only at the end:

- **certainty** (#110) -- what the extractor says about its own proposal. The
  model self-reports it per edit, it is poorly calibrated by construction, and
  this module treats it as an ORDERING signal, never as a probability.
- **authority** (#112) -- who in the transcript the proposal rests on. The model
  cites a speaker and a short excerpt; this module checks BOTH against the
  scene's own transcript -- that somebody spoke under that label, and that the
  words cited are in what they said -- and then against the record the edit
  changes, so the tier is something the store proved rather than something the
  model claimed. A model that lies about its certainty moves one factor; a
  model that lies about its source moves neither, because both halves of the
  citation are checked. The name alone was not enough: every scene has a
  `Grimoire`, and the present cast's names are in the prompt's own context
  block, so a name-only check granted the top tiers to anything that copied
  one.

  The check is deliberately insensitive to case, whitespace and quote-mark
  shape (see `_normalized`). The cost of a FALSE fabrication report is a row
  the reviewer needed collapsed out of sight, which is worse than the miss it
  would be guarding against.

`score = certainty * WEIGHTS[authority]`, and `band(score)` is the whole output:
the review panel pre-checks `high` and `medium` exactly as it always did, and
routes `low` rows to a collapsed section that starts UNCHECKED. That direction
is the point. #110 records review-everything as a deliberate invariant of the
continuity pipeline, so this ships the half of its Option A that only ever
withholds a default approval -- a reviewer still has to tick a low row and press
Save for anything to be written, exactly as before.

**Nothing here is permission.** `apply.apply_edits` never reads a review block,
and must not start: the citation is display, and the band is a default checkbox
state. #112 says the same thing about evidence quotes in so many words. The one
thing a reviewer can never recover from is a wrong write they were nudged into
not reading, so the nudge only ever runs toward reading more.
"""

from __future__ import annotations

from ..appearances import cast as appearances_cast, paths as appearances_paths
from ..scenes import read as scenes_read, serialize as scenes_serialize

#: The narrator says so. Un-labelled prose on either side of the table: the
#: model's narration ("Grimoire") and the player's own un-labelled posts
#: ("You"), which are that player narrating rather than a character speaking.
#: A scene transition contributes to this tier the same way: it is stored under
#: an internal speaker the transcript never shows and renders as unlabelled
#: narration, so `_label` reads it back as the narration it looks like. Citing
#: the internal marker ITSELF is a different thing and is not narration -- the
#: model never saw that string, so a row claiming it did not come from there.
NARRATION = "narration"
#: The record's own subject said it, about themself. First-hand, and the only
#: tier a character's own dialogue can reach.
SELF = "self"
#: Somebody else in the scene said it. A character's claim about a third party,
#: about the world, or about a record they are not the subject of -- the case
#: #112 exists to stop being weighed like narration.
OTHER = "other"
#: The model named a speaker this scene's transcript cannot pin down: nobody
#: spoke under that name, or -- rarer, and the reason this tier is not called
#: "invented" -- two speakers answer to it equally and `match_name` declines to
#: guess. Either way the citation cannot be checked, which is the one thing
#: worth ranking below hearsay: a claim whose source is merely weak can still be
#: weighed, and one whose source cannot be found cannot be.
UNATTRIBUTED = "unattributed"
#: The model cited nobody. The prompt asks for a citation, so this is a row that
#: did not answer -- scored below a corroborated first-hand claim and above a
#: fabricated one, because an absent citation says nothing either way.
UNCITED = "uncited"

#: Words a model reaches for when it means "the narration" rather than a
#: speaker, folded. The two the transcript actually writes are in here by
#: derivation; the rest are there because the prompt asking for "Grimoire" does
#: not stop a model writing "narrator", and reading that as a citation nobody in
#: the scene answers to would collapse EVERY narrated row in the review --
#: turning the one tier that should sail through into the one that never does.
#:
#: Consulted only after the transcript's own labels have declined the name, so a
#: character who really is called Narrator is matched as herself first: she
#: spoke under that label, and a label that matched is never re-read as a word.
NARRATION_WORDS: frozenset[str] = frozenset(
    {label.casefold() for label in scenes_serialize.RESERVED_LABELS}
    | {"narrator", "narration", "the narrator", "narrative", "gm",
       "game master", "storyteller", "dm"})

#: What each tier does to the model's own number. Chosen for the ORDER they
#: impose and for two properties that hold at every certainty:
#:
#: - `UNATTRIBUTED * 1.0` is below `LOW`, so an uncheckable citation is always
#:   routed to the collapsed section however sure the model claims to be. A
#:   confident fabrication is the failure mode this whole path exists to catch,
#:   and letting certainty buy its way out would hand it the opposite.
#: - `UNCITED * 1.0` is below `HIGH`, so a row that skipped the citation can
#:   never be ranked as a strong one. It still pre-checks (it lands `medium`),
#:   which is what keeps rows that legitimately have no speaker -- weather,
#:   a new lore entry -- behaving as they did before this landed.
WEIGHTS: dict[str, float] = {
    NARRATION: 1.0,
    SELF: 0.8,
    UNCITED: 0.6,
    OTHER: 0.5,
    UNATTRIBUTED: 0.3,
}

#: Stand-in for a certainty the model did not give. Deliberately a value and not
#: a `None` branch: `parse` keeps "said nothing" distinct from "said 0.7" so the
#: panel can show which happened, but the BAND still has to be decided, and the
#: honest reading of silence is "no reason to think this is either strong or
#: weak". At 0.7 an uncited, uncertain-by-default row lands `medium` -- today's
#: behaviour for today's rows -- while an unattributed one still lands `low`.
ASSUMED_CERTAINTY = 0.7

#: Band edges. `HIGH` sits above `ASSUMED_CERTAINTY * WEIGHTS[UNCITED]` and
#: above `WEIGHTS[OTHER]`, so neither silence nor hearsay alone can reach it;
#: `LOW` sits at `ASSUMED_CERTAINTY * WEIGHTS[OTHER]`, so ordinary hearsay is
#: not collapsed merely for being hearsay -- it takes hearsay the model is also
#: unsure of, or a citation that does not check out.
HIGH = 0.65
LOW = 0.35


def band(score: float) -> str:
    """Which of the three review tiers a score falls in.

    Half-open from below (`score < LOW` is low): an edge value belongs to the
    more visible band, which is the direction every rounding decision in this
    module takes.
    """
    if score >= HIGH:
        return "high"
    return "low" if score < LOW else "medium"


def _label(message: dict) -> str:
    """The transcript label one stored message is rendered under.

    Composed from the same two pieces `snippets/transcript.j2` and
    `chronicle.transcript_text` use, so the labels checked here are the labels
    the model was actually shown -- including the transition normalisation,
    without which an internal `⁣Scene` marker would read as a speaker the model
    could have cited but never saw.
    """
    speaker = message.get("speaker")
    if not isinstance(speaker, str) or speaker == scenes_serialize.TRANSITION_SPEAKER:
        speaker = None
    role = message.get("role", "assistant")
    if scenes_serialize.label_preserved(speaker):
        return speaker
    return str(scenes_serialize.ROLE_TO_LABEL.get(role, role))


#: `ROLL_SPEAKER` without its U+2063 sentinel -- the spelling a reader (and so
#: the extractor) actually sees above a manual dice-roll line. Derived rather
#: than written out, so the two cannot drift apart.
ROLL_LABEL = scenes_serialize.ROLL_SPEAKER.lstrip("⁣")

#: Typographic quote marks folded to their ASCII forms before matching. A store
#: that curled an apostrophe, or a model that straightened one, is not a
#: different quote, and treating it as one would collapse an honest row.
_SMART_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"',
                               "‘": "'", "’": "'", "‚": "'",
                               "«": '"', "»": '"'})


def _normalized(text) -> str:
    """A quote or a message body reduced to what a match should turn on.

    Case, run-length of whitespace and the shape of the quote marks are all
    things that differ between what a model writes and what the transcript
    holds without either being wrong -- a re-wrapped line, a tidied apostrophe.
    Matching raw would report those as fabrications, which is the one failure
    this check must not introduce: a false `unattributed` collapses a row the
    reviewer needed to see.
    """
    if not isinstance(text, str):
        return ""
    return " ".join(text.translate(_SMART_QUOTES).casefold().split())


def _canonical(label: str) -> str:
    """The single spelling this index files a speaker under.

    Two labels collapse here. A sub-speaker form drops its parenthetical, so
    "Mara (aside)" and "Mara" are ONE speaker rather than two candidates -- with
    both listed, `match_name`'s prefix rule counted them separately and declined
    a citation of "Mara" as ambiguous between a speaker and herself, sending the
    scene's most quotable lines to the collapsed section. And `ROLL_SPEAKER`
    becomes the visible `Roll`: its sentinel renders as nothing, so the
    extractor can only ever cite the visible form, and indexing the raw string
    made a verbatim roll citation unmatchable.
    """
    if label == scenes_serialize.ROLL_SPEAKER:
        return ROLL_LABEL
    return scenes_serialize.speaker_base(label)


def _refs(cid: str, sid: str, aliases: dict[str, str]) -> dict[str, str]:
    """Display name -> ``"<kind>:<id>"`` for everyone who could have spoken here.

    The scene's current cast, PLUS any roster actor whose name the transcript
    shows as a speaker label. `leave()` removes the scene id from an actor's
    appearance record while the lines they already spoke stay in the transcript,
    so the cast alone reads a departed character's own first-hand claim as
    hearsay about themself -- and, where a same-named actor is still present,
    hands their line to that actor instead.

    Widened by the transcript rather than to the whole roster on purpose: every
    extra name is another candidate `match_name` can trip over, so a campaign's
    unrelated cast would cost `SELF` resolutions in scenes none of them are in.
    Only actors this transcript actually names can be the ones it quoted.

    A name two actors share names NEITHER of them: keyed by name, the second
    would overwrite the first and a citation would resolve to whichever was
    listed last, which is how a speaker gets read as the subject of a record
    they are not. Dropping it is `match_name`'s own answer, one step earlier
    where the collision is visible.
    """
    try:
        members = list(appearances_cast.scene_cast(cid, sid))
    except Exception:                                          # noqa: BLE001
        members = []
    seen = {(a.get("kind"), a.get("id")) for a in members}
    try:
        aroot = appearances_paths.locked_actor_root(cid)
        for a in appearances_cast.roster(cid):
            if (a["kind"], a["id"]) in seen:
                continue
            name = appearances_cast._actor_name(aroot, a["kind"], a["id"], a["version"])
            if isinstance(name, str) and name.casefold() in aliases:
                members.append({**a, "name": name})
                seen.add((a["kind"], a["id"]))
    except Exception:                                          # noqa: BLE001
        pass                          # an unreadable roster widens nothing
    refs: dict[str, str] = {}
    ambiguous: set[str] = set()
    for a in members:
        name = a.get("name")
        # A STRING, checked rather than assumed: cards are hand-editable and
        # `scene_cast` hands back whatever `name:` holds, so a mapping-valued
        # name reaches here unhashable and raises out of `materialize` -- after
        # the extraction call was paid for, turning one malformed card into a
        # 500 for the whole absorb. Everything downstream of here treats a name
        # as text (`match_name` lowercases it), so anything else names nobody.
        if not isinstance(name, str) or not name.strip():
            continue
        ref = f"{a.get('kind')}:{a.get('id')}"
        if name in refs and refs[name] != ref:
            ambiguous.add(name)
        refs[name] = ref
    for name in ambiguous:
        del refs[name]
    return refs


def speaker_index(cid: str, sid: str) -> dict:
    """What this scene can say about a cited speaker, gathered once per absorb.

    - ``canonical`` -- one entry per speaker the transcript shows, and the only
      list `match_name` is ever run against, so a speaker cannot be ambiguous
      with a second spelling of themself.
    - ``aliases`` -- every folded spelling (full label and canonical) mapped to
      its canonical, so a verbatim citation of "Mara (aside)" resolves without
      the prefix rule being consulted at all.
    - ``synthetic`` -- canonical labels that stand for no actor. A roll line is
      real transcript content and can be cited, but nobody said it.
    - ``refs`` -- see `_refs`.

    The transcript is the ground truth for whether a citation is corroborated,
    not the cast list: an actor can be present the whole scene without speaking
    a line worth quoting.

    An unreadable scene yields an empty index rather than raising: this runs
    after the extraction call has been paid for, and the failure a reviewer can
    act on is a proposal shown as uncorroborated, not a 500.
    """
    try:
        messages = scenes_read.read_scene(cid, sid)["messages"]
    except Exception:                                          # noqa: BLE001
        messages = []
    # A list, not a set: `match_name`'s prefix rule counts how many names a
    # label could mean by iterating. Deduped as it is built, so a speaker with
    # fifty lines does not look like fifty candidates.
    canonical: list[str] = []
    aliases: dict[str, str] = {}
    said: dict[str, list[str]] = {}
    roll_said: dict[str, list[str]] = {}
    for m in messages:
        if not isinstance(m, dict):
            continue
        label = _label(m)
        if not label:
            continue
        canon = _canonical(label)
        if canon not in canonical:
            canonical.append(canon)
        for spelling in (label, canon):
            aliases.setdefault(spelling.casefold(), canon)
        # Kept APART rather than flagging the canonical, because the visible
        # `Roll` is also a writable character name. Marking the shared label
        # synthetic took the real actor's identity with it -- she could never be
        # first-hand about herself in any scene that also held a dice line. What
        # is synthetic is the LINE, so that is what is recorded.
        bucket = roll_said if label == scenes_serialize.ROLL_SPEAKER else said
        bucket.setdefault(canon, []).append(_normalized(m.get("content")))
    # Joined with a separator no quote can straddle, so two adjacent messages
    # cannot be spliced into a sentence neither of them contains.
    return {"canonical": canonical, "aliases": aliases,
            "texts": {c: "\n".join(p) for c, p in said.items()},
            "roll_texts": {c: "\n".join(p) for c, p in roll_said.items()},
            "refs": _refs(cid, sid, aliases)}


def _excerpt(quote: str) -> str:
    """The cited words, ready to match. Empty when nothing usable was cited.

    Surrounding quote marks come off because a model wrapping its excerpt in
    them is quoting, not misquoting.
    """
    return _normalized(quote).strip("\"'")


def _found(index: dict, key: str, canons, excerpt: str) -> bool:
    """Does the excerpt appear in one of these labels' lines, real or rolled?"""
    texts = index.get(key, {})
    return any(excerpt in texts.get(c, "") for c in canons)


def _narration_canons(index: dict) -> list[str]:
    """The canonical labels that stand for the narration in this scene -- the
    reserved role labels, and only the ones the transcript actually used."""
    return [c for c in index.get("canonical", []) if c in scenes_serialize.RESERVED_LABELS]


def authority(index: dict, speaker: str, subjects: tuple[str, ...] = (),
              quote: str = "") -> str:
    """The tier a cited speaker earns for an edit about `subjects`.

    `subjects` are the ``"<kind>:<id>"`` actors the record BELONGS to -- the
    character whose state is being rewritten, both ends of a bond, the `from`
    side of a feeling. A record with no personal subject (a lore entry, a plot
    thread, the weather) passes none, so no speaker can reach `SELF` for it:
    a character asserting something about the world is a third-party claim,
    which is exactly what #112 asks for.

    Resolution runs transcript-FIRST, and the order is the point: a name is
    read as a speaker if this scene has one, and only otherwise as a word for
    the narration. `match_name` gives the same exact-then-unambiguous-prefix
    rule the transcript parser uses, so a model citing "Mara" for a line
    labelled "Mara Cotgrave" still checks out, and one citing a name two
    speakers answer to is declined rather than guessed at.
    """
    label = (speaker or "").strip()
    if not label:
        return UNCITED
    # Half a citation is not a citation. A speaker with no excerpt used to earn
    # the full tier on the name alone -- and the name is the cheap half, so
    # `{"speaker": "Grimoire"}` banded high on a row nothing corroborated.
    # `UNCITED` is the honest answer: it is what a row that answered nothing
    # gets, it can never band high, and it still pre-checks, so the rows that
    # legitimately have no speaker are unaffected.
    excerpt = _excerpt(quote)
    if not excerpt:
        return UNCITED
    refs = index.get("refs", {})
    # Verbatim first, prefix second. An exact spelling the transcript holds is
    # not a guess and must not be put to `match_name`, whose prefix rule would
    # decline it the moment a longer label also starts with it.
    matched = (index.get("aliases", {}).get(label.casefold())
               or scenes_serialize.match_name(label, index.get("canonical", [])))
    if matched is None:
        if label.casefold() not in NARRATION_WORDS:
            return UNATTRIBUTED
        # A word for the narration rather than a label: check the excerpt
        # against every narrated line, since that is what the model meant by it.
        return (NARRATION if _found(index, "texts", _narration_canons(index), excerpt)
                else UNATTRIBUTED)
    # A dice line first, and under the SAME visible label as any actor sharing
    # the name: real transcript content, quotable, and spoken by nobody. So it
    # is corroborated rather than a fabrication, and can never be first-hand --
    # while the actor's own lines below still resolve to her.
    if _found(index, "roll_texts", (matched,), excerpt):
        return OTHER
    # The NAME is the cheapest part of a citation to get right -- every scene
    # has a `Grimoire`, and a present character's name is in the prompt's own
    # context block -- so a tier granted on the name alone is a tier a
    # confabulating model can claim by copying. The words have to be there too.
    if not _found(index, "texts", (matched,), excerpt):
        return UNATTRIBUTED
    if matched in scenes_serialize.RESERVED_LABELS:
        return NARRATION
    names = list(refs)
    name = scenes_serialize.match_name(matched, names)
    # `confusable` asks the second question `match_name` cannot: not "where does
    # this label land" but "who else could have written it". With both "Mara"
    # and "Mara Cotgrave" cast, a line labelled "Mara" resolves to "Mara"
    # cleanly and possibly wrongly, since the model may have been abbreviating
    # the longer name -- and `SELF` is the tier that must not be handed out on a
    # maybe. The same helper the voice judge uses, for the same reason.
    if name and not scenes_serialize.confusable(name, names):
        return SELF if refs[name] in subjects else OTHER
    return OTHER


def review(index: dict, row: dict, subjects: tuple[str, ...] = ()) -> dict:
    """One staged edit's review block: what the model claimed, what the
    transcript corroborates, and the band the panel acts on.

    `certainty` is reported as the model gave it -- `None` when it gave none --
    so a reviewer reading a `low` row can tell an unsure model from an
    unverifiable source. `score` uses `ASSUMED_CERTAINTY` in that case, because
    a band still has to be decided; the two fields disagreeing is the point.
    """
    speaker = str(row.get("speaker") or "").strip()
    quote = str(row.get("quote") or "").strip()
    # Re-clamped rather than trusted. `parse._certainty` already did this, but a
    # score outside 0-1 would silently move both band edges for one row, and the
    # cost of proving it here instead of assuming it is one comparison.
    raw = row.get("certainty")
    certainty = (max(0.0, min(1.0, float(raw)))
                 if isinstance(raw, (int, float)) and not isinstance(raw, bool)
                 and raw == raw else None)                # NaN != NaN
    tier = authority(index, speaker, subjects, quote)
    # Rounded BEFORE banding, not after. A band read off the raw product and a
    # score reported to four places disagree at the edge -- 0.649999 bands
    # medium and prints 0.65 -- which is a reader's afternoon gone. It also
    # settles the boundary the weights are tuned to hit exactly:
    # `ASSUMED_CERTAINTY * WEIGHTS[OTHER]` is 0.35 in arithmetic and a hair
    # under it in binary floating point, so rounding is what makes the band
    # follow the table rather than the representation.
    score = round((ASSUMED_CERTAINTY if certainty is None else float(certainty))
                  * WEIGHTS[tier], 4)
    return {"certainty": certainty, "quote": quote, "speaker": speaker,
            "authority": tier, "score": score, "band": band(score)}
