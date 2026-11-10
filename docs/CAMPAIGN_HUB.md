# Campaign hub API (113)

**Status: served, with its page.** `grimoire.campaignapi.CampaignApi` is the typed, authority-aware adapter behind the campaign hub. The app serves it as the `campaign.*` adapter ops of 108's [shell contract](APP_SHELL.md) (`grimoire.app.adapters.campaign`), and the **Campaigns** page (`static/pages/campaign/`, route `campaign`) is its screen. In the app the hub acts as the library owner, with the app's own trusted handle. No other handle is accepted: `CampaignApi` refuses a handle that is not the owner's, or not trusted.

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
| `recover_effects` | Settle (or `abort`) acceptances of this chat that were cut short; where 035 needs the player present (an abort of an effect they endorsed, or a completion whose endorsement the authority no longer records), `player_key: true` is the player's own key, given knowingly | 035 runner, 030 |
| `chats` (app only) | The library's recent chats, grouped by campaign; with `campaign`, that campaign's alone (read only) | none |

Every op takes `chat` (a `ChatRef` string) and an optional `audience`, checked before anything is written. Write ops return `{"done": ..., "hub": ...}`, the hub as it stands after the write, so a screen never shows a stale situation beside a fresh result. A refusal is an `ApiRefused` with the owning service's own reason (`fingerprint_mismatch`, `already_accepted`, `unknown_effect`, `not_at_table`, `changed` for a stale write, ...) and writes nothing unless that service's refusal says so (035 records a labelling the model refused, so the table sees why). A hub that cannot be read after a committed write comes back as `hub: null` with its problem, never as a refusal of the write.

## The page

- `#/campaign` lists every campaign with a chat in the library, and `#/campaign/<campaign id>` lists that campaign's chats.
- `#/campaign/<campaign id>?chat=<ChatRef>` is the hub for one chat. It opens in the table's view; `&audience=director` gives the director's.
- Each view shows what the hub returns for that audience, and nothing more.
- Every write is one `campaign.*` call. The page then shows the hub that the write returned.
- The page offers an action only where the hub's `actions` says it is available. Anything else is named under "Not available yet", with what it waits on.

Who acts:

- **The table** confirms a labelling and consents to a setback, naming the frame's fingerprint, after a confirmation that says what it decides. It also endorses an effect exactly as shown.
- **Either view** asks for a labelling where the app has a labeller. The app passes the hub none yet, so labelling there is named as unavailable.
- **The director** decides, and proposes an effect. A proposal names a kept turn (read from the core `open` op) or the open action's turn once it is decided and is written in structured form: 021 operations, plus 023 `add` and `consume` lines. The director then approves it. When the director's key is the last one, the confirmation says the effect becomes world truth.
- A cut-short acceptance of this chat (035's `effect_unsettled`, `effect_untagged` or `effect_elsewhere`) is shown to both views. Only the director's view settles or undoes it. The player's own key is a separate box they tick, and so is the owner's word that an untagged one was accepted in this library (`own`). One that another device accepted is left to that device. The page then says how many effects were settled.
- A proposal being built survives other writes, and is cleared once it is made.
- Each effect is shown in full before either key is given: its operations with who acts, and every holdings line with its counts, its source and destination (a `move`'s `target`, an `adjust`'s `seen` and `to`), who acts, and the holdings note.
- An action confirmed or decided here is narrated in the chat: the chat's own runner (044) reads its frame with the same adjudication key.
- A character pinned behind its source links to the import and review screen's source-update tab for the chat (110, `#/import?tab=rebase&chat=<ChatRef>`) when that page is in the app, and is otherwise named as unavailable.
- A perspective is opened per actor and labelled as theirs.

Not on the page yet: moving or adjusting holdings, and waiving what an obstacle made due. The API takes both.

## Who acts, and with which key

In this slice the library owner is both the table's one player and its director (one person, one library, as the [text API](TEXT_API.md)); only the owner can construct a `CampaignApi`. The hub never gives one key for the other: endorsing an effect and approving it are two separate calls, even when one person makes both (021's solo-play rule). A consent is the acting player's own `decide` naming the fingerprint of the frame they were shown; a consent for another frame or an earlier revision is refused. The hub's `actions` say which ops are available now and why not (no labeller, or play is paused). Labelling is a model call: it is available only when the app passes a labeller, its untrusted handle and an inference policy allowing `labelling`; otherwise `label` is refused (`no_labeller`) and the hub lists it as unavailable with that reason. Nothing is proposed from narration: an effect is always the director's structured proposal.

## Who sees what

`audience` is `table` (the default) or `director`.

- **The table** sees the cast (each actor's controller and role, whether on stage, any staged absence), the persona, the stage, who is present and its own characters' party standing (a character set in reserve stays its player's), its own characters' holdings, items, powers and source pins, the obstacles (040 keeps no hidden facts), each turn's labellings and the effects it holds a key on (never the transcript, which is the chat page's), the open action with its frame and outcome, and its own characters' knowledge (with what the director wrote while a character was an NPC set apart as `inherited`).
- **The director** also sees `truth`: 021's accepted events (and revoked ones), the cast's current 017 relations and 012's `claims` (below); every effect, including those no player character acts in; every party membership and 021's problems; every actor's holdings, items and powers; every binding of the chat; the app's 039 agendas (`goals`) and each turn's recorded `intentions`; and any one actor's perspective by asking `knowledge` for it with `audience: director`.

An actor's perspective is that actor's observations, sent reports and beliefs, each labelled with its `kind` and `holder`. It is never world truth and is never folded into the table's: another character's perspective asked at the table is refused (`director_only`), and an NPC's private observation appears in no hub view, not even the director's world truth.

## Powers, goals, claims and sources

- **`abilities`**: each shown actor's (a character in reserve included) effective 025 `abilities` facet, from the chat's 022 snapshot: every power with its effects (tags, range, certainty), limits (excluded tags), costs and prerequisites. An NPC's powers are the director's; the table sees its own characters'. No facet and an empty one both read as no powers. An actor whose definition cannot be composed now is left out with an `abilities_unreadable` problem. Using a power in play is not offered (`ability_use`, 035).
- **`goals`** (director only): the 039 agendas the app passes as `agendas` (the same values 035's runner takes, checked by it), for this chat's cast: goals, approaches with their needs and tags, limits with their conditions. `usable` is false, with a reason, where play would not use the agenda now: `not_an_npc` (a player character's intentions are its player's), `not_on_stage`, `no_voice` (the chat reports the NPC has no voice), 039's `unknown_proposition` or `unknown_actor` against the campaign's ledger and instances, or `ledger_damaged` when the ledger cannot be read in full. The hub edits no agenda (`npc_autonomy`, `goal_progress`).
- **`intentions`** (director only, on each turn): 035's record of what each director NPC pursued and refused in each take and why (`explanation` is 039's director-side text). It is an account of a choice, never an agenda. The table's turns carry no `intentions` key.
- **`truth.claims`** (director only): every proposition of 020's stored ledger with canon's `standing` (012: `established`, `refuted`, `unknown`, `reported` or `unasserted`) and the `ruling` that governs it, its 017 claim `shape` if it has one, and `held`: each cast actor's current belief of it, as `{kind: belief, holder, stance}`. A stance is that actor's belief and never the standing. `contested` is true when one holder rejects what another believes or suspects, or a stance goes against canon (believed or suspected while refuted, rejected while established). A ledger with quarantined records or problems is reported as `knowledge_damaged`, and `truth.claims_complete` is then false: a lost ruling would show its proposition as merely `reported`.
- **`sources`**: the chat's active bindings (for the table, only its own characters'): role, the bound ref and whether it is `pinned`, the revision it `resolved` to (or why it is `broken`), the source's current `head` (or the store's `problem` finding it), the actor and 022's notices for it (`instance_source_drift`, `actor_rebound`). A pinned actor binding behind its head is `behind` and carries a `handoff`, `{api: review, op: rebase.review, args: {actor}}`: the review screen's 042 rebase review ([REVIEW_UI](REVIEW_UI.md)). The hub rebases nothing.

An obstacle's `due` lists what its decided attempts brought, as 040 says (each with the 021 change ids a waiver names); whether it was applied is the turn's effect.

## Not available yet

The hub's `unavailable` list names each feature it does not offer and the work that would: revoking an accepted effect and ability use (035 offers neither), effects proposed from narration (035/047), the long-term ledger (053), the story graph (081), clocks (055), NPC agenda editing (039) and stored goal progress (039's next slice), rebasing a pinned scenario or lorebook (042), stored change-set proposals (043 milestone 3: a change set is reviewed from its document in the review screen), and the stage when a chat has none (041). Item powers (034) and beliefs of actors outside the chat's cast are not shown yet.
