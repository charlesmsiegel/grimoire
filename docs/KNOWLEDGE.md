# Knowledge and perspective contract

**Status:** first slice of [Knowledge And Perspective](roadmap/sprints/knowledge-and-perspective.md) (sprint 012). It is implemented as pure, in-memory, provider-free code in `backend/src/grimoire/core/knowledge.py` (records and ledger) and `perspective.py` (projections and belief admission).
- Nothing is persisted; 020 owns the stored form.
- No model is called, and no prose is read or rendered.
- Field names are a first-slice contract, not a frozen file schema.
- None of this is app-integrated.

Four things are kept apart:
- **what happened** (events);
- **what an actor perceived or was told** (evidence);
- **what that actor concludes** (beliefs);
- **what canon holds about a proposition** (rulings).

**Access is not truth.** Receiving, repeating or believing a proposition never makes it established. Being a participant, a group member or graph-connected never grants evidence.

## Vocabulary

All records are frozen dataclasses that belong to one campaign.
- IDs use the [identity](IDENTITY.md) segment grammar.
- Actors are `InstanceRef`s in the record's campaign.
- `provenance` is attribution text (`director`, `rule:x`, `scene:s1/t4`), not authority.

| Record | Meaning |
|---|---|
| `Moment(sequence, scene_id=None, turn_id=None)` | A position in campaign order. Only `sequence` (a non-negative `int`) orders; the labels only describe. |
| `Subject(kind, ref)` | A typed thing a proposition overtly refers to, or a ruling rests on. Kinds: `event` (an event id), `instance` (exact `InstanceRef` text), `definition` (exact `SourceRef` text), `relation` (an opaque id owned by the [typed graph](roadmap/sprints/typed-relationship-graph.md)). Subjects themselves never appear in an actor projection. |
| `Event(…, summary, moment, provenance, participants=())` | An accepted objective happening. `participants` describes the event and **grants no access**. |
| `Proposition(…, text, moment, provenance, about=())` | Something that can be asserted or believed. It has no truth field. `about` lists only what the text **overtly** refers to, recognisably to anyone who holds it, and no event later than the proposition. |
| `Ruling(…, proposition_id, standing, moment, provenance, grounds=())` | A director or adjudication decision. `standing` is `established`, `refuted` or `unknown`; `established` needs at least one ground. |
| `Perception(…, holder, event_id, channel, percept, moment, provenance, confirmed)` | The holder sensed part of an event. |
| `Report(…, holder, proposition_id, channel, moment, provenance, confirmed, sender=None, attributed_to=None, addressed=False, sent_at=None)` | The holder received an assertion at `moment`. See the field notes below. |
| `Belief(…, holder, proposition_id, stance, moment, authored_by, evidence_ids, supersedes=None, contribution_id=None)` | The holder's own conclusion. `stance` is `believed`, `suspected` or `rejected`. |
| `Disclosure(…, event_id, moment, provenance)` | From `moment` on, the table (players, out of character) may read the event. No actor learns it. |

`Report` fields:
- `sender` is the actual originator, and only the director sees it.
- `attributed_to` is who the holder takes the source to be. `None` means anonymous, and it may differ from `sender` (a forged letter).
- `addressed` means the sender knowingly sent it to this holder, at `sent_at`. It is `False` for overheard or intercepted reports.

`channel` is an open vocabulary (`witnessed`, `overheard`, `told`, `written`, `telepathic`, `backstory`, …). Transmission semantics belong to [diegetic communication](roadmap/sprints/diegetic-communication.md) (032).

`confirmed` is a required `bool` with no default. A transcript line spoken while an actor was off-stage is at most an unconfirmed report.

### Standing and stance

The handoff's single status list is deliberately split into two axes:

- **Standing** is canon's view of a proposition as of moment M:
  - the latest ruling at or before M;
  - otherwise `reported`, once any report of it has been received (confirmed or not);
  - otherwise `unasserted`.
- **Stance** is one actor's belief: `believed`, `suspected` or `rejected`.

So the handoff's `established`, `reported` and `unknown` are standings, and `suspected` and `rejected` are stances. `refuted` and `unasserted` are additions. Standing never depends on anyone's belief.

## The ledger

`Knowledge(campaign_id, events, propositions, rulings, evidence, beliefs, disclosures)` validates one campaign's records and is immutable.
- Inputs may be any iterables.
- Fields are tuples sorted by ID, so equal record sets compare equal however they were supplied.

Every input is first type-checked: each family must be an iterable of its record type, else `invalid_record`. Validation then runs family by family (events, propositions, rulings, evidence, beliefs, disclosures). Each family gets an identity pass (campaign, duplicates), then each record's rules in ID order. The belief family closes with its chain checks.

The first failure raises `KnowledgeError(ValueError)`. It has `.reason` and `.records`, the offending records named `"<family>:<id>"`, so a store can quarantine them. Messages never contain record text, so they are safe to log.

| reason | rule |
|---|---|
| `invalid_record` | a field is malformed (bad id, empty text, non-bool flag, wrong type) |
| `campaign_mismatch` | a record or reference belongs to another campaign |
| `duplicate_id` | an ID is unique per family; perceptions and reports share the evidence namespace |
| `unknown_event` / `unknown_proposition` / `unknown_evidence` / `unknown_belief` | a reference does not resolve. Nothing is rebound by name or dropped: removing evidence that a belief cites names both records |
| `before_event` | a perception or disclosure is earlier than its event |
| `about_future_event` | a proposition is about an event later than itself (otherwise holding it would reveal a director-only future event) |
| `before_proposition` | a report (including its `sent_at`), belief or ruling is earlier than its proposition |
| `ungrounded_ruling` / `ruling_cites_future` / `conflicting_rulings` | an established ruling has no ground; it rests on a later event; or one proposition has two rulings at one sequence |
| `invalid_sent_at` | `sent_at` is given exactly when `addressed` is set, with a sender, and is not later than receipt |
| `unsupported_belief` | a belief cites no evidence |
| `foreign_evidence` / `unconfirmed_support` / `future_evidence` | cited evidence is someone else's, unconfirmed, or later than the belief |
| `unrelated_support` | no cited item is a report of the same proposition, or shares a subject with the proposition's `about` (a perception's subject is its event; a report's are its proposition's `about`). Related evidence may contradict, so a correction can reject a lie. Unrelated evidence cannot back a belief, which closes "give an NPC the secret by citing their rain perception" |
| `invalid_supersession` / `forked_supersession` / `unsuperseded_conflict` | for each (holder, proposition), beliefs form one chain: one root, each later belief supersedes an earlier one of the same holder and proposition, and no forks. So exactly one belief is current |
| `reused_contribution` | a player contribution backs at most one of a holder's beliefs |

## Projections

Every projection names its perspective and takes a keyword `as_of` (a non-negative `int`, else `invalid_as_of`). It sees only records at or before that sequence, ordered by `(sequence, id)`.

- **`actor_knowledge(knowledge, actor, *, as_of)`** returns an `ActorKnowledge` with these buckets:
  - `evidence`: the actor's own **confirmed** evidence, giving content (the percept, or the reported proposition's text), channel, moment, and the reported proposition and apparent author for reports;
  - `sent`: addressed reports the actor sent, from `sent_at`, with no receipt time or confirmation, so delivery and loss look alike;
  - `current` and `history`: the actor's own beliefs, without authorship. Superseded beliefs stay as history the actor can recall;
  - `inherited` and `inherited_history`: `None` here (not computed).

  Never included: events, subjects, participants, rulings or standing, true senders, provenance, belief authorship or control, unconfirmed evidence, disclosures, or other actors' records. The result depends only on the actor's own evidence, own beliefs and the reports they sent. An actor with none gets an empty view, which is the normal fail-closed answer. An actor of another campaign gets `campaign_mismatch`.
- **`knowledge_about(knowledge, actor, subject, *, as_of)`** is the actor projection restricted to items about `subject`: perceptions of that event, and reports, sent reports and beliefs whose proposition overtly mentions it. It is the single consumer contract for graph and retrieval views (see the obligations below). The subject is never resolved against the ledger, so an unknown subject and an existing-but-unheld one give the same empty answer.
- **`player_knowledge(knowledge, player_id, controls, *, as_of)`** returns, for each `character` the player currently controls (confirmed per actor with `controller_of`), that actor's projection. A belief that the present controller did not author, such as one from the actor's NPC era, moves to `inherited` (current) or `inherited_history` (superseded) and is never shown as the PC's own. `table_events` lists the events disclosed by `as_of`, with the earliest disclosure moment. `AuthorityError` is raised for `DIRECTOR`, for a player who controls no character, and for ambiguous control.
- **`director_knowledge(knowledge, *, as_of, requested_by)`** is the separate truth projection, holding raw records ordered by `(sequence, id)`: events, propositions with standing and governing ruling, rulings, all evidence (unconfirmed items included and flagged; a sent report appears from its `sent_at`), beliefs and disclosures. `requested_by` must be `DIRECTOR`. This makes call sites explicit and auditable; it does not authorize the caller, which is the executor's job (035, 104).
- **`standing(knowledge, proposition_id, *, as_of)`** returns the standing defined above.

`actor_view(actor, definitions, knowledge=None)` accepts only an `ActorKnowledge` (exactly that type, else `not_actor_knowledge`) for this actor (else `actor_mismatch`). `ActorKnowledge` is a public value type. The guarantee is that projections never put director data in it and that `actor_view` refuses anything else.

## Belief authorship (player sovereignty)

`belief` is a protected PC domain ([player sovereignty](PLAYER_SOVEREIGNTY.md)). A writer must call `admit_belief(belief, proposition, controls, contribution=None)` when a belief is authored. Refusals raise `BeliefAuthorityError`, an `AuthorityError` (never an `OSError`), with a `.reason`:

| reason | rule |
|---|---|
| `unsupported_holder_kind` | the holder is not a `character`. Wave 1 control is keyed by actor id only; sentient objects wait for [entity facets](roadmap/sprints/entity-facets-and-instance-model.md) (009) |
| `unknown_control` | the holder has no control assignment. Ambiguous control raises `controller_of`'s `AuthorityError` |
| `wrong_author` | a director-controlled holder's belief is not authored by `DIRECTOR`, or carries a contribution |
| `pc_belief_authorship` | a player-controlled holder's belief is authored by anyone except that controller, the director included |
| `invalid_expression` | the backing `PlayerContribution` is missing, or is not this controller's `interiority` contribution for this actor in the belief's scene and turn with the same contribution id |
| `not_verbatim` | the proposition text differs from the contribution text after `strip()` (Wave 1's verbatim rule) |

A proposition that is not the belief's raises `KnowledgeError("invalid_record")`. Delegated PC belief authoring fails closed for now (owner: 035, with 003's delegations).

`Knowledge` cannot re-check authorship, because Wave 1 control has no history; the `inherited` bucket surfaces any non-controller belief instead. Perceptions and reports about a PC are open-domain world facts and need no player authorship.

## Trust boundaries and writer obligations

- Rulings come only from director or adjudication writers, never from narration or player input.
- Beliefs and player contributions follow the player-sovereignty input channels. The labeller that turns contributions into beliefs is not the narrator, and must keep the player's stance: `admit_belief` checks the proposition is the player's words but cannot see whether "rejected" was meant (stance in the contribution itself is a 035 question).
- A **percept is sensory register only**, Wave 1's `sensation`: "a sealed charter changing hands", not "Ines realizes the duke betrayed her". Interpretation, feeling and deduction are beliefs, which are sovereignty-checked. Text is not validated.
- A proposition's `about` names only what its text overtly refers to, as every holder would understand it. "The masked man fled north" is not about `instance:duke`, even if the director knows the masked man is the duke. That identification is a separate proposition. `about` is never padded to manufacture support or awareness, because it decides what a holder's items are about and which evidence counts as related support.
- IDs are not secrecy-safe text. Keep proposition and evidence IDs neutral (not `lie-about-duke`), and never render IDs into model prompts.

## Consumer obligations

| Consumer | Must |
|---|---|
| [Typed graph](roadmap/sprints/typed-relationship-graph.md) (017) | Gate actor-scoped traversal only through `knowledge_about` and the actor projection. An actor-scoped view may present only those items, which overtly mention the subject. They never expose the subject's canonical fields, participants or edges, and never permit expanding from it. A refuted proposition about X is still only a proposition the actor holds. Never infer knowledge from connectivity |
| [Event/evidence persistence](roadmap/sprints/durable-event-evidence.md) (020) | Persist these records' meaning in 010 envelopes, quarantine using `KnowledgeError.records`, and own retraction and retcon semantics. Removing cited evidence currently fails loudly rather than silently weakening a belief |
| [Per-NPC generation](roadmap/sprints/per-npc-generation-runtime.md) (027) | Build each NPC's prompt from that actor's `ActorKnowledge` only. Refuse `ActorView.knowledge is None` rather than read it as unfiltered. Never pass `DirectorKnowledge` or render IDs. Call `admit_belief` before writing a model-proposed NPC belief |
| [Context retrieval](roadmap/sprints/context-retrieval.md) (031) | Pack only `knowledge_about` and actor-projection items. A rumour is evidence of a report, not of truth |
| [Diegetic communication](roadmap/sprints/diegetic-communication.md) (032) | Emit `Report`s with honest `addressed`, `sent_at`, `attributed_to` and `confirmed`, and never mark receipt as confirmed without evidence of it |

## Open questions and owners

- Redacting director-only events, rulings and true senders from exports: the export owner.
- Retcons that invalidate witnessed evidence: 020.
- Timed control history and delegated PC beliefs: 009 and 035.
- Graded uncertainty beyond three stances: [NPC theory of mind](roadmap/sprints/npc-theory-of-mind.md).
- Knowledge crossing campaigns: [cross-campaign scene transfer](roadmap/sprints/cross-campaign-scene-transfer.md).
- Opaque per-view handles instead of IDs: 027 or 031, if prompts ever need references.
