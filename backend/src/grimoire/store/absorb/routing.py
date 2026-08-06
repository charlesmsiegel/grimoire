"""Which staged proposals a reviewer should look at first, and which the panel
should stop pre-approving.

Two signals, deliberately computed apart and multiplied only at the end:

- **certainty** (#110) -- what the extractor says about its own proposal. The
  model self-reports it per edit, it is poorly calibrated by construction, and
  this module treats it as an ORDERING signal, never as a probability.
- **authority** (#112) -- who in the transcript the proposal rests on. The model
  cites a speaker; this module checks that citation against the scene's own
  transcript labels and against the record the edit changes, so the tier is
  something the store proved rather than something the model claimed. A model
  that lies about its certainty moves one factor; a model that lies about its
  source moves neither, because the citation is checked.

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

from ..appearances import cast as appearances_cast
from ..scenes import read as scenes_read, serialize as scenes_serialize

#: The narrator says so. Un-labelled prose on either side of the table: the
#: model's narration ("Grimoire") and the player's own un-labelled posts
#: ("You"), which are that player narrating rather than a character speaking.
#: Scene transitions land here too -- they are stored under an internal speaker
#: the transcript never shows, and read back as the narration they render as.
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


def speaker_index(cid: str, sid: str) -> dict:
    """What this scene can say about a cited speaker, gathered once per absorb.

    ``labels`` are the speaker labels the transcript actually shows, base forms
    included, and they are the ground truth for whether a citation is
    corroborated at all -- not the cast list, which holds actors who may have
    been present without ever speaking. ``refs`` maps a cast display name to its
    ``"<kind>:<id>"`` token, which is the only thing that can answer "is this
    speaker the subject of the record being changed".

    An unreadable scene yields an empty index rather than raising: this runs
    after the extraction call has been paid for, and the failure a reviewer can
    act on is a proposal shown as uncorroborated, not a 500.
    """
    try:
        messages = scenes_read.read_scene(cid, sid)["messages"]
    except Exception:                                          # noqa: BLE001
        messages = []
    labels: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        label = _label(m)
        base = scenes_serialize.speaker_base(label)
        labels.extend(x for x in (label, base) if x and x not in labels)
    try:
        members = appearances_cast.scene_cast(cid, sid)
    except Exception:                                          # noqa: BLE001
        members = []
    # A display name two present actors share names NEITHER of them: keyed by
    # name, the second would overwrite the first and a citation would silently
    # resolve to whichever the cast happened to list last -- which is how a
    # speaker gets read as the subject of a record they are not. `match_name`
    # already declines an ambiguous label; dropping the name here is the same
    # answer one step earlier, where the collision is visible.
    refs: dict[str, str] = {}
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
        refs[name] = ref if refs.get(name, ref) == ref else ""
    return {"labels": labels, "refs": {n: r for n, r in refs.items() if r}}


def authority(index: dict, speaker: str, subjects: tuple[str, ...] = ()) -> str:
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
    labels = index.get("labels", [])
    refs = index.get("refs", {})
    matched = scenes_serialize.match_name(label, labels)
    if matched is None:
        return NARRATION if label.casefold() in NARRATION_WORDS else UNATTRIBUTED
    if matched in scenes_serialize.RESERVED_LABELS:
        return NARRATION
    # The MATCHED label, not the citation, and its base as a fallback: a line
    # stored as "Mara (aside)" renders under that label, so a model quoting it
    # verbatim matches a label the cast list does not hold under that spelling.
    for probe in (matched, scenes_serialize.speaker_base(matched)):
        name = scenes_serialize.match_name(probe, list(refs))
        if name:
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
    tier = authority(index, speaker, subjects)
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
