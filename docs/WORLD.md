# A campaign's world (115)

The **World** page (`#/world/<campaign id>?chat=<ChatRef>&audience=director`) is Wave 8's UI exit for four
director-facing slices: places and routes (059), rumors (063), a faction's decision (057) and what an NPC thinks
others think (060). All of it is the director's. In the table's view (no `audience`, or `audience=table`) the page
says only that it is the director's, calls no `world.*` op and shows nothing of the world: a character learns of
any of it only through what it perceives in play. The campaign hub links the page in the director's view, for the
same chat, and the Chronicle's feature list links it for each of the four slices.

The page is served by the `world` adapter (`grimoire.app.adapters.world`, owner 115), a package with one module
per slice over the existing domain services: `TopologyLibrary` (059), 063's verified rumor ledger read with
`grimoire.rumorledger.replay` and 020's `EvidenceLibrary` for an admitted belief, 057's `decide` and 060's
`admit`, `recall` and `choose_tactic`. It keeps no record of its own. Every op refuses `director_only` unless
`audience` is `director`, and no domain error escapes as a server error: each keeps its reason (an unreadable
ledger is `ledger_unreadable`, and a campaign the library does not hold `missing_campaign`).

The two changes the page makes are each reviewed first: the plan op writes nothing and answers the files the
change would write; the commit op takes the same args (the same `operation_id`, and `expected`, the etag that was
shown) and makes the change as reviewed. A change made by anyone in between refuses `changed`, at the review or at
the commit, and the page reads the section again, so the next review is made against what is there now; the same
operation again, even after other changes, is `already_applied` and writes nothing; the id reused for another change
refuses `operation_conflict`.

## Places and routes (059)

| Op | Args | Answer |
|---|---|---|
| `world.topologies` | `campaign` | `{topologies[]}`: the library's topologies, listed apart from any one's view |
| `world.places` | `campaign`, `topology` (optional: the first otherwise) | `{topologies[], topology: {topology, name, etag, places[], paths[], missing[]}}` |
| `world.find_place` | `campaign`, `topology`, `name` | `{name, matches[]}`: every place the name or an alias matches, each by its exact ref |
| `world.route` | `campaign`, `topology`, `from`, `to` (exact refs), `conditions[]` | `{found, route: {places[], paths[]}, conditions}` |
| `world.path_plan` | `campaign`, `topology`, `path`, `blocked`, `operation_id`, `expected` | `{plan: {already_applied, before, after, files}, topology}` |
| `world.path_commit` | the same args | `{done, topology}` |

The topology is the campaign's view: the source graph with this campaign's road replacements. A road is blocked or
opened only in this campaign, through 059's own `change_path`, and the source topology is never changed. A name
never stands for a place: two places of one name are shown with their refs, a search lists every match without
picking one, and a route is asked only between exact refs. 059's refusals keep their reason: `missing_place`
(a place with no definition, at either end), `unavailable_topology`, `invalid_conditions`, `unsupported_schema`
(an unreadable campaign replacement); the page's own are `no_path` and `no_change` (the road is already so).

Weighted routes, hazards, travel time and maps are not in 059: the page names each as not available.

## Rumors (063)

| Op | Args | Answer |
|---|---|---|
| `world.rumors` | `campaign`, `chat` (optional: its cast names the characters) | `{now, rumors[], etag}` |
| `world.belief_plan` | `campaign`, `chat`, `rumor`, `stance` (believed, suspected or rejected), `operation_id`, `expected` | `{plan: {belief, holder, text, already_applied, files}, rumors}` |
| `world.belief_commit` | the same args | `{done, rumors}` |

Each rumor shows who told whom (and to whom it is attributed), whether it is faithful or distorted (and how), what
it rests on, its channel and the route conditions that carried it, when it was sent and arrives on the campaign's
055 calendar, whether it is delivered, due or on its way, and whether its recipient's belief was admitted.

A recipient believes a rumor only when the director admits that belief: 063 delivers a report, never a belief.
Admission is 020's `admit_belief` through `EvidenceLibrary.append`, citing the rumor's report, for a delivered
rumor whose recipient is a director-controlled NPC; a player character's belief is the player's own and is not
offered. The belief is named for the operation (`admit-<operation id>`), so a retry finds it. Refusals:
`no_rumor`, `not_delivered`, `already_admitted`, `changed`, `operation_conflict`.

Sending and delivering a rumor are not offered: they are `RumorLibrary`'s acts on the rumor queue's own
application key and store grant, which the app does not hold yet. Spreading a rumor through a faction or public
media is not in 063. Both are named as not available.

## A faction's decision (057)

| Op | Args | Answer |
|---|---|---|
| `world.cast` | `chat` | `{cast[]}`: each member's ref, name, role (npc or player) and whether it is an agent |
| `world.faction_preview` | `chat`, `group`, `leader`, `goals[]` (1 to 16), `policy`, `proposal`, `choice`, `members[]` (`{actor, choice}`), `told[]` | `{saved: false, decision, members, dissent[], agreeing[], told}` |

057 is a pure policy with no store. The director lays out a group from the chat's cast, its members and what each
prefers, and the leader's explicit choice; the page shows what `decide` answers: the decision, the dissent it
keeps beside it (an intention, never an act) and who is told. The group and its membership exist only for that
answer, and the page says plainly that nothing is saved and nothing carries the choice out. A player's character
can neither lead nor prefer (`npc_authority`), and a character outside the cast refuses `no_actor`.

Accepting a dissenting member's help as an event, and a faction deciding by itself, are not in 057: both are named
as not available.

## What an NPC thinks others think (060)

| Op | Args | Answer |
|---|---|---|
| `world.mind` | `chat`, `owner` (an NPC of the cast) | `{owner, now, evidence[], truth: {rulings[], beliefs[]}}` |
| `world.mind_preview` | `chat`, `owner`, `hypotheses[]` (`{id, about[], claim, confidence, evidence[], at, supersedes}`), `tactic` (`{hypothesis, minimum, credited, fallback}`, optional) | `{saved: false, admitted, current[], tactic, truth, unchanged}` |

The director writes each hypothesis in their own words, about what another character believes, or what one character thinks a second believes (060's
outer-to-inner order), citing evidence the NPC
holds; the page holds them from the newest evidence they cite. The preview admits them in order into a ledger that
lives for that one answer, recalls what the NPC holds current, and proposes the credited tactic or the fallback.
It shows the director's truth and every cast member's actual beliefs, read again after the preview, and
`unchanged` says that nothing in the library changed. 060's refusals keep their reason and name the hypothesis:
`unavailable_evidence`, `npc_authority_required`, `nonmonotonic_revision`, `invalid_confidence`,
`not_current_hypothesis`, `unresolved_actor`.

Nothing is inferred and nothing is saved: the hypotheses being written are kept only as the director's draft, per
NPC, in the browser tab. Inferring a hypothesis from play and keeping hypotheses are named as not available.

## The page

Each section is a card of real buttons and labelled fields, laid out in one column at phone width, and each reads
on its own: a section that is refused shows the reason in its own card with **Read again**, and the others keep
working. A topology whose view is refused still leaves the others to choose from.

What the director lays out is a draft kept across leaving the page, keyed per chat and character: what is typed,
and what is picked or ticked too (a route's two ends, a faction's leader, its members and who is told, the
characters a hypothesis is about and the evidence it rests on). A hypothesis being written is that NPC's own.

Only the newest answer is drawn: the NPC chosen last, the topology chosen last (a review of the one before is
dropped), the last search and route. A search or route answered before a road changed is cleared once it changes.
A review is cancelled or made with its own buttons; a stale one is dropped and the section read again.

Tests: `backend/tests/test_app_world.py` (every op, its refusals, privacy, stale reviews and retries, run on a
synthetic library) runs `js/world.e2e.mjs` against a real app server; `js/world.test.mjs` covers the page's logic
and its late answers.

## Not proved here

The page runs end to end in fakedom against a real app server. It has not been used in a real browser, with a
screen reader or on a phone.
