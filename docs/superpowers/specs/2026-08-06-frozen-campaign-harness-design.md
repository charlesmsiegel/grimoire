# Frozen-campaign snapshot harness + shared LLM gateway fakes — design

**Date:** 2026-08-06
**Status:** implemented
**Issues:** #205 (frozen-campaign snapshot regression harness), #204 (mock LLM
gateway + record/replay "golden" fixtures)
**Branch:** `claude/frozen-campaign-snapshot-harness-565s5q`

## 1. The problem

Two gaps in the test suite, filed separately, that turn out to share one seam.

**#205 — nothing tests reading old data.** All 111 backend test modules build
their store *with the code under test*, seconds before reading it. That
arrangement is structurally incapable of catching a change that breaks reading
data an *older* version wrote: the setup would simply write the new shape too,
and the suite would stay green while every existing library broke. The one
migration that exists — `migrations.migrate_scene_ids()`, which renames legacy
real-date scene files into the `<number>--<date>--<slug>` grammar and repoints
every persisted reference — is tested only against scenes hand-written by the
test itself.

**#204 — every LLM fake is hand-rolled per call site.** `test_routes.py` carried
six near-identical fakes (`FakeOpenRouter`, `FakeOpenRouterComplete`,
`CapturingOpenRouter`, `FailingOpenRouter`, `StallingOpenRouter`,
`QuietThenAnswers`, plus a seventh, `FakeCompleter`, that duplicated the
second), used across ~60 call sites, with no shared module. Reply bodies were
inline string literals. The `verify` skill grew an *eighth*, an SSE mock
branching on whether the system prompt mentions suggestions — the same idea, in
a throwaway launcher, sharing nothing.

## 2. Re-scoping #204

The issue was filed pre-rebuild and says "there is no gateway abstraction to
mock — only this one client to fake". That is no longer true: `llm.LLMClient`
(#141) now dispatches across OpenRouter, Claude and OpenAI-compatible providers,
and `routes.common.get_llm()` is the DI seam every route takes. So the mock
target is the gateway, not a provider client, and it is one object with two
methods (`stream`, `complete`).

Of the issue's three options, **A** (shared fake + hand-authored fixture bodies)
is implemented, with one piece of **C** folded in: the `verify` skill's
branch-on-the-prompt idea, promoted from a throwaway `if "suggestions" in
prompt` into a **cassette** — a fixture file of `{when: <predicates>, reply}`
entries matched against the request.

**Option B (record/replay against the live API) is deliberately not built.** Its
own honest caveat is fatal: a recorded cassette is keyed on the exact request
shape seen at record time, so any prompt-template edit invalidates it and
requires a re-record — a real API call, real spend, and a re-record nobody will
do, leaving a cassette that silently no longer corresponds to anything. What
that option was reaching for — "the fixture still matches what the code sends" —
is bought here without a network call, by rendering the real templates in a test
and asserting each cassette matcher is still a phrase they contain (§4.2).

## 3. #205: the frozen campaign

### 3.1 What is frozen

`backend/tests/fixtures/frozen_campaign/home/` — a complete store tree: one
world (two characters, one with two versions; a location; keyed lore; two
greetings joined by a plotmap edge; a tag vocabulary), one campaign (a PC,
PC-owned lore, the `d20-basic` module bound with a filled sheet, a dossier, a
chronicle with a timeline, an open and a closed plot thread, an open commitment,
a feeling and a bond), and two scenes. All names are invented placeholders
already used as fixtures in this codebase.

The second scene is stored under the **pre-migration real-date filename**, and
is referenced by the chronicle, a plot beat and an appearance — so the migration
has both a rename and a repoint sweep to perform, and the harness covers the
half that is easy to break and invisible until a campaign loses its chronicle.

`build.py` records how the tree was minted (its clock is faked by replacing
`store.paths`' `datetime`, not `paths.now_iso`, which a dozen modules bind by
value at import). It is provenance and a way to mint a *new* fixture at a newer
format beside this one — never a way to refresh this one in place.

### 3.2 Two frozen artifacts, two rules

| Artifact | Rule | Why |
| --- | --- | --- |
| `home/` | **never regenerated** | its entire value is being old — rebuilt with today's code it tests nothing |
| `snapshot.json` | **regenerated deliberately** | it is expected *output*, and legitimately moves when a template or render changes on purpose |

This is the same doctrine `store_api_baseline.json` and
`fixtures/weather_vectors.json` already carry, and the regeneration path is a
module you run (`python -m tests.fixtures.frozen_campaign.sweep`), not a pytest
flag — a flag is one keystroke from "make the red go away".

### 3.3 Why a snapshot is not enough on its own

A snapshot compare is only as good as the discipline around regenerating it. So
the harness has three independent kinds of assertion, and the snapshot is only
one of them:

- **the snapshot compare** — broad and mechanical; catches drift anywhere.
- **semantic assertions** — hand-written, not derived from the snapshot: the
  assembled prompt still carries the NPC card, the PC persona, relationships,
  the chronicle, plot threads, commitments, the location, world lore, the sheet
  and the check roster; and PC-owned lore appears in the scene its owner is in
  and *not* in the other one. Regenerate the snapshot from a broken build and
  these still fail.
- **tree digests** — the sweep must write nothing (a read that writes is how a
  "just look at it" endpoint rewrites a transcript), and migrating twice must
  change nothing. Neither property is visible in the sweep's output at all.

Plus a fourth, cheap one: the sweep must still touch every store module it
claims to (`test_the_sweep_covers_the_whole_store_not_a_corner_of_it`), so a
sweep that quietly stopped calling things cannot match a snapshot regenerated
from itself.

### 3.4 What the snapshot deliberately excludes

- **Token counts.** `count_tokens` falls back to a characters/4 heuristic
  without tiktoken, and tiktoken is a `desktop` extra — so `make check-pydantic1`
  (the Android dependency set) would disagree with `make check-py` on every
  count. Section *composition* is snapshotted; the numbers are not.
- **Anything calling an LLM.** The sweep is read-only and offline by
  construction; the generating half is covered by the route-driven tests in
  §4.3 with the cassette.
- **Weather.** The fixture has none, and `weather_vectors.json` already freezes
  the generator.

Two couplings are kept rather than hidden, both documented in `sweep.py`:

- the dated scene's prompt contains a `# Today` section naming US federal
  holidays from the `holidays` package, so a release renaming one moves the
  snapshot. The suite already carries that exposure (`test_calendars.py`,
  `test_context.py`), and a snapshot diff touching only `# Today` lines is that,
  not a store regression.
- the campaign binds the **built-in** `d20-basic` pack, which ships in
  `grimoire.store.builtin_modules` rather than in the fixture, so editing that
  pack moves the sheet, rules and check-roster sections. That is the correct
  signal: a builtin pack edit really does change every campaign bound to it, and
  this is the only place it shows up as a reviewable diff.

## 4. #204: the shared fakes

### 4.1 One implementation

`backend/tests/llm_fakes.py` holds `FakeLLM` — turns of deltas consumed in call
order (last turn repeats), every request recorded, optional error or stall
injection — and the named shapes tests ask for as thin subclasses. The names and
behaviour `test_routes.py` used are preserved exactly, so its ~60 call sites are
unchanged; the seven local class definitions are deleted and imported instead,
and `FakeCompleter` (a duplicate of `FakeOpenRouterComplete` plus capture, which
`FakeLLM` gives every fake) collapses into it.

### 4.2 Cassettes, and the link that keeps them honest

A cassette answers by *what the request looks like* rather than by call order —
right for a flow like absorb, whose call sequence is an implementation detail.
`fixtures/llm/campaign_flow.json` covers every prompt the app can send (scene
turn, absorb, audit, dossier, voice anchor, voice drift, suggestions, tagline),
with bodies written against the frozen campaign's ids.

An unmatched request raises `CassetteMiss` naming what was tried. **There is no
default reply**, deliberately: a fake that answers everything keeps a test green
after the code stopped calling what the test thought it called.

The failure mode a fixture set like this normally dies of is a reworded system
prompt leaving every matcher quietly dead. So `test_llm_fakes.py` renders each
real template and asserts, in both directions, that every matcher is still a
phrase some prompt contains and every prompt is matched by some entry. That is
the guarantee Option B wanted from re-recording, at zero cost and no spend.

### 4.3 Where the two issues meet

`test_frozen_campaign.py` ends by driving the app's real routes over the frozen
store with the cassette at `routes.get_llm`: a chat turn streams, persists, and
its request carries the frozen campaign's own state; an absorb pass reads the
frozen chronicle and plot and stages movements against the frozen ids without
writing them. That is the whole point of having both — a campaign this process
did not create, played through the real stack, with no provider.

## 5. Scope notes

- **#203's markers are not added here.** That issue names the marker strings
  (`frozen_campaign`, `perf`, …) and asks that they be chosen once; adding
  `pytestmark` from this branch would fix a name outside the issue that owns it.
  The harness runs in the default suite meanwhile, which is where it is most
  useful anyway.
- **No `GRIMOIRE_RECORD` mode.** See §2.
- The pre-existing failure of `test_atomic.py::test_a_read_only_record_is_not_silently_replaced`
  under a uid-0 test runner (root ignores the read-only bit, so no
  `PermissionError` is raised) is unrelated to this branch and untouched by it.
