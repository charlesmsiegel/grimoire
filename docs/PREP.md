# Preparing a campaign (115)

The **Prep** page (`#/prep/<campaign id>?chat=<ChatRef>&audience=director`) is Wave 8's UI exit for three
first slices: the catalog records a campaign's chats bind (056), the ruleset action contracts (062) and one
prepared scene (064). A campaign's hub (113) and its Chronicle link to it, for the same chat and view.

The page is served by the `prep` adapter (`grimoire.app.adapters.prep`, owner 115). It works only over the
existing domain services: 056's `compose_catalogs`, the chat's own source snapshot and the runner's
`inspect_context`; 062's constructors and `propose`; and 064's `PreparationSession` and `PreparationGenerator`
over 013's provider adapter and the app's model settings (112). It keeps no store of its own and writes nothing to
the library.

## Catalogs (056)

The director's: `prep.catalogs` (`campaign`, `audience`) refuses `director_only` in the table's view.

| Answer | What it holds |
|---|---|
| `catalogs[]` | each catalog at its source's head: `catalog` (source-qualified, pinned), `name`, `problem` (056's refusal reason when it does not compose, such as `missing_catalog`), `members`, `available`, `kinds`, `excludes[]`, `depends_on[]`, `basis[]` (each composed catalog and its etag) and up to 50 `records[]` |
| `chats[]` | each of the campaign's chats (at most 12, newest first): its `bindings[]` and each NPC's context `costs[]` |
| a binding | `role`, `binding` (the ref the chat holds), `pinned`, `resolved`, `revision`, `broken`, `available_in[]` (the catalogs that make exactly that revision available, and which catalog declares it), `excluded_by[]`, and `cost[]`: the tokens each NPC's context pack takes from it now |

A campaign opts in to a catalog only through its chats' bindings, so nothing here is inferred. An exclusion hides
an entity from discovery; a chat that binds it keeps it, and the page says so. Token counts are the app's
deterministic estimate (UTF-8 bytes / 3), never a provider's count.

Changing a binding is not done on this page: each record links to its Library page, whose **Attach to a chat**
is 109's reviewed, etag-guarded write. Detaching a working binding, package distribution (103) and following a
catalog's head (079) are named as not available.

## Ruleset contracts (062)

Public: both views read `prep.rulesets` (`chat`, `audience`), which answers the four reference profiles (D20
5e-2024, WoD v5, PbtA Dungeon World 1e, Ars Magica 5e) with each field's shape, 062's documented example and its
notes. No campaign or chat pins a ruleset module (062 keeps no record of one), so `pinned` is `null` and the page
says so; a check names the profile's reference module, `reference:062/ruleset/<family>@<edition>`.

`prep.ruleset_check` (`chat`, `audience`, `family`, `actor`, `action`, `result`, optional `request_id`) checks one
proposed action and the result recorded at the table:

- the table proposes only for a character it controls now (`not_your_character` otherwise); the director for any
  cast member with a controller (`no_actor` otherwise);
- `action` and `result` take exactly the profile's fields (`invalid_field` otherwise);
- 062's own refusal keeps its words as `invalid_proposal`: a die no d20 shows, a total that does not add up, a
  partial result with no choice left open.

The answer is `{valid, canonical: false, action, result, noncanonical}`. Nothing is rolled, resolved, recorded or
applied, and no time passes; every answer and the page say so.

## Scene preparation (064)

The director's: every op refuses `director_only` in the table's view.

| Op | Args | Answer |
|---|---|---|
| `prep.preparation` | `chat`, `audience` | `{evidence, model, proposals[], spent[], saved: false, note}` |
| `prep.prepare_plan` | `request_id`, `brief` | `{plan: {request, expected, replay, blocked, call, snapshot}, evidence}`; nothing is sent |
| `prep.prepare` | the same, and `expected` (the plan's token) | `{done: {request, replay}, preparation}` |
| `prep.prepare_review` | `request_id`, `decision` accept (with `credible_stakes`, `open_agency`, `grounded`) or reject | `{done, preparation}` |

**The evidence** is assembled again on every call from what the library holds for the chat:

- *secret*: each proposition of the campaign's ledger that is not refuted and that no player character of the
  chat believes, was told or sent; named `campaign:<id>#proposition/<id>`. A ledger that cannot be read in full
  refuses `knowledge_damaged` rather than call a secret missing;
- *goal*: each goal of the agendas play runs for an NPC of the cast (the startup agendas, then the model
  settings'); named by the NPC;
- *calendar*: the campaign clock (055) and its deadlines not yet flagged. 055 keeps the clock in the campaign's
  scope record, which no 064 source type names, so an item is labelled `campaign:<id>/clock/now` or
  `campaign:<id>/clock/<deadline id>`: a label for that entry, not a stored instance;
- *choice*: the latest posts the chat's player characters made on kept turns.

Each item has an id such as `secret-1`; the cast is the chat's cast and the places are the campaign's
instances of kind `location` or `place`. A kind with nothing in it is 064's `missing`.

**The one call.** `prepare_plan` builds the exact prompt 064 would send and answers its estimate: the prompt's
token estimate, the reply cap, the attempts and, when the model has prices, the most it can cost. Its `expected`
token covers the brief, the evidence, the model settings and the estimate. `prepare` builds everything again and
refuses `changed` unless the token still matches; only then does it run 064's generator, under a policy that
allows exactly one `planning` call, through 013's adapter to the endpoint of the app's model settings, on an
untrusted handle (`app:scene-preparer`) whose every attempt is approved for its exact payload. With no model set
up (`no_model`), a key variable not set (`key_missing`), no evidence (`no_evidence`) or no place
(`no_location`), the page says so and offers no call.

- The same request id, brief and evidence again is answered from the session: nothing is sent again
  (`replay`).
- The same id with another brief or newer evidence refuses `request_conflict`; a discarded id refuses
  `rejected`.
- A reply 064 refuses (`unparseable`, `incomplete_length`, `unknown_evidence`, ...) keeps nothing, and its
  approval is spent: sending again needs the estimate again. What every call used is shown (`spent`).

**The result** shows the premise, cast, place, questions, clues, tensions, stakes and directions, each item
with the evidence ids it rests on. Accepting needs all three acknowledgements; a scene whose evidence changed
since refuses `changed`.

**Nothing is saved.** The session is in the app's memory, one per chat, until the app stops. An accepted scene is
064's in-memory `PreparedScene`: never canon, an event, a belief or a player's choice.

## The page

- The ruleset form and the brief are drafts (`ctx.draft`), kept per chat (and per family for the form) across
  navigation. Text typed while a request is on its way is kept: only the brief that was sent is cleared.
- An answer is drawn only for the choice still on screen: a check answered after the person picked another
  character or changed a field, or an estimate answered after the brief changed, is dropped and the page asks
  again (`js/prep.test.mjs`).
- Tests: `test_app_prep_catalogs.py`, `test_app_prep_rulesets.py`, `test_app_prep_preparation.py` (scripted
  transport only), `test_app_prep_page.py` with `js/prep.e2e.mjs`, and `js/prep.test.mjs`.

## Not proved here

The page runs end to end in fakedom against a real app server. It has not been used in a real browser, with a
screen reader or on a phone, and no real provider was called. The prep call is not traced by 048.
