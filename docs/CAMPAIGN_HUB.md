# Campaign hub API (113, first slice)

**Status: backend adapter only.** `grimoire.campaignapi.CampaignApi` is the typed, authority-aware adapter the campaign hub screen (Sprint 113) calls. It is not yet served over HTTP and has no page: both wait for Sprint 108's shell contract (navigation slots and route registration), which 113 must not invent. Until then it is reachable only from trusted Python code, as here and in its tests.

It keeps no record of its own. Every write goes through the service that owns the record:

| Op | What it does | Owner of the write |
|---|---|---|
| `hub` | The chat's campaign situation for an audience (read only) | none |
| `knowledge` | One perspective: the table's, or one actor's (read only) | none |
| `label` | Ask the labelling model to frame the open action, optionally against an obstacle | 035 runner, 029 |
| `confirm` | The acting player confirms the latest labelling, unedited | 035 runner, 029, 040 |
| `decide` | The director adjudicates (`basis: adjudicated`), or the acting player consents to a setback (`basis: consent` with the frame's `fingerprint`) | 035 runner, 029, 040 |
| `propose_effect` | The director proposes a turn's effect whole, as structured 021 operations and 023 holdings lines; what an obstacle attempt made due comes first | 035 runner, 021 |
| `endorse_effect` | The player's own key on the effect exactly as shown (its `fingerprint`) | 035 runner, 021 |
| `approve_effect` | The director's key; the last key accepts the effect, once | 035 runner, 021 |
| `recover_effects` | Settle (or `abort`) acceptances of this chat that were cut short; an abort of an effect the player endorsed needs `player_key: true`, the player's own key given knowingly | 035 runner, 030 |

Every op takes `chat` (a `ChatRef` string). Write ops return `{"done": ..., "hub": ...}`, the hub as it stands after the write, so a screen never shows a stale situation beside a fresh result. A refusal is an `ApiRefused` with the owning service's own reason (`fingerprint_mismatch`, `already_accepted`, `unknown_effect`, `not_at_table`, `changed` for a stale write, ...) and writes nothing unless that service's refusal says so (035 records a labelling the model refused, so the table sees why). A hub that cannot be read after a committed write comes back as `hub: null` with its problem, never as a refusal of the write.

## Who acts, and with which key

In this slice the library owner is both the table's one player and its director (one person, one library, as the [text API](TEXT_API.md)); only the owner can construct a `CampaignApi`. The hub never gives one key for the other: endorsing an effect and approving it are two separate calls, even when one person makes both (021's solo-play rule). A consent is the acting player's own `decide` naming the fingerprint of the frame they were shown; a consent for another frame or an earlier revision is refused. The hub's `actions` say which ops are available now and why not (no labeller, or play is paused). Labelling is a model call: it is available only when the app passes a labeller, its untrusted handle and an inference policy allowing `labelling`; otherwise `label` is refused (`no_labeller`) and the hub lists it as unavailable with that reason. Nothing is proposed from narration: an effect is always the director's structured proposal.

## Who sees what

`audience` is `table` (the default) or `director`.

- **The table** sees the cast (each actor's controller and role, whether on stage, any staged absence), the persona, the stage, who is present and its own characters' party standing, its own characters' holdings and items, the obstacles (040 keeps no hidden facts), each turn's labellings and the effects it holds a key on (never the transcript, which is the chat page's), the open action with its frame and outcome, and its own characters' knowledge (with what the director wrote while a character was an NPC set apart as `inherited`).
- **The director** also sees `truth`: 021's accepted events (and revoked ones) and the cast's current 017 relations, which are world truth from their moment on; every effect, including those no player character acts in; every party membership and 021's problems; every actor's holdings and items; and any one actor's perspective by asking `knowledge` for it with `audience: director`.

An actor's perspective is that actor's observations, sent reports and beliefs, each labelled with its `kind` and `holder`. It is never world truth and is never folded into the table's: another character's perspective asked at the table is refused (`director_only`), and an NPC's private observation appears in no hub view, not even the director's world truth.

An obstacle's `due` lists what its decided attempts brought, as 040 says (each with the 021 change ids a waiver names); whether it was applied is the turn's effect.

## Not available yet

The hub's `unavailable` list names each feature it does not offer and the work that would: revoking an accepted effect and ability use (035 offers neither), effects proposed from narration (035/047), the long-term ledger (053), the story graph (081), clocks (055), NPC agenda editing (039), and the stage when a chat has none (041). The first slice also does not yet show abilities and powers, actor goals, contested claims, source revisions or source rebase handoffs; those are later 113 slices.
