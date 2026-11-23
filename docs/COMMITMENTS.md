# Quests, promises and their deadlines (077)

A commitment is a promise, mission or objective that someone in a campaign owes someone else. Grimoire records
it as an append-only, cited ledger. The ledger keeps what was offered, what the obligors decided and what the
fiction showed came of it. It also keeps the 055 deadline the commitment is due by, even after a refusal. A
later scene can then be checked against the record instead of the narrator's memory. A party that refused a
mission did not complete it.

`grimoire.commitments` holds the pure values, the fold and the codec. `grimoire.commitmentstore.CommitmentLibrary`
is the store-backed, director-facing interface. Neither calls a model, reads a wall clock or touches an actor's
knowledge.

## Records

`Commitment(campaign_id, commitment_id, kind, summary, owed_by, owed_to, deadline_id=None)` describes the
commitment:

- `kind` is `promise`, `mission` or `objective`.
- `owed_by` lists the obligors and `owed_to` the parties owed. Each is 1 to 16 campaign instances, in canonical
  order. Nobody can owe a commitment to themselves (`self_owed`).
- `summary` is director-facing canon. Who in the fiction knows about the commitment is 020's business, not
  this record's.
- `deadline_id` names a deadline in the campaign's 055 calendar.

Every operation cites 1 to 16 020 objective events it rests on. The operations are:

| Operation | What it records |
|---|---|
| `Offer` | The commitment was offered, at the moment of its latest cited event. It may schedule the commitment's deadline in the same write. |
| `Transition` | One later step. See the status table below. |
| `Void` | An offer or step was recorded in error and is withdrawn. 020 cannot retract an event, so this is how a mistaken entry is undone. |

| Status | Who records it | Can follow |
|---|---|---|
| `offered` | the offer itself | none |
| `accepted` | the obligors' decision | `offered` |
| `refused` | the obligors' decision; final | `offered` |
| `partially_fulfilled` | the director's adjudication | `accepted`, `partially_fulfilled` |
| `fulfilled` | the director's adjudication; final | `accepted`, `partially_fulfilled` |
| `abandoned` | the obligors' decision; final | `accepted`, `partially_fulfilled` |
| `lapsed` | the director's adjudication that it fell through without anyone deciding to drop it; final | `offered`, `accepted`, `partially_fulfilled` |

There is no percentage or progress bar. A commitment can end refused or abandoned with its deadline still on
the record.

## Authority

`CommitmentLibrary(store, handle, issued=...)` takes the authorizer's trusted handle as the outcome key, as
021's turn effects do. Every write is one compare-and-swap transaction on the campaign's `scope.json`. A human
can `preview()` it before `commit()`. Narration holds neither key.

A decision (`accepted`, `refused`, `abandoned`) belongs to the obligors:

- `record` takes the campaign's current 003 `ControlAssignment`s. It snapshots each obligor's controller into the
  step. An obligor with no controller is the director's.
- For **every** obligor a player controls, the decision needs that player's own `PlayerContribution`, of a kind
  covering 003's `commitment` domain, for the scene and turn of the step's moment. It also needs their
  `Endorsement` of the exact step (`transition_fingerprint`). One player never decides for another's character.
- A player's decision must cite an event of a scene turn (`no_turn`). A controller that is not a human is
  refused (`non_human_controller`).
- A decision with no player-controlled obligor takes no backing (`unexpected_backing`).

Outcomes (`partially_fulfilled`, `fulfilled`, `lapsed`) are adjudicated by the director from the cited fiction
and carry no backing.

A `Void` needs the endorsement of every player who backed the voided step (`void_fingerprint`). Voiding an offer
withdraws the whole commitment, and with it every step on it not already voided. So it needs the endorsement of
every player who backed any of those steps (`pc_authorship`).

One player contribution backs one step and one obligor. Two obligors backed by one contribution id are refused
(`pc_authorship`), and a contribution id reused on a later step is refused (`reused_contribution`), so an endorsement given before a void cannot be replayed.

The fingerprints cover the campaign, the commitment (or the voided operation), the status, the sorted citations
and the note (or the void reason). They are length-prefixed, so a note cannot pass for a citation.

## Citations, retcons and uncertainty

The library re-reads every citation's support from 020 and 021 on every read, plan and commit:

| Support | Meaning |
|---|---|
| `supported` | The event is on the 020 ledger. If a 021 accepted event shares its id, that event decodes, is active and bridges to exactly it. |
| `revoked` | Its accepted event was revoked: a retcon. The fiction it recorded did not happen. A readable revoked record settles it even when its bridge is no longer on the 020 ledger. |
| `missing` | It is not on the 020 ledger. It may still be syncing from another device. |
| `unreadable` | It could not be checked: a quarantined or damaged record, an unreadable 020 ledger, or a 021 bridge whose accepted event is not here or could not be read. |

A damaged accepted-event file taints only the event of its own id. A problem that names no id taints every bridge.
Support is read from the director's ledger: `read_support` refuses anyone else.

`fold` derives each commitment's state from its ledger and these supports:

- Only a confirmed retcon removes fiction. A step whose citation is `revoked` is skipped (`unsupported`). An
  operation a `Void` withdrew is skipped (`voided`).
- A later step that only followed from a skipped one is skipped too (`stranded`). That covers a step whose status
  is no longer a legal next step, and a step earlier than the commitment's current moment. So a retcon never
  leaves a later scene claiming what the undone fiction alone supported.
- A `missing` or `unreadable` citation keeps its step but marks the state `certain=False`. It is reported as
  uncertainty, never guessed either way. A step that would be skipped however its citation reads (voided,
  stranded, or after a final status) adds no uncertainty.
- The library records no new step on an uncertain history (`uncertain_history`) or on an unsupported or voided
  commitment (`unsupported_commitment`).
- A new step's own citations must all be `supported` (`citation_missing`, `citation_unreadable`,
  `citation_revoked`).
- A retcon that lands between planning and commit refuses the commit (`citation_changed`).

## Deadlines

An offer whose commitment names a `deadline_id` either links to a deadline already in the campaign calendar, or
schedules one with `due=Due(native_date, label, minute=0)`. A scheduled deadline lands in the same write, as
calendar operation `commitment-<operation_id>`. The two receipts land together or not at all.

- A calendar that already holds that operation id is refused (`operation_mismatch`). A commitment never adopts a
  schedule it did not make.
- A deadline needs an initialized calendar (`uninitialized`) and a deadline id (`no_deadline_id`). Linking needs
  the deadline to exist (`unknown_deadline`).

Each state's `deadline` is a `DeadlineState(deadline_id, found, due, label, passed, triggered_by)`, read from the
calendar as it is **now**, since the calendar has no mapping to campaign sequence:

- `passed` is the calendar's own flag: an explicit advance crossed the deadline.
- `overdue` is true only for an open commitment (`offered`, `accepted`, `partially_fulfilled`) whose deadline has
  passed. A refused mission is not overdue.
- `late` says whether a fulfilment was recorded at a campaign-clock time after the deadline. It is `None` with
  no clock time to compare.
- `found=False` means the calendar or the deadline is not there. It is never an all-clear.

Every offer and step snapshots the calendar's current time as `clock_at` (a `ClockTime(fixed_day, minute)`, the
calendar `Moment`'s axis kept as plain values so the pure package stays pure, as 063's rumors do). A voided or
unsupported offer's deadline stays in the calendar for the director to cancel.

## Reading

`read(scope, requested_by="director", as_of=None)` returns a `CommitmentView`: the ledger, the calendar and
each commitment's `CommitmentState` as of campaign sequence `as_of` (`None` means everything recorded), with
whatever made a citation unreadable in `problems`. Reads are director-only and write nothing. A commitment is
canon for the director, never an NPC's memory: give an actor only what 020 says it perceived.

A scope with unresolved sync conflicts is refused (`scope_conflict`). So is an unreadable calendar
(`calendar_unreadable`) or a ledger of an unknown schema (`unknown_schema`). Unknown fields fail closed: dropping
a field a newer writer added could drop an authority record. Every other scope field is preserved.

The parties a commitment names stay reserved in `campaign_taken_ids` ([CHATS](CHATS.md#taken-instance-ids)), so a
new individual never takes a hand-deleted character's id and inherits its promises.

## Referee

047's referee checks a `fulfills` claim (outcome category, field `commitment_id`) against
`Facts(..., commitments=...)`, the states folded as of the review's own `as_of`:

- A commitment refused, abandoned, lapsed, voided or unsupported is `contradicts_commitment`. That is material
  when the history is certain.
- An open commitment is `not_fulfilled`.
- A fulfilled one is clean. It is `uncertain_commitment` when the history is uncertain, and `not_obligor`
  (uncertain) when the actor does not owe it.
- An unknown id is unchecked (`no_commitment`).

See [REFEREE](REFEREE.md).

## Retries and errors

Every method takes an operation id (at most 100 characters) and the reviewed scope etag. A retry with the same
request is a no-op whose `preview()` is empty. The same id with a different request is `operation_mismatch`. A
retried offer that schedules a different `due` is `operation_mismatch` too, including a plan whose twin landed first. A stale etag raises `StaleWrite`, at plan
time or, when another write lands between planning and commit, at commit. A status, note, reason or citation list of
the wrong type is `invalid_record`. Other refusals are `CommitmentError` with a machine `reason`: the ones above, plus
`invalid_record`, `campaign_mismatch`, `duplicate_commitment`, `unknown_party`, `unknown_commitment`,
`illegal_transition`, `out_of_order`, `invalid_void`, `no_controls`, `not_obligor` and `history_changed`. A
refusal writes nothing.

The ledger lives in `scope.json` beside the calendar and shares the native record's size cap. Nothing is ever
dropped to fit.

## Not in this slice

| Not here | Owner |
|---|---|
| Commitment UI beyond the Time page ([TIME](TIME.md), 116), which offers, records and voids: a decision for a character another person plays, made with their own key | 116 later |
| Labelling and runner capture of `fulfills` claims | 077 later / 047 |
| Branching goals, quest stages and dependencies, and planning aids | 077 milestone 3 |
| Delegating or transferring a commitment, scene transfer, ledger compaction | 077 later |
