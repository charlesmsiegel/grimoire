"""The continuity decision items: the identity check run on absorb, and the
reconciliation sweep over the story ledger."""

from __future__ import annotations

from grimoire.store.continuity import identity, reconcile

from .harness import REPORT, _source, check, render, require_carried

# The continuity identity check (capstone spec §10.2). Rows are
# `Examination.prompt_rows()`-shaped; four inputs so every optional branch of
# `item.j2` is taken both ways: every optional field, none, a closed neighbour
# carrying earlier beats and every signal, and a row none of whose candidates
# can be offered (which asks `decision` alone).


def _identity_signals(**over):
    return {"title_equal": False, "slug_equal": False, "tokens": 0.0, "chars": 0.0,
            "cosine": None, "actors": [], "scenes": [], "anchors": [], "via": "lexical",
            **over}


def _identity_row(key, kind, title, candidates, **over):
    return {"key": key, "kind": kind, "title": title, "beat": "Winifred went looking.",
            "status": "", "commitment_kind": "", "due": "", "quote": "", "speaker": "",
            "certainty": None, "why_new": "", "distinguished_from": [],
            "candidates": candidates, **over}


def _identity_candidate(cid_, title, **over):
    return {"id": cid_, "title": title, "status": "open", "kind": "", "due": "",
            "latest_beat": "", "earlier": [], "signals": _identity_signals(), **over}


IDENTITY_INPUTS = {
    "thread+commitment": [
        _identity_row("r1", "thread", "Recover the harbour ledger", [
            _identity_candidate("find-the-ledger", "Find the ledger", status="advanced",
                                latest_beat="Winifred learned the harbour ledger exists.",
                                signals=_identity_signals(tokens=0.4, chars=0.52))],
            status="open", quote="I want that ledger.", speaker="Winifred", certainty=0.8,
            why_new="The search is for a different ledger.",
            distinguished_from=["find-the-ledger"]),
        _identity_row("r2", "commitment", "Seraphine's midnight deadline", [
            _identity_candidate("the-midnight-deadline", "The midnight deadline",
                                due="midnight",
                                latest_beat="Seraphine must pay by midnight.",
                                signals=_identity_signals(tokens=0.5, cosine=0.81,
                                                          actors=["characters:seraphine"]))],
            status="open", commitment_kind="threat", due="midnight",
            speaker="Seraphine", why_new="A second deadline.")],
    "bare": [
        _identity_row("r1", "commitment", "The midnight deadline", [
            _identity_candidate("the-midnight-deadline", "The midnight deadline",
                                signals=_identity_signals(title_equal=True,
                                                          slug_equal=True))])],
    "closed-neighbour": [
        _identity_row("r1", "thread", "Find the ledger", [
            _identity_candidate("find-the-ledger", "Find the ledger", status="closed",
                                latest_beat="Winifred burned the ledger.",
                                earlier=["Winifred found the first page.",
                                         "Winifred learned the harbour ledger exists."],
                                signals=_identity_signals(
                                    title_equal=True, slug_equal=True, tokens=0.6,
                                    chars=0.7, cosine=0.9, actors=["characters:winifred"],
                                    scenes=["saltmarch-docks"], anchors=["event:e1"]))],
            status="open", certainty=0.5)],
    # A hand-edited ledger's ``thread:`` key leaves an empty bare id, which the
    # decision contract cannot offer: the row is asked about, with no
    # ``existing`` to answer.
    "no-offerable": [
        _identity_row("r1", "thread", "Recover the harbour ledger", [
            _identity_candidate("", "Find the ledger")])],
}

def _identity_item(label, item, row) -> None:
    """One row's item: its context over the candidates it offers, its one
    question, and that question's options."""
    tag = f"{label}, {row['key']}"
    kept, _ = identity._offered(row, {})
    [shown] = identity.template_rows([{**row, "candidates": kept}])
    check(f"continuity identity item context ({tag})", item.context,
          render("continuity_identity/item.j2", r=shown))
    asked = [q.id for q in item.questions]
    REPORT.require(f"continuity identity item questions ({tag})",
                   asked == [identity.DECISION_ID],
                   f"asks {asked}, wanted {[identity.DECISION_ID]}")
    if asked[:1] != [identity.DECISION_ID]:
        return
    decision = item.questions[0]
    check(f"continuity identity decision question ({tag})", decision.instructions,
          render("continuity_identity/question.j2"))
    wanted = [(identity.EXISTING_PREFIX + c["id"],
               render("continuity_identity/option.j2", decision="existing",
                      title=c["title"] or c["id"])) for c in shown["candidates"]]
    wanted += [(word, render("continuity_identity/option.j2", decision=word))
               for word in identity.DECISIONS if word != "existing"]
    REPORT.require(f"continuity identity decision options ({tag})",
                   [(o.id, o.description) for o in decision.options] == wanted,
                   f"offers {[(o.id, o.description) for o in decision.options]}")
    REPORT.require(f"continuity identity decision allows no null ({tag})",
                   not decision.allow_none, "the decision question allows null")


def check_identity_items() -> None:
    # The continuity identity check as decision items (slice G, spec 7.4):
    # `identity.build_items` over the row sets, against direct renders. Each
    # item's context is `item.j2` over its row, holding only the candidates the
    # item offers (`identity._offered`); it asks one question, its `decision`
    # choice, `question.j2`'s: an `existing:<id>` per offered candidate, described
    # by `option.j2` under that candidate's title, then `new` and `uncertain`,
    # each described by `option.j2`. No `id` is asked apart from the decision: a
    # native endpoint answers each question alone.
    for label, rows in IDENTITY_INPUTS.items():
        items = identity.build_items(rows, {})
        for item, row in zip(items, rows, strict=True):
            _identity_item(label, item, row)
    check("continuity identity explain", identity.explain(),
          render("continuity_identity/explain.j2"))


#: The duplicate check's criteria, carried out of its legacy one-call prompt
#: (the `continuity_identity/system.j2` slice G deleted) into the decide-era
#: templates word for word (I8): (fragment, the template it went to). Invent no
#: criterion, and drop none. While the legacy prompt existed, a coverage check
#: held that every sentence of it was inside these tables or its reply format,
#: which `decide/system.j2` owns now.
IDENTITY_CARRIED = (
    ("You are checking whether newly proposed story records already exist.",
     "continuity_identity/question.j2"),
    (("A plot thread is an open narrative question; a commitment is an obligation someone "
      "owes (a promise, a threat or a piece of foreshadowing)."),
     "continuity_identity/question.j2"),
    (('A closed or resolved candidate is never "existing": it is listed so you can see that '
      "business was already settled, and a later development is a new record, not that "
      'one. A row\'s "distinguished_from" ids and the signals under each candidate are '
      "hints, not proof: shared words, characters, scenes or dates make two records worth "
      "comparing, never the same record."), "continuity_identity/question.j2"),
    ("one short sentence", "continuity_identity/explain.j2"),
)
#: Each decision's description, carried out of its legacy bullet word for word
#: after the quoted word, keyed by the decision it describes: `option.j2` must
#: render exactly this text for exactly this decision. A description moved to
#: another word would turn the check around, and the gate (parser-only) cannot
#: see it. The existing bullet is folded with the candidate its option names,
#: so it is IDENTITY_REWORDED's, as is its id sentence.
IDENTITY_OPTIONS = {
    "new": ("when the row is a different question, a continuation, or a related subplot: "
            "something that grew out of a listed record but is business of its own "
            "deserves a record of its own."),
    "uncertain": ("when the transcript cannot tell: the cited evidence and the beats do not "
                  "settle whether the row and a candidate are the same business."),
}
#: Sentences the decide prompt lays out differently, reworded rather than
#: dropped: (old, new, the template `new` went to, why).
IDENTITY_REWORDED = (
    (("A scene was just absorbed, and the extraction proposed opening some NEW plot threads "
      "or commitments."),
     ("A scene was just absorbed, and the extraction proposed opening a NEW plot thread or "
      "commitment."),
     "continuity_identity/question.j2", "one item asks about one row"),
    (("Each proposed record is listed below as a row: its title, the beat the scene gave "
      "it, the transcript evidence the extraction cited, why the extraction called it new, "
      "and up to three existing records of the same type that look similar, each with its "
      "id, status, latest beats and the similarity signals that put it on the list."),
     ("The proposed record is shown above: its title, the beat the scene gave it, the "
      "transcript evidence the extraction cited, why the extraction called it new, and up "
      "to three existing records of the same type that look similar, each with its id, "
      "status, latest beats and the similarity signals that put it on the list."),
     "continuity_identity/question.j2",
     "decide/user.j2 renders the item's context above its questions"),
    (("only when a listed candidate is the same narrative question or obligation, so the "
      "row's beat simply moves that record forward."),
     ('the listed candidate "{{ title }}" itself, only when the row is that same narrative '
      "question or obligation, so its beat simply moves that record forward; a narrower "
      'question, a continuation or a related subplot of it is "new".'),
     "continuity_identity/option.j2",
     ("an existing option is folded with the candidate it names (spec 7.4), so its "
      "description names that candidate, and says what of that candidate is still new, "
      "since each candidate is offered on its own")),
    (('Give that candidate\'s "id" exactly as it is listed. Leave "id" empty unless the '
      'decision is "existing".'),
     ('"existing" is offered once for each listed candidate, as "existing:" followed by '
      "that candidate's id exactly as it is listed."),
     "continuity_identity/question.j2",
     ("a native decisions endpoint answers each question alone, so an id asked apart "
      "from the decision it depends on was answered without it; it is folded into the "
      "decision instead (spec 7.4)")),
)

def check_identity_carried() -> None:
    require_carried("continuity identity", IDENTITY_CARRIED)
    for word, text in IDENTITY_OPTIONS.items():
        check(f"continuity identity option carried ({word})",
              text, render("continuity_identity/option.j2", decision=word))
    REPORT.require("continuity identity options are the item's",
                   ["existing", *IDENTITY_OPTIONS]
                   == [identity.unfolded(o.id)[0] for o in identity.build_items(
                       IDENTITY_INPUTS["bare"], {})[0].questions[0].options],
                   f"carried descriptions for {list(IDENTITY_OPTIONS)}")
    for _old, new, target, why in IDENTITY_REWORDED:
        REPORT.require(f"continuity identity reworded ({target}: {new[:40]}…)",
                       new in _source(target), f"not in {target} ({why})")

# The reconciliation sweep (capstone spec §11.2). Payloads are
# `reconcile.build_payload`-shaped; four inputs so every optional branch of
# `item.j2` is taken both ways: pairs and lifecycle findings with every optional
# field, none, a temporal pair whose second record is an event, and a closure
# showing four scenes.


def _reconcile_fields(title, status="open", kind="", due=""):
    return {"title": title, "status": status, "kind": kind, "due": due}


def _reconcile_record(letter, ref, fields, **over):
    kind = {"thread": "plot thread", "commitment": "commitment"}.get(ref.partition(":")[0], "")
    return {"letter": letter, "ref": ref, "type": kind,
            "line": reconcile.snippet_line(ref, fields),
            "beats": [], "pressure": "", "links": [], "actors": [], "_fields": fields, **over}


def _reconcile_candidate(key, vocabulary, records, signal_text=""):
    return {"key": key, "id": f"candidate-{key}", "vocabulary": vocabulary,
            "records": records, "signal_text": signal_text}


RECONCILE_INPUTS = {
    "pairs+lifecycle": {
        "now": "the twelfth of May",
        "known_scenes": ["001--saltmarch-docks", "002--realm-road"],
        "chronicle": [{"id": "001--saltmarch-docks", "one_line": "Mara came ashore."},
                      {"id": "002--realm-road", "one_line": "The road was long."}],
        "recent": ["002--realm-road"],
        "candidates": [
            _reconcile_candidate("c1", "same_thread", [
                _reconcile_record("A", "thread:find-the-ledger",
                                  _reconcile_fields("Find the ledger", "advanced"),
                                  beats=[{"scene": "001--saltmarch-docks",
                                          "text": "Winifred learned the ledger exists."},
                                         {"scene": "", "text": "Winifred went looking."}],
                                  pressure="stale", actors=["Winifred", "Mara"],
                                  links=["Find the ledger pays_off The midnight deadline"]),
                _reconcile_record("B", "thread:recover-the-harbour-ledger",
                                  _reconcile_fields("Recover the harbour ledger"))],
                "same title; word overlap 0.42; shared characters: Mara"),
            _reconcile_candidate("c2", "commitment", [
                _reconcile_record("A", "commitment:the-midnight-deadline",
                                  _reconcile_fields("The midnight deadline", kind="threat",
                                                    due="2026-05-05"),
                                  pressure="overdue, 5 days ago", actors=["Seraphine"])],
                "its due date has passed (5 days ago)")]},
    "bare": {
        "now": "", "known_scenes": [], "chronicle": [], "recent": [],
        "candidates": [_reconcile_candidate("c1", "thread", [
            _reconcile_record("A", "thread:mara-s-map", _reconcile_fields("Mara's map"))])]},
    "temporal": {
        "now": "Saltmarch Eve", "known_scenes": [], "chronicle": [], "recent": [],
        "candidates": [_reconcile_candidate("c1", "temporal", [
            _reconcile_record("A", "commitment:mara-s-oath",
                              _reconcile_fields("Mara's oath", due="before the bells stop")),
            _reconcile_record("B", "event:the-coronation",
                              {"title": "The coronation", "status": "", "kind": "",
                               "due": "2026-05-13"})],
            "the commitment's due could not be placed on the calendar; the event is in 3 days")]},
    # A closure showing four scenes (three beats and the recent window's
    # line), so an item asks the most evidence questions and leaves one out.
    "four-scenes": {
        "now": "", "recent": ["004--saltmarch-quay"],
        "known_scenes": ["001--saltmarch-docks", "002--realm-road", "003--winifreds-house",
                         "004--saltmarch-quay"],
        "chronicle": [{"id": sid, "one_line": f"Mara passed through {sid}."}
                      for sid in ("001--saltmarch-docks", "002--realm-road",
                                  "003--winifreds-house", "004--saltmarch-quay")],
        "candidates": [_reconcile_candidate("c1", "thread", [
            _reconcile_record("A", "thread:mara-s-map", _reconcile_fields("Mara's map"),
                              beats=[{"scene": sid, "text": f"The map turned up in {sid}."}
                                     for sid in ("001--saltmarch-docks", "002--realm-road",
                                                 "003--winifreds-house")])],
            "no new beat in 75 days")]},
}

def _reconcile_wanted(vocab, pair, refs):
    """The decision's options, tied to ids, in DECISIONS order: a word labelled
    by itself, a pair's directed word once per way the link rules let it run."""
    wanted = []
    for w in reconcile.DECISIONS[vocab]:
        if not (pair and w in reconcile._DIRECTED):
            wanted.append((w, w.replace("_", " ")))
            continue
        wanted += [(reconcile.folded(w, frm, to),
                    render("continuity_reconcile/directed_option.j2", word=w, frm=frm,
                           to=to))
                   for frm, to in (("A", "B"), ("B", "A"))
                   if reconcile._runs(w, refs.get(frm, ""), refs.get(to, ""))]
    return wanted


def _reconcile_evidence(tag, evidence, scenes_shown) -> None:
    """The evidence choices: one per shown scene, up to `EVIDENCE_SCENES`, the
    first `evidence.j2`'s and the rest `evidence_more.j2`'s, each over
    `scene_option.j2`."""
    count = min(len(scenes_shown), reconcile.EVIDENCE_SCENES)
    REPORT.require(f"continuity reconcile evidence slots ({tag})",
                   [q.id for q in evidence] == list(reconcile.EVIDENCE_IDS[:count]),
                   f"asks {[q.id for q in evidence]} over {scenes_shown}")
    offered = [(sid, render("continuity_reconcile/scene_option.j2", sid=sid))
               for sid in scenes_shown]
    for k, q in enumerate(evidence):
        check(f"continuity reconcile {q.id} question ({tag})", q.instructions,
              render("continuity_reconcile/evidence.j2" if k == 0
                     else "continuity_reconcile/evidence_more.j2"))
        REPORT.require(f"continuity reconcile {q.id} options ({tag})",
                       q.allow_none and [(o.id, o.description) for o in q.options]
                       == offered, f"offers {[(o.id, o.description) for o in q.options]}")


def _reconcile_item(label, payload, item, cand) -> None:
    """One candidate's item: its context, its decision and its evidence."""
    tag, vocab = f"{label}, {cand['key']}", cand["vocabulary"]
    scenes_shown = reconcile.item_scenes(payload, cand)
    lines = [line for line in payload["chronicle"] if line["id"] in scenes_shown]
    check(f"continuity reconcile item context ({tag})", item.context,
          render("continuity_reconcile/item.j2", now=payload["now"], chronicle=lines,
                 c={"label": reconcile.LABELS[vocab], "records": cand["records"],
                    "signal_text": cand["signal_text"]}))
    decision, *evidence = item.questions
    check(f"continuity reconcile decision question ({tag})", decision.instructions,
          render("continuity_reconcile/question.j2", vocabulary=vocab))
    pair = vocab in reconcile.PAIR_VOCABULARIES
    REPORT.require(f"continuity reconcile direction said on pairs only ({tag})",
                   (render("continuity_reconcile/direction.j2") in decision.instructions)
                   == pair, "direction.j2 is in a question it does not belong to, "
                   "or missing from a pair's")
    refs = {r["letter"]: r["ref"] for r in cand["records"]}
    wanted = _reconcile_wanted(vocab, pair, refs)
    REPORT.require(f"continuity reconcile decision options ({tag})",
                   decision.id == reconcile.DECISION_ID and not decision.allow_none
                   and [(o.id, o.description) for o in decision.options] == wanted,
                   f"offers {[(o.id, o.description) for o in decision.options]}")
    _reconcile_evidence(tag, evidence, scenes_shown)
    # No option repeats its context (M1).
    repeated = [o.description for q in item.questions for o in q.options
                if len(o.description) > len(o.id) and o.description in item.context]
    REPORT.require(f"continuity reconcile options do not repeat the context ({tag})",
                   not repeated, f"{repeated} already in the context")


def check_reconcile_items() -> None:
    # The reconciliation sweep as decision items (slice G, spec 7.4):
    # `reconcile.build_items` over the same payloads, against direct renders. Each
    # item's context is `item.j2` over its candidate and the scene lines it shows;
    # its `decision` choice is `question.j2`'s under its vocabulary (which
    # includes `direction.j2` on a pair), each option labelled by its own word,
    # and a pair's directed words folded with each direction the link rules allow,
    # described by `directed_option.j2`; no direction is asked apart from the
    # decision. Then one evidence choice per shown scene, up to `EVIDENCE_SCENES`,
    # is `evidence.j2`'s and then `evidence_more.j2`'s over `scene_option.j2`.
    for label, payload in RECONCILE_INPUTS.items():
        items = reconcile.build_items(payload)
        for item, cand in zip(items, payload["candidates"], strict=True):
            _reconcile_item(label, payload, item, cand)
    check("continuity reconcile explain", reconcile.explain(),
          render("continuity_reconcile/explain.j2"))
    REPORT.require("continuity reconcile fixtures ask every kind of question",
                   {len(reconcile.item_scenes(p, c)) for p in RECONCILE_INPUTS.values()
                    for c in p["candidates"]} >= {0, 1, 2, 4}
                   and {c["vocabulary"] for p in RECONCILE_INPUTS.values()
                        for c in p["candidates"]} & set(reconcile.PAIR_VOCABULARIES) != set(),
                   "no fixture shows two scenes, or more than EVIDENCE_SCENES, or none offers "
                   "a direction")


#: The sweep's criteria, carried out of its legacy one-call prompt (the
#: `continuity_reconcile/system.j2` slice G deleted) into the decide-era
#: templates word for word (I8): (fragment, the template it went to). Invent no
#: criterion, and drop none. While the legacy prompt existed, a coverage check
#: held that every sentence of it was inside these tables or its reply format,
#: which `decide/system.j2` owns now.
RECONCILE_CARRIED = (
    (("A plot thread is an open narrative question; a commitment is an obligation someone "
      "owes (a promise, a debt, a threat or a piece of foreshadowing); an event is a dated "
      "occasion on the campaign calendar."), "continuity_reconcile/question.j2"),
    ("Signals say why a candidate is worth a look, never what the answer is.",
     "continuity_reconcile/question.j2"),
    ('When what is shown cannot settle a candidate, answer "uncertain".',
     "continuity_reconcile/question.j2"),
    ("one short sentence", "continuity_reconcile/explain.j2"),
)
#: Each vocabulary's legacy bullet, whole, keyed by the vocabulary it states
#: the criteria of: `question.j2` must render exactly this bullet for exactly
#: this vocabulary (and two commitments, judged by "the same rules", the plot
#: threads' bullet before their own). A bullet moved to another vocabulary
#: would ask one candidate another's question, and the gate (parser-only)
#: cannot see it.
RECONCILE_BULLETS = {
    "same_thread": ('- Two plot threads ("duplicate", "continuation", "subthread", "related", '
                    '"distinct", "uncertain"): "duplicate" only when both records are the same '
                    "question or obligation, so the two should be read as one record; a "
                    'narrower or later question is "continuation" or "subthread", not '
                    '"duplicate". "related" when they bear on each other but are separate '
                    'business, "distinct" when they do not.'),
    "same_commitment": ('- Two commitments ("duplicate", "related", "distinct", "uncertain"): '
                        "the same rules, except that a commitment is never a continuation or "
                        "a subthread of another."),
    "cross": ('- A plot thread and a commitment ("pays_off", "related", "distinct", '
              '"uncertain"): "pays_off" when settling the thread is how the commitment is '
              'settled. A thread and a commitment are never "duplicate".'),
    "thread": ('- Whether a plot thread is finished ("close", "keep_open", "uncertain"): '
               '"close" only when a beat or a scene line shows the question answered. Age '
               "alone is never evidence that a thread is finished."),
    "commitment": ('- Whether a commitment is resolved ("fulfilled", "broken", "expired", '
                   '"keep_open", "uncertain"): "expired" when its occasion went by with nobody '
                   "keeping or breaking it. A passed deadline alone is never evidence that a "
                   "promise was kept or broken."),
    "temporal": ('- A commitment and a dated event ("before", "on", "after", "by", "unrelated", '
                 '"uncertain"): whether the commitment falls due before, on, after or by that '
                 "event. Do not invent a date: when nothing shown ties the commitment to the "
                 'event, answer "unrelated" or "uncertain".'),
}
#: The bullets a vocabulary's question carries: its own, after the one its own
#: refers back to.
_RECONCILE_ASKS = {vocab: (("same_thread",) if vocab == "same_commitment" else ()) + (vocab,)
                   for vocab in reconcile.DECISIONS}
#: Why the direction is no longer asked as letters (spec 7.4).
_FOLDED = ("a native decisions endpoint answers each question alone, so a direction "
           "asked apart from the verdict it qualifies came back none; each directed "
           "word is offered folded with its direction instead, which its option says")
#: Sentences the decide prompt lays out differently, reworded rather than
#: dropped: (old, new, the template `new` went to, why).
RECONCILE_REWORDED = (
    (("You are reviewing a campaign's story ledger for records that may overlap or be "
      "finished."),
     ("You are reviewing a campaign's story ledger for records that may overlap or be "
      "finished."),
     "continuity_reconcile/question.j2",
     "kept verbatim; listed beside the sentence after it, which moves"),
    (("Each candidate below shows one or two records, lettered A and B: the record's line, "
      "its latest beats with the scene each happened in, its deadline or staleness, the "
      "links already recorded on it and the people involved, and then the signals that put "
      "it on the list."),
     ("The candidate above shows one or two records, lettered A and B: the record's line, "
      "its latest beats with the scene each happened in, its deadline or staleness, the "
      "links already recorded on it and the people involved, and then the signals that put "
      "it on the list."),
     "continuity_reconcile/question.j2",
     "decide/user.j2 renders the item's context above its questions"),
    (('For "duplicate", "continuation", "subthread" and "pays_off", give the direction as '
      'letters in "from" and "to":'),
     ('"duplicate", "continuation", "subthread" and "pays_off" are offered once for each way '
      "they may run between the two records, and each option says which record is which."),
     "continuity_reconcile/direction.j2", _FOLDED),
    ('for "duplicate", "from" is the record to fold away and "to" the one to keep;',
     "the same record: fold record {{ frm }} away into record {{ to }}, the one to keep",
     "continuity_reconcile/directed_option.j2", _FOLDED),
    ('"from" continues "to",', "record {{ frm }} continues record {{ to }}",
     "continuity_reconcile/directed_option.j2", _FOLDED),
    ('or is a subthread of "to";', "record {{ frm }} is a subthread of record {{ to }}",
     "continuity_reconcile/directed_option.j2", _FOLDED),
    ('for "pays_off", "from" is the plot thread and "to" the commitment.',
     "record {{ frm }}, the plot thread, pays off record {{ to }}, the commitment",
     "continuity_reconcile/directed_option.j2", _FOLDED),
    (('For "close", "fulfilled", "broken" and "expired", give a reason and name at least one '
      'evidence scene id from the lines shown; without both, the answer counts as '
      '"uncertain".'),
     ("Which scene shown, if any, shows a record above settled: a plot thread's question "
      "answered, or a commitment fulfilled, broken or expired? Answer that scene's id from "
      "the lines shown, or null when no scene shown does."),
     "continuity_reconcile/evidence.j2",
     ("a native decisions endpoint answers each question alone, so evidence asked only "
      '"for" some decision words came back none and lost every status verdict; it asks '
      "what a scene shows instead, and _lifecycle_word still reads a status word with no "
      "cited scene as uncertain. A verdict stands without a rationale (spec 7.4, I4), "
      "which explain.j2 still asks for")),
)
#: Words the decide prompt adds that the legacy prompt never said: each a
#: pointer or a label, never a criterion. Printed, so they stay visible.
RECONCILE_ADDED = (
    (("Each evidence question may name a different such scene; a scene named twice "
      "counts once."), "continuity_reconcile/evidence_more.j2"),
    ("the scene listed above as {{ sid }}", "continuity_reconcile/scene_option.j2"),
)


def _require_reconcile_bullets() -> None:
    for vocab, bullet in RECONCILE_BULLETS.items():
        REPORT.require(f"continuity reconcile bullet names its vocabulary ({vocab})",
                       bullet.startswith(
                           f"- {reconcile.LABELS[vocab][0].upper()}{reconcile.LABELS[vocab][1:]} ("
                           + ", ".join(f'"{w}"' for w in reconcile.DECISIONS[vocab]) + "):"),
                       "its label or its words are not this vocabulary's")
    for vocab, asks in _RECONCILE_ASKS.items():
        question = render("continuity_reconcile/question.j2", vocabulary=vocab)
        REPORT.require(f"continuity reconcile question carries its bullets ({vocab})",
                       all(RECONCILE_BULLETS[w] in question for w in asks)
                       and not any(b in question for w, b in RECONCILE_BULLETS.items()
                                   if w not in asks)
                       and [question.index(RECONCILE_BULLETS[w]) for w in asks]
                       == sorted(question.index(RECONCILE_BULLETS[w]) for w in asks),
                       f"carries {[w for w, b in RECONCILE_BULLETS.items() if b in question]}")
    REPORT.require("continuity reconcile bullets are the vocabularies'",
                   list(RECONCILE_BULLETS) == list(reconcile.DECISIONS),
                   f"bullets for {list(RECONCILE_BULLETS)}")


def check_reconcile_carried() -> None:
    require_carried("continuity reconcile", RECONCILE_CARRIED)
    _require_reconcile_bullets()
    for _old, new, target, why in RECONCILE_REWORDED:
        REPORT.require(f"continuity reconcile reworded ({target}: {new[:40]}…)",
                       new in _source(target), f"not in {target} ({why})")
    for text, target in RECONCILE_ADDED:
        REPORT.require(f"continuity reconcile added ({target})", text in _source(target),
                       f"not in {target}")
        print(f"continuity reconcile adds, in {target}: {text}")


CHECKS = (check_identity_items, check_identity_carried, check_reconcile_items,
          check_reconcile_carried)
