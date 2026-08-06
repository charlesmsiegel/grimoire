# Live rolling per-scene summary — design

**Date:** 2026-08-06
**Status:** approved, pre-implementation
**Issue:** #85 (milestone: Scenes & Play)
**Branch:** `claude/live-rolling-per-scene-summary-4ju8mx`

## 1. The problem

Every summary grimoire holds is an *end-of-scene* artifact. `POST
…/scenes/{sid}/absorb` runs one extraction and `PUT …/chronicle` writes
`one_line`/`summary` into the scene's frontmatter (`scenes.mark_absorbed`) and
into `chronicle.json` (`chronicle.absorb`). Those feed the story-so-far recap of
*later* scenes (`context/story.py` → `chronicle.recent`).

Nothing summarizes the scene being played. A long scene is a wall of transcript
with no reading aid, and the only way to get a summary of it is to end it.

There is no background-job machinery in `backend/` — no `BackgroundTasks`, no
`asyncio.create_task`, no queue. Every LLM call is request-scoped: an SSE stream
(`routes/streaming.py`) or an awaited `client.complete()`. "Non-blocking" is
therefore a thing to design, not a thing to wire up.

## 2. Decisions

Taken from the issue's own recommendations except where noted.

1. **Option A — client-triggered refresh endpoint.** No new server lifecycle,
   no unmanaged task whose exception vanishes, and the whole policy is
   reachable from `TestClient`. The cost is that the summary only refreshes
   while a client is open, which is fine: play is interactive by definition.
2. **Display-only.** The rolling summary is *not* injected into the scene
   context. Per-campaign recap injection already exists; adding a second
   summarization channel into the prompt is a separate, larger decision about
   prompt budget and about a model reading its own summary of itself.
3. **`rolling_summary_every` in `config.md`**, default `10`, `0` = off — beside
   `recap_depth` / `archive_depth` / `absorb_budget`.
4. **The refresh gate lives on the server, not the client** (a deviation from
   the issue's sketch, which put `messages.length - rolling_at >= N` in
   `CampaignView`). One source of truth for the policy, and it is then
   exercised by pytest rather than only by vitest. The client fires the POST
   after every turn and the route decides whether to spend a call.
5. **Failures never touch the turn loop.** The route returns ordinary status
   codes (409 no connection, 502 upstream); the client fires the request
   without awaiting it and swallows every rejection.

## 3. What the issue got wrong, and what it costs

> "`mark_absorbed` already stores multi-line `summary` in frontmatter, so the
> format is proven"

It is not. `store/frontmatter.py` is a **one-line-per-key** format:
`dump_frontmatter` writes `f"{key}: {_quote(value)}"`, and `_quote` wraps a
value in single quotes without escaping newlines. A value containing `\n` is
written across two physical lines, and `parse_frontmatter` then reads the first
line back with an unbalanced quote and treats the second as either a junk key
(if it contains a colon) or a dropped line (if it does not) — and a
continuation line beginning `---` terminates the block early, silently
truncating the frontmatter.

So a multi-line rolling summary would corrupt the scene file. The rolling
summary is therefore **normalized to a single line** in two places, for two
different reasons:

- `rolling_summary.parse_output` collapses whitespace runs, because the parse
  contract is "one paragraph" and a model that answers in three paragraphs
  should still produce a usable value;
- `scenes.set_rolling_summary` collapses again, because "frontmatter values are
  single-line" is the *store's* invariant and must not depend on which parser
  fed it.

This is a latent bug in `mark_absorbed` too — see §8.

## 4. Storage

Three scene-frontmatter keys, written together:

| key | meaning |
| --- | --- |
| `rolling_summary` | the summary prose, single line |
| `rolling_at` | how many messages it covers (an index into the message list) |
| `rolling_digest` | sha256 over the covered prefix |

`rolling_at` alone is not enough, and this is the design's load-bearing point.
An incremental fold assumes the prefix it already folded is still there. It is
not: a reroll replaces the trailing reply, `edit_message` rewrites one in place,
`trim_continuation` and `remove_trailing_*` shorten the transcript. All four can
leave the transcript the same length while changing what it says, so a
length-only check cannot see them — and the fold would then carry prose about
content the player deleted, for the rest of the scene, with no way back.

`rolling_digest` closes that: on refresh the route re-digests the current
`messages[:at]` and compares. A mismatch means the covered prefix moved, so the
prior summary is discarded and the scene is folded from scratch.

The same digest is also the **write's own precondition**, checked under one
campaign hold in `_rolling_commit`: the fold lands only if `messages[:covered]`
is still what it was when the prompt was built. An earlier draft argued this was
unnecessary because a bad write is self-correcting on the next refresh; Codex
review found two cases where it is not, and both are answered by the same check:

- The panel's *Refresh now* renders this route's answer **directly**, so a
  summary stored over a transcript that changed mid-call would be presented as
  current until some later GET happened to notice.
- `delete_scene` frees a scene's id and the numbering reuses it, so a scene
  deleted and remade under the same title during the call hands the write the
  very id it holds — attaching one scene's prose to another. On an *empty*
  replacement not even *Refresh now* clears it, because there is nothing pending
  for a forced refold to fold.

An ordinary turn **appending** during the call leaves the covered prefix
untouched and so still lands, which it must — otherwise every busy scene would
throw away the summary it just paid for. The route then returns the *reconciled*
view read back under that hold, never the pre-call snapshot.

Holding the campaign lock across the LLM call itself would close the window
earlier and is refused — it would block every other write in the campaign for
the duration of a network round trip.

## 5. Surface

### `GET /campaigns/{cid}/scenes/{sid}/rolling-summary`

Never calls the LLM. Returns:

```json
{"summary": "…", "at": 12, "total": 14, "stale": false, "every": 10, "due": false}
```

`stale` is the digest mismatch — the panel says so rather than presenting prose
about a rerolled turn as current. `due` always answers the *automatic*
question ("would a plain per-turn POST spend a call"), never the forced one.

A stale summary is still **returned**, and the distinction matters: what a fold
may build *on* (`""` once the digest breaks) is not the same as what a reader
should be *shown* (the stored prose, flagged). Collapsing the two makes the
panel report a scene that has a summary as having none, and takes the staleness
warning — which renders beside that prose — with it.

### `POST /campaigns/{cid}/scenes/{sid}/rolling-summary?force=true`

Same body plus `"refreshed": bool`. When not due it returns immediately having
spent nothing (`refreshed: false`); this is the ordinary per-turn case. `force`
bypasses the gate for the panel's *Refresh now* button, which is also what makes
the feature reachable at all when `rolling_summary_every` is `0`.

Guards, in order: `_require_scene` (404) → `_require_connection` (409) →
due-check → one `client.complete()` (502 on `LLMError`) → write.

Note the connection check runs *before* the due-check on purpose: a due-check
that passed and then 409'd would be indistinguishable, from the client, from one
that quietly no-opped.

## 6. Prompt

`templates/rolling_summary/{system,user}.j2`, mirrored by
`rolling_summary.build_prompt(prior, transcript)` and covered by
`scripts/verify_templates.py` like every other call.

`user.j2` takes `prior` (the previous summary, `""` on a from-scratch fold) and
`transcript` (`snippets/transcript.j2` over *the new posts only* when folding,
over the whole scene when not). The system prompt asks for one paragraph, plain
prose, present tense, no headings — a reading aid for a scene still in progress,
which is why it is present tense where `absorb/system.j2` is past.

## 7. Frontend

- `SceneInspector` grows a **Scene so far** section: the summary, a coverage
  hint (`covered 12 of 14 posts`), a staleness note, and a *Refresh now* button
  that POSTs with `force`. It reads through `api.getRollingSummary` on its
  existing `refreshKey`, the same way every other section of that panel reloads.
- `CampaignView.runStream`'s `finally` fires `api.refreshRollingSummary` **not
  awaited** — the player's next turn must never wait on a summary — and bumps
  `ctxKey` when it resolves, but only while the reader is still on that scene.
  Every rejection is swallowed.

## 8. Adjacent problems, deliberately not fixed

- `scenes.mark_absorbed` writes the absorb summary into frontmatter with no
  single-line normalization, so a model that answers `"summary"` in two
  paragraphs corrupts the scene's frontmatter (§3). Real, pre-existing, and a
  different change: fixing it means deciding what happens to scene files already
  written that way.
- The rolling summary is not part of `context`. See §2.2.
- #84 (scene-break detection) can reuse this "every N posts" gate; #94's
  paginated transcript is what makes the panel worth having. Neither is in
  scope here.
