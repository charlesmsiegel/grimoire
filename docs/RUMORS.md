# Authored rumors (063)

The headless `RumorLibrary` queues a single authored sender-to-recipient edge. Its source is confirmed evidence held by the sender at the authored Moment. The exact transmitted text may differ; provenance, distortion, confidence, attribution, and route constraints are director-auditable. The queue does not change an event or ruling and receipt creates no belief. An authorized caller may separately admit a belief through 020. Retelling requires a new edge sourced from the sender's received report.

## Campaign time

A rumor is timed on its campaign's own 055 calendar ([CALENDARS](CALENDARS.md)): `sent_on` and `due_on` are `CampaignTime(fixed_day, minute)` values, the calendar `Moment`'s axis kept as plain values so `grimoire.rumors` stays pure (`invalid_time` outside 055's range, or a due time before the send). They are not knowledge Moments; the separate `authored_at` Moment orders the 020 records. `enqueue` reads the campaign calendar and refuses a send dated after its current time (`future_send`); a campaign whose calendar was never initialized queues and delivers nothing (`calendar_uninitialized`). `advance` takes no time of its own: it delivers what the calendar's current time has made due, records that time as a nondecreasing watermark even without a delivery, and refuses a calendar now behind it (`clock_backwards`, a replaced history). It accepts explicit proximity, affiliation, channel and attention conditions and processes at most `limit` (1–500, default 100) eligible due edges in `(due_on, communication_id)` order; blocked entries remain pending and do not starve eligible entries. Remaining capacity reconciles blocked edges with staged attempts. The returned counts and `trace` are director-only. No distance, relationships or attention are inferred.

An NPC send requires an issued trusted application handle and explicit director control. A PC send requires the current controller's issued trusted human handle and a typed, exact speech contribution matching actor, scene and turn, admitted using current stored controls. A retry reconciles the original proof. Store permissions must separately authorize evidence writes; a trusted handle is not a grant.

## What the sender sees

A delayed report is appended only on delivery, so a raw 020 sender projection would change the moment it arrived and tell the sender it had. Instead `EvidenceLibrary` gives every assembly it reads the campaign's sends as `Knowledge.dispatches` ([KNOWLEDGE](KNOWLEDGE.md#projections)), from the hash-verified `rumors/<campaign>` ledger (`grimoire.rumorledger`). Each enqueued edge appears in its sender's `sent` from the enqueue Moment, before and after delivery alike, with the transmitted text, recipient and channel and nothing about route, distortion or receipt. Every reader built on the library sees the same: the scene runner's NPC and controller packs, context packs (031), campaign API views, memory and goals. If the rumor ledger cannot be read and verified, disagrees with a delivered report, or is missing while a delivered rumor report exists (a device that has the evidence but not the unsynced ledger), no actor is shown any send and the director's `read` reports the problem `rumors/<campaign>:unreadable`. The evidence ledger itself is intact, so evidence writes are not refused for it.

`outbox` returns immutable sender-safe authored details without route, distortion, provenance, delivery status or receipt time. `RumorLibrary.actor_knowledge` requires `requested_by` and an issued trusted application handle for director access or the current controller's issued trusted human handle for actor access; the campaign must match the actor. Human reads use the controller projection, separating earlier controllers' beliefs as inherited; director reads use the library's actor projection. This slice makes no UI claim.

## Persistence and recovery

Requests and explicit attempts are stored in the hash-chained `rumors/<campaign>` authority ledger. Evidence feasibility is checked before reserving a new request and before pinning each attempt, so rejected oversized or invalid output does not strand an operation. Attempts pin expected ordinary 020 record values before each single-record evidence append. On retry, exact committed evidence is reconciled before a newer supplied Moment is considered. Missing output may get a new audited attempt when an unrelated evidence write advanced the high-water mark; conflicting or quarantined output fails closed. A delivery already committed is reconciled even if its route is now blocked. If it was staged but absent, current conditions must permit delivery again. Reopen the authority after an incomplete or poisoned write. This is local crash reconciliation, not distributed exactly-once transport.

## Proofs

`backend/tests/test_rumor_scenarios.py` is the sprint's proving scenario, on the synthetic card library and a real scene runner. Anya laughs at the Vell banner on the quay and only Oswin sees it. Oswin tells Bryn faithfully that evening; Bryn's letter to Mara, away up the coast, says Anya tore the banner down and spat on it, and takes 21 calendar days. Mara's authored agenda calls out whoever slandered her house only while she holds that proposition, and otherwise trades. She trades on turn one, nothing arrives after a week, a face-to-face route does not carry the due letter while the courier route does, hearing alone leaves her trading, and once the director admits her belief she chooses the challenge, with the letter's account and never the true insult in her brief. The insult event, its one witness's perception and the rulings never change, the letter's proposition stays merely reported, and Bryn's view of what she sent is identical before and after delivery. `test_rumor_store.py`, `test_rumor_recovery.py` and `test_rumors.py` prove calendar timing, routing, the dispatch overlay and its fail-closed cases, and crash reconciliation.

## Not in this slice

| Item | Owner |
|---|---|
| Syncing the rumor ledger between devices (a peer without it shows no sends, never the raw ones) | A later sync slice |
| Inferring proximity, affiliation, channel or attention; supplying delivery conditions from the world | 063 later |
| Admitting a belief on receipt without the director's act | 063 later |
| Any UI for authoring, tracing or delivering rumors | 115 |

## On screen

The director's World page (115) traces a campaign's rumors on its calendar and admits a delivered rumor's recipient's belief, reviewed first; it neither sends nor delivers one. See [WORLD](WORLD.md).
