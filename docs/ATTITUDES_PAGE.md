# An NPC's attitudes, checked (116)

The **Attitudes** page (`#/attitudes/<ChatRef>?audience=director`) is Wave 9's UI exit for 078's first slice
([RELATIONSHIPS](RELATIONSHIPS.md)): the director checks one NPC's attitude toward another actor or a group against
that NPC's own evidence. A chat's **Details**, under "The director's", links to it; the link names no audience.

The page is served by the `attitudes` adapter (`grimoire.app.adapters.attitudes`, owner 116) over 078's pure
`relationships.admit`, the campaign's evidence ledger as the director reads it (020) and the campaign hub's
`knowledge` perspective. **Nothing is saved.** 078 has no store, and no brief or goal reads a disposition yet, so no
NPC plays from an attitude: the page says both plainly.

## Whose it is

An NPC's attitudes are director-authored NPC interiority. Every op refuses `director_only` unless `audience` is
`director`; the table's view says whose this is and shows nothing. A player character's feelings are its player's:
the page never offers a player character as a holder, `attitudes.grounds` refuses one `pc_interiority`, and so does
078 when a check names one.

## Ops

| Op | Args | Answer |
|---|---|---|
| `attitudes.cast` | `chat`, `audience` | `{cast[{actor, name, role, agent, may_hold, why_not}], groups[{actor, name}], dimensions, bipolar, max_degree, max_grounds, max_text, max_held, now, saved: false, not_available}` |
| `attitudes.grounds` | `chat`, `holder`, `audience` | `{holder, evidence[{evidence, type, channel, content, at, attributed_to}], beliefs[{belief, text, stance, at}]}` |
| `attitudes.check` | `chat`, `audience`, `attitude`, `held` (optional) | `{saved: false, now, verdict: {admitted, reason, attitude}, held[], current[], unchanged}` |

- **Holders** are the chat's cast that is a `character`, an agent and director-controlled now. **Targets** are the
  rest of the cast and the campaign's groups (its instances of kind `group`).
- **Grounds** are the holder's own evidence and current beliefs **exactly as the campaign hub shows them for that
  actor** (`campaign.knowledge` with that actor, the director's view), never another actor's: the same perspective
  the Chronicle offers evidence to cite from. A check whose grounds are not among them is refused
  `unavailable_grounds` (078's own reason for the same thing), and 078 checks them again itself.
- **An attitude** is `{id, holder, toward, dimension, account, degree?, evidence[], beliefs[], at?, supersedes?}`.
  `at` is its moment and defaults to the campaign's present (`now`: its latest recorded moment, as 060's preview
  takes it); the change is director-authored, with the provenance "director's check on the Attitudes page".

## The ledger: client-held, re-validated from scratch

078 keeps an append-only ledger in memory and has no persistence. The page holds, **in its own memory while it is
open**, the attitudes a check admitted (`held`); leaving the page drops them. Every `attitudes.check` builds a new,
empty 078 ledger, admits the held attitudes again in order against the library as it is now, and then the new one;
nothing the client sends is trusted as admitted. A held attitude that is no longer admitted (its grounds changed)
refuses the check with 078's reason, and the page stops holding any. The answer is 078's **verdict** on the new
attitude (`admitted`, or its refusal `reason`), the holder's current attitudes in that ledger (`attitudes_of`), and
`unchanged`: the evidence ledger's digest and contents are the same after the check as before.

## Refusals

A verdict keeps 078's reason ([RELATIONSHIPS](RELATIONSHIPS.md#admission)): `unavailable_grounds` (not the holder's
own, or held only later), `unrelated_grounds` (no ground overtly concerns the target: a perception alone never says
who did it), `future_moment`, `pc_interiority`, `unknown_control`, `ambiguous_control`, `unsupported_holder_kind`,
`not_agent`, `not_agent_or_group`, `unresolved_actor`, `unsuperseded_conflict` (the holder already holds that
attitude toward them here: forget the held one first), `nonmonotonic_revision`, `stale_supersession`,
`different_chain`, and the shape refusals (`invalid_text`, `invalid_degree`, `self_attitude`,
`ungrounded_attitude`, `duplicate_ground`, `budget_exceeded`, `unknown_dimension`, `invalid_id`).
`npc_authority_required` cannot arise here: every check is the director's. The adapter's own refusals are
`director_only`, `invalid_field`, `no_actor` and `ledger_unreadable` (the evidence ledger left records out).

## The page

1. **Whose attitude**: "Nothing is saved", the NPCs that may hold one, and the cast that may not, with why.
2. **The attitude** (for the chosen NPC): toward whom, the dimension, an optional degree (trust and affection from
   -3 to 3, the rest from 0 to 3), the NPC's own account, and what it rests on, picked from that NPC's own grounds.
3. **Verdict**: admitted or refused, with 078's reason in plain words and kept as is; focus moves to it. An answer
   to a check asked before the form changed is never drawn.
4. **Held on this page**: what has been admitted while the page is open, each with **Forget**.

## Not on a screen, and why

Named, never offered (`ui.unavailable`):

- saving an attitude: 078 has no store, sync or storage adapter yet;
- an NPC playing from its attitudes: the per-NPC brief (027) and goals (039) do not read dispositions yet;
- public reputation held by a community and a change of allegiance: 078's later milestones.

## Tests

`backend/tests/test_app_attitudes.py` drives every op through the app's adapter contract over the synthetic card
chat (Mara Vell as the envoy, Anya Rook as the newcomer the owner plays, Tamsin Hale as the dockhand, and the harbor
guild): Mara's resentment of Anya from the report she received is admitted; the same attitude for Tamsin is refused
`unavailable_grounds` with Mara's report and `unrelated_grounds` with Tamsin's own rain; a player character is
refused `pc_interiority`; the table's view `director_only`; held attitudes are admitted again on every check and a
forged one is refused; the library is byte for byte unchanged. `js/attitudes.e2e.mjs` runs the page end to end in
fakedom against a real app server; `js/attitudes.test.mjs` unit-tests its logic.

## Not proved here

The page runs end to end in fakedom against a real app server. It has not been used in a real browser, with a
screen reader or on a phone.
