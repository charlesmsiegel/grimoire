# Roadmap 04 S2 — Client first paint, then revalidate

> Executed by a delegated agent in an isolated worktree from this brief, then
> cherry-picked and reviewed. Plan gate by substitute review; the PR's
> `@codex review` stands in for the CLI gates.

**Goal:** 04-C2b in full (spec 04 section 6, all but 6.6, which is a decision
and not code). Frontend only; the server answers exactly as before.

## Tasks

1. `listWorlds`, `listCampaigns`, `getTodo` become remembered reads under
   `memoKey("worlds")`, `memoKey("campaigns")`, `memoKey("todo", cid)`, with
   `rememberedWorlds`, `rememberedCampaigns`, `rememberedTodo`. `MEMO_MAX`
   stays 24; root-scoped; a failed read drops its entry.
2. `writing()` forgets every remembered read and retires every in-flight GET,
   before the write and when it settles (no path condition).
3. The epoch moves to a leaf, `api/memoEpoch.ts`, so a page under a wholesale
   mock of `api/client` can still ask it. `issuedEpoch` and `memoEpoch` are
   exported; every GET and every remembered read's returned promise is
   stamped. `noteRunSettled()` forgets and retires; it is called from
   `draftRun`'s completion, the stream helpers' terminal frames (`streamPost`
   and the re-attach), and the run registry through a `runEnded` channel in
   `appEvents.ts` (a direct import would break suites that mock the client;
   the effect is the spec's).
4. `api/useRevalidated.ts`: keyed state, sequenced reads, `reload()` as a
   superseding fresh read, an answer older than the memo epoch discarded and
   re-read fresh, a failure dropping the payload. `reload()` returns
   `Promise<boolean>` (false only when its own newest read failed) so the
   fork flow can keep its "created, but the list could not be refreshed"
   message.
5. WorldsView, CampaignsView (two hooks), TodoView (keyed on `cid`): `aria-busy`
   on main while stale (`PageShell`/`LibraryPage` gain `busy`); rename, fork,
   delete, Ignore and Restore disabled while stale; empty states only after a
   successful read; failure banners for Worlds and Campaigns; New campaign
   held until the worlds read succeeded; every post-mutation refresh through
   `reload()`. `getChoreItems` stays fresh and unremembered.
6. `docs/store-guarantees.md` gains the 6.5 bound; TodoView's docstring the
   4.4 wording.

## Tests

`api/remembered.test.ts` (12.5 list, including runEnded and the re-attach
terminal frame), `api/useRevalidated.test.ts` (overtaking, joined pre-write
read, key change, failure, reload), and the three view suites' 12.5 cases with
stale frames asserted synchronously after `render`. Typecheck, the full vitest
suite, the eslint ratchet (two findings resolved, baseline lowered) and the
docs guard pass.
