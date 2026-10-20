"""Closed decision vocabulary, decision-item transport and strict evidence/direction rebuilding into proposals."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from .... import decisions, prompts
from .. import effective, pending

#: A proposal's reason, clipped -- the bound identity's decision uses
#: (`identity.REASON_CHARS`), one short sentence.
RECONCILE_REASON_CHARS = 280


# -------------------------------------------------------------- adjudication

#: What the model may answer, per vocabulary (spec §11.3). §5.3 allows
#: ``continues`` and ``subthread_of`` between threads only, so the same-type
#: list is read per type (deviation 18), and no vocabulary but a same-type one
#: offers ``duplicate`` (§3.6).
DECISIONS: dict[str, tuple[str, ...]] = {
    "same_thread": ("duplicate", "continuation", "subthread", "related", "distinct",
                    "uncertain"),
    "same_commitment": ("duplicate", "related", "distinct", "uncertain"),
    "cross": ("pays_off", "related", "distinct", "uncertain"),
    "thread": ("close", "keep_open", "uncertain"),
    "commitment": ("fulfilled", "broken", "expired", "keep_open", "uncertain"),
    "temporal": ("before", "on", "after", "by", "unrelated", "uncertain"),
}

#: What each vocabulary asks, as a candidate's heading in the user prompt.
LABELS: dict[str, str] = {
    "same_thread": "two plot threads",
    "same_commitment": "two commitments",
    "cross": "a plot thread and a commitment",
    "thread": "whether a plot thread is finished",
    "commitment": "whether a commitment is resolved",
    "temporal": "a commitment and a dated event",
}

#: A pair word -> the link relation it proposes (§5.3); ``duplicate`` proposes
#: an alias, which is not a link.
_RELATION_OF = {"duplicate": "", "continuation": "continues", "subthread": "subthread_of",
                "related": "related_to", "pays_off": "pays_off"}
#: The words whose meaning depends on which record is which (§11.3).
_DIRECTED = frozenset({"duplicate", "continuation", "subthread", "pays_off"})
#: The lifecycle words that propose a status, and so need positive evidence
#: (§11.4); ``keep_open`` writes no status.
_STATUS_OF = {"close": "closed", "fulfilled": "fulfilled", "broken": "broken",
              "expired": "expired"}


def vocabulary(record: dict) -> str:
    """The `DECISIONS` key a cached record is asked under: its lifecycle type,
    ``temporal`` for a commitment beside an event, the refs' type for a
    duplicate, else ``cross``."""
    kind = record.get("kind")
    if kind == "possible_thread_closure":
        return "thread"
    if kind == "possible_commitment_resolution":
        return "commitment"
    if pending.is_temporal(record):
        return "temporal"
    if kind == "possible_duplicate":
        refs = record.get("refs") or [""]
        first = refs[0] if isinstance(refs[0], str) else ""
        return "same_commitment" if first.startswith("commitment:") else "same_thread"
    return "cross"


def _ref_of(value, refs: dict[str, str]) -> str:
    return refs.get(value.strip().upper(), "") if isinstance(value, str) else ""


def _evidence(value, known: set[str]) -> list[str]:
    """The cited scene ids the prompt showed, deduped in order; the rest dropped."""
    out: list[str] = []
    for sid in value if isinstance(value, list) else ():
        if isinstance(sid, str) and sid in known and sid not in out:
            out.append(sid)
    return out


def _allowed(relation: str, frm: str, to: str) -> bool:
    rule = effective.RELATIONS.get(relation)
    return rule is not None and frm.partition(":")[0] in rule[0] \
        and to.partition(":")[0] in rule[1]


def _temporal_word(word: str, refs: dict[str, str], base: dict) -> dict:
    """A temporal word runs from the commitment to the event, whatever letters
    the reply gave; ``unrelated`` and ``uncertain`` propose nothing."""
    event = next((r for r in refs.values() if r.startswith("event:")), "")
    owed = next((r for r in refs.values() if r.startswith("commitment:")), "")
    if word not in pending.TEMPORAL_RELATIONS:
        return {**base, "decision": word}
    if not event or not owed or not _allowed(word, owed, event):
        return base
    return {**base, "decision": word, "from": owed, "to": event, "relation": word}


def _runs(word: str, frm: str, to: str) -> bool:
    """Whether pair word `word` may run from ref `frm` to ref `to`: two
    different records, and a relation `effective.RELATIONS` allows for
    ``(from, to)`` -- or, for ``duplicate``, which proposes an alias rather
    than a link, two records of one type. The one rule `_pair` reads a reply
    by and `build_items` offers a folded direction by, so no option is offered
    that `_pair` would turn into ``uncertain``."""
    if not frm or not to or frm == to:
        return False
    relation = _RELATION_OF[word]
    if relation:
        return _allowed(relation, frm, to)
    return frm.partition(":")[0] == to.partition(":")[0]


def _pair(word: str, item: dict, refs: dict[str, str], base: dict) -> dict:
    """A pair word with its direction (§11.3). A directed word needs two
    different letters; ``related`` takes A to B when it was given none. A
    relation `effective.RELATIONS` refuses for ``(from, to)`` is ``uncertain``,
    so no proposal names a link `create_link` would reject."""
    if word not in _RELATION_OF:
        return {**base, "decision": word}
    frm, to = _ref_of(item.get("from"), refs), _ref_of(item.get("to"), refs)
    if word not in _DIRECTED and (not frm or not to or frm == to):
        frm, to = refs.get("A", ""), refs.get("B", "")
    if not _runs(word, frm, to):
        return base
    return {**base, "decision": word, "from": frm, "to": to, "relation": _RELATION_OF[word]}


def _lifecycle_word(word: str, base: dict) -> dict:
    """A status word stands only with a known evidence scene (§11.4), and on
    that scene alone (§7.4, I4): its rationale may be empty, and on a native
    backend there is none."""
    if word in _STATUS_OF:
        if not base["evidence_scenes"]:
            return base
        return {**base, "decision": word, "status": _STATUS_OF[word]}
    return {**base, "decision": word}


def _decide(item: dict, cand: dict, known: set[str]) -> dict:
    """One reply element rebuilt field by field into a cache proposal (§6)."""
    vocab = cand["vocabulary"]
    word = item.get("decision")
    word = word.strip().casefold() if isinstance(word, str) else ""
    word = word if word in DECISIONS[vocab] else "uncertain"
    reason = item.get("reason")
    base = {"decision": "uncertain", "from": "", "to": "", "relation": "", "status": "",
            "reason": reason.strip()[:RECONCILE_REASON_CHARS] if isinstance(reason, str) else "",
            "evidence_scenes": _evidence(item.get("evidence_scenes"), known)}
    refs = {r["letter"]: r["ref"] for r in cand["records"]}
    if vocab in ("thread", "commitment"):
        return _lifecycle_word(word, base)
    if vocab == "temporal":
        return _temporal_word(word, refs, base)
    return _pair(word, item, refs, base)


def rebuild(item: dict, cand: dict, known: set[str]) -> dict:
    """One answer rebuilt field by field into a cache proposal, by the sweep's
    own rule (§6, `_decide`): a word outside the candidate's vocabulary is
    `uncertain`, the letters resolve to its records, evidence outside `known`
    is dropped and a lifecycle word with none left is `uncertain`. `cand` is
    one of `build_payload`'s candidates. Roadmap 12's continuity
    investigation validates its `propose` through this, with `known` the
    scenes it read."""
    return _decide(item, cand, known)


# ------------------------------------------------------- as decision items
#
# The adjudication as `decide()` items (spec §7.4): one per candidate, its
# context self-contained, asking the decision (a pair's direction folded into
# it) and the evidence scenes it shows. These are what the sweep sends and reads
# (`routes.continuity._adjudicate`).

#: The id of each item's first question, a choice over its vocabulary's words.
DECISION_ID = "decision"

#: Evidence scenes one item may cite (I2). Today stores a list and the detail
#: view shows every cited scene as a chip, so one is too few; three nullable
#: choices over one item's shown scenes keep a call's enum values at most
#: 3 x options x `decisions.MAX_ITEMS_PER_CALL`, well inside
#: `decisions.MAX_ENUM_VALUES` (and `decisions.chunks` closes a call early if
#: not). Argued from that structure alone; to be tuned against real prompts
#: later.
EVIDENCE_SCENES = 3

#: The evidence questions' ids, slot by slot.
EVIDENCE_IDS = ("evidence_scene", "evidence_scene_2", "evidence_scene_3")

#: The vocabularies whose words need a direction (`_DIRECTED`): the pairs.
PAIR_VOCABULARIES = ("same_thread", "same_commitment", "cross")

#: How a folded option joins the two letters of a directed word (§7.4), so
#: the option reads as the proposal it makes: ``duplicate_a_into_b`` folds A
#: into B, ``continuation_b_of_a`` and ``subthread_b_of_a`` make B a
#: continuation or a subthread of A, and ``pays_off_b_to_a`` runs the
#: plot thread B to the commitment A it pays off.
_JOINS = {"duplicate": "into", "continuation": "of", "subthread": "of", "pays_off": "to"}

#: The two ways a pair may run, as ``(from, to)`` letters.
_WAYS = (("A", "B"), ("B", "A"))


def folded(word: str, frm: str, to: str) -> str:
    """The `decision` option that carries directed word `word` running from
    letter `frm` to letter `to` (§7.4): ``<word>_<from>_<join>_<to>``, the
    letters in lowercase. A native decisions endpoint answers each question
    of an item on its own, so a direction asked as a question of its own
    ("null when the decision has no direction") was answered without the
    decision it depends on, and every directed verdict was lost; one choice
    carrying both cannot be split that way."""
    return f"{word}_{frm.lower()}_{_JOINS[word]}_{to.lower()}"


#: Every folded option, back to its ``(word, from, to)``.
_UNFOLDED = {folded(word, frm, to): (word, frm, to) for word in _JOINS for frm, to in _WAYS}


def unfolded(answer: str) -> tuple[str, str, str]:
    """`(word, from, to)` of a `decision` answer: a folded option split back
    into its directed word and its two letters, and any other answer as
    itself with no letters -- the decision, `from` and `to` of today's reply
    element, which `_decide` reads."""
    return _UNFOLDED.get(answer, (answer, "", ""))


def _offered(vocab: str, records: list[dict]) -> list[tuple[str, str, str]]:
    """`(word, from, to)` of each `decision` option, in `DECISIONS` order: a
    word that needs no direction as itself with no letters, and a directed
    word on a pair once per way `_runs` allows it to run between the item's
    records -- so ``pays_off``, which runs from the plot thread to the
    commitment only, is offered once, and a word no way allows is not
    offered. Rendering nothing, so the reading side can ask it too."""
    refs = {r["letter"]: r["ref"] for r in records}
    out: list[tuple[str, str, str]] = []
    for word in DECISIONS[vocab]:
        if vocab not in PAIR_VOCABULARIES or word not in _DIRECTED:
            out.append((word, "", ""))
            continue
        out += [(word, frm, to) for frm, to in _WAYS
                if _runs(word, refs.get(frm, ""), refs.get(to, ""))]
    return out


def _option_id(word: str, frm: str, to: str) -> str:
    return folded(word, frm, to) if frm else word


def _decision_options(vocab: str, records: list[dict]) -> tuple[decisions.Option, ...]:
    """The `decision` choice's options (`_offered`): a word that needs no
    direction labelled by itself (ruling 6), and a directed word folded with
    each way it may run, described by `directed_option.j2`. A word offered
    one way only takes its bare spelling as an alias: today's reply naming it
    without letters can mean nothing else. Aliases are the structured
    parser's; a native endpoint is sent the option ids alone."""
    offered = _offered(vocab, records)
    ways = Counter(word for word, frm, _ in offered if frm)
    return tuple(
        decisions.Option(_option_id(word, frm, to),
                         prompts.render("continuity_reconcile/directed_option.j2",
                                        word=word, frm=frm, to=to),
                         (word,) if ways[word] == 1 else ())
        if frm else decisions.Option(word, word.replace("_", " "))
        for word, frm, to in offered)


def _chosen(answer: decisions.Answer, cand: dict) -> str:
    """The option `answer` stands for. As chosen, unless the distribution an
    endpoint reported puts more mass on another word than on the chosen
    option's, summed over that word's folded spellings
    (`decisions.regrouped`): an endpoint scoring options one by one splits a
    duplicate whose direction is a coin flip across its two spellings, and
    reads 0.3 + 0.3 as losing to 0.4. Then the winning word's best-scored
    spelling, a tie going to the one offered first. With no distribution (a
    structured reply), as chosen."""
    chosen = _answered(answer)
    offered = [_option_id(*way) for way in _offered(cand["vocabulary"], cand["records"])]
    word = decisions.regrouped(answer, lambda oid: unfolded(oid)[0],
                               DECISIONS[cand["vocabulary"]])
    if word is None:
        return chosen
    weights = answer.distribution or {}
    return max((oid for oid in offered if unfolded(oid)[0] == word),
               key=lambda oid: weights.get(oid, 0.0))


def item_scenes(payload: dict, cand: dict) -> list[str]:
    """The scene ids one candidate's item shows: its records' beat scenes, and
    the chronicle lines it shows -- those of its own beat scenes and of the
    recent window (`payload["recent"]`) -- in `payload["known_scenes"]`
    order. An id `decisions.offerable` refuses is dropped, and so is EVERY id
    of a group that collides once normalised (Review Focus 3, M4, M8) -- from
    the item's evidence options and its chronicle lines; a beat keeps its
    marker, as the record block is today's byte for byte -- so a hand-edited
    ledger or chronicle never builds a request `decisions.validate` refuses.
    Keeping one of a colliding group would be worse than keeping none: the
    others' ids still show in the beat markers, and a citation of one of them
    normalises onto the one kept, storing a scene the model did not cite.
    Dropped, every id of the group is a scene the item did not show, so a
    status word standing on one alone is ``uncertain`` by `_decide`'s rule.
    With no id dropped, the union over a payload's candidates is today's
    `known_scenes`: each item may cite only what it shows."""
    beats = {b["scene"] for r in cand["records"] for b in r["beats"]} - {""}
    recent = set(payload["recent"])
    lines = {line["id"] for line in payload["chronicle"]
             if line["id"] in beats or line["id"] in recent}
    shown = beats | lines
    offered = [sid for sid in payload["known_scenes"]
               if sid in shown and decisions.offerable(sid)]
    keys = Counter(decisions.normalise(sid) for sid in offered)
    return [sid for sid in offered if keys[decisions.normalise(sid)] == 1]


def _decision_item(payload: dict, cand: dict) -> decisions.Item:
    vocab = cand["vocabulary"]
    shown = item_scenes(payload, cand)
    kept = set(shown)
    context = prompts.render(
        "continuity_reconcile/item.j2", now=payload["now"],
        chronicle=[line for line in payload["chronicle"] if line["id"] in kept],
        c={"label": LABELS[vocab], "records": cand["records"],
           "signal_text": cand["signal_text"]})
    asked: list[decisions.Question] = [decisions.Choice(
        DECISION_ID, prompts.render("continuity_reconcile/question.j2", vocabulary=vocab),
        _decision_options(vocab, cand["records"]))]
    scenes = tuple(decisions.Option(sid, prompts.render("continuity_reconcile/scene_option.j2",
                                                        sid=sid))
                   for sid in shown)
    for slot in range(min(len(shown), EVIDENCE_SCENES)):
        template = "evidence.j2" if slot == 0 else "evidence_more.j2"
        asked.append(decisions.Choice(EVIDENCE_IDS[slot],
                                      prompts.render(f"continuity_reconcile/{template}"),
                                      scenes, allow_none=True))
    return decisions.Item(context, tuple(asked))


def build_items(payload: dict) -> tuple[decisions.Item, ...]:
    """One `decide()` item per payload candidate, in order.

    Each item's context is `continuity_reconcile/item.j2`: today's preamble
    (the campaign date, and the chronicle lines this item shows), a heading
    naming what it asks, and today's record and signal block byte for byte --
    so it stands alone, as a native backend sends each item by itself. It asks
    `decision`, a choice over its vocabulary's words, each labelled by its own
    word (the criteria stay whole in the question, ruling 6), a pair's
    directed words folded with their direction (`_decision_options`); and one
    nullable evidence choice per scene it shows (`item_scenes`), up to
    `EVIDENCE_SCENES`, each over every scene it shows. No question depends on
    another's answer, nor refers to one: a native endpoint answers each one
    alone (spec 7.4). So the evidence questions ask which shown scene, if any,
    shows a record settled, never "for" a status word (`evidence.j2`), and
    the rule that a status word stands only on a cited scene is applied to
    the answers by `_decide`, not stated in a question. Pure, but it renders:
    callers run it in the threadpool."""
    return tuple(_decision_item(payload, cand) for cand in payload["candidates"])


def explain() -> str:
    """The rationale instruction: each candidate's display-only reason."""
    return prompts.render("continuity_reconcile/explain.j2")


def _answered(answer: decisions.Answer | None) -> str:
    return answer.answer if answer is not None and isinstance(answer.answer, str) else ""


def proposals_of(payload: dict,
                 results: Sequence[decisions.ItemResult]) -> dict[str, dict] | None:
    """The parsed batch as today's proposals, ``{candidate id: proposal}``.

    A candidate counts as answered only when its `decision` answer `was_read`
    (I1). One that was not -- the reply held no object, or nothing in it reads
    as that item, or it errored, was refused or abstained -- gets no entry: no
    proposal, so persist 2 leaves its cached record's `proposal` None and the
    next sweep's `select` asks it again. It is never stored as ``uncertain``.

    Every read item is rebuilt as today's reply element -- the decision (``""``
    when unreadable; where a native endpoint reported a distribution, the word
    its mass favours once each word's folded spellings are summed, `_chosen`)
    and, for a folded option, the `from` and `to` letters it carries
    (`unfolded`), the rationale as `reason`
    (``""`` when none came back), and the answered evidence slots in order --
    and run through `_decide` with the scenes the item showed as the known
    ones, so a word outside the vocabulary, a direction the link rules refuse
    and a status word without a shown scene are ``uncertain`` by today's code.
    A status word with a shown scene stands without a rationale, storing
    ``reason: ""`` (I4): nothing is invented in its place.

    A garbled chunk is not a garbled item (N8): "never ``uncertain``" is about
    a candidate the reply never reached. An object that reaches an item and
    answers it badly -- ``{"answers": {}}``, a null decision, ``maybe`` -- was
    read, and becomes ``uncertain`` as today's unknown word does.

    None only when no item was read and every item held no object: today's
    undecodable reply, a failed run. An object holding no item (``{}``,
    today's format) is ``{}``: the sweep lands with nothing adjudicated
    (§24). A chunk error with nothing read is the call site's to report before
    it asks this (M12)."""
    out: dict[str, dict] = {}
    read = False
    for cand, result in zip(payload["candidates"], results, strict=True):
        decision = result.answers.get(DECISION_ID)
        if decision is None or not decisions.was_read(decision):
            continue
        read = True
        answers = result.answers
        word, frm, to = unfolded(_chosen(decision, cand))
        element = {"decision": word, "from": frm, "to": to, "reason": result.rationale,
                   "evidence_scenes": [_answered(answers.get(slot)) for slot in EVIDENCE_IDS
                                       if _answered(answers.get(slot))]}
        out[cand["id"]] = _decide(element, cand, set(item_scenes(payload, cand)))
    if not read and results and all(decisions.held_no_object(result.answers.get(DECISION_ID))
                                    for result in results):
        return None
    return out
