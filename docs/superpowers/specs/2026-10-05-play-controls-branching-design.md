# Play controls III — branching on one fork primitive

Step 3 of the SillyTavern-parity play controls (programme table in
`2026-10-05-play-controls-swipes-design.md`). The owner chose the **hybrid**
absorb policy at the start and asked for the rest of the programme to run
without stopping; decisions below that a brainstorm would have put to them are
recorded with their reason.

## The request

> Branching forks a scene at a post into a sibling scene file. Plain files make
> this cheap. The hard part is absorb … decide that first. #151 (replay with
> fork and seed) is the same mechanism from the debugging side; build both on
> one fork primitive.

## The absorb decision (made at the start, restated)

- **Unabsorbed scene** — branching makes a **sibling scene** in the same
  campaign. Siblings form a **branch group**. Absorbing any one member
  **closes** the group: every other member turns read-only and cannot be
  absorbed. Un-absorbing that member (a cut, a revert) reopens them.
- **Absorbed scene** — absorbed state lives in campaign files (facts, plot,
  dossiers, state, relationships), and only a copy of the campaign can hold two
  pasts. Branching an absorbed scene is a **campaign fork cut at the post**:
  the existing `store/fork.py` copy, extended from scene granularity to post
  granularity. No campaign-file snapshot machinery is built.

## The primitive

`store/branch.py`:

```python
def branch_scene(cid: str, sid: str, through: int, *, title: str = "") -> str
```

Creates a sibling of `sid` holding `messages[: through + 1]` — the post the
player branched **from** is the last post the branch keeps — and returns the
new scene id. Refused (`BranchRefused(kind)`) when the source is absorbed
(`absorbed_use_fork`), closed (`branch_closed`), or `through` is out of range.

It is the one primitive both callers below build on: "Branch from here" in the
play view, and replay (#151), which branches the whole transcript and replays
inside the copy.

### What the sibling is

A scene is a markdown file plus records keyed three ways (by file name, by
identity token, by scene id inside campaign files). The sibling gets:

| Piece | Sibling gets |
|---|---|
| Transcript | `messages[: through + 1]`, written through the ordinary serializer, with `turn_sizes` clamped and `location_history` / `time_history` rewound exactly as a cut at `through + 1` would (the sibling is built by copying then cutting with `scenes.write.delete_from`, so the existing rewind is reused rather than reimplemented) |
| Id | the source's **number** with a uniquified title slug (`005--…--title-b`), so it sorts directly after its source in play order rather than after every later scene. `repad` already tolerates duplicate numbers |
| Title | `"<source title> (branch)"`, uniquified with ` 2`, ` 3`… unless the caller passes one |
| Identity | a **fresh** token (two files sharing one make the reverse lookup ambiguous) |
| Frontmatter | `model`, date fields, `pcless` copied. Never `done`, `one_line`, `summary`, `greeting`, `dismissed`, rolling-summary or scene-break keys — those are recomputed (they are digest-validated anyway) |
| Branch keys | `branch_group` and `branch_of` (see below) |
| Response ledger | every record whose messages survive the cut is **cloned** under the new identity with a **new response id**; its prompt snapshots are copied under the new id; the copied transcript's `response_id` metadata is rewritten to match. Without new ids, deleting one sibling would delete the other's prompt snapshots (`responses.drop_scene` removes every snapshot a record references). Rounds referenced by cloned records are cloned, any unfinished one marked `superseded` |
| Appearances | the new sid joins each cast actor's `scenes`, and the source's `presence` intervals are copied (then remapped by the cut) |
| Rolls | each `rolls.json` entry for the source that produced a roll line the sibling keeps is copied with `scene` = new sid and a fresh id, matched by the line its entry formats to, consumed in order. An entry whose line the branch does not keep is not copied |
| Steering log | copied (absorb hints are the player's intent up to that point) |
| Audit baseline | the source's baseline, copied — the sibling starts from the sheets the source started from, not from "now" |
| Tracker | scene-level field definitions copied; per-post records are not (the sibling re-tracks on its next turn) |
| Not copied | prompt log, attempts, pins, commits, pending reviews, proposals, legacy alternates, the source's pending replay |

The whole build happens under the campaign lock with `runs.require_scene_free`
for the source (a live turn would append to a transcript mid-copy) and the
`create_would_repad` → `require_campaign_free` check scene creation already
does. A failure part-way deletes the half-built sibling through
`scenes.lifecycle.delete_scene`, as `scene_import` does.

### The branch group

Two frontmatter keys, flat strings like every other scene key:

- `branch_group` — a group id: the source's existing `branch_group`, or (on the
  first branch) the source's identity token, written onto the source too.
- `branch_of` — the source's identity token (identity, not sid, because sids
  move on rename and the first date stamp).

`scenes.read` exposes both on the scene row and the scene payload, plus
`branch_closed` (below). Groups are discovered by scanning frontmatter
(`list_scenes` already reads every scene's frontmatter); no new campaign file.

### Closing and reopening

- **Close.** The chronicle commit (`PUT .../chronicle`), inside the campaign
  lock hold that runs `mark_absorbed`, writes `branch_closed: <absorbed
  identity>` onto every other member of the absorbed scene's group.
- **Reopen.** `scenes.write.unmark_absorbed` (reached by `cascade.delete_from`
  and `revert_scene`) clears `branch_closed` from every member whose value names
  the scene being un-absorbed.
- **A closed scene is read-only.** One guard, `runs.require_scene_open` (in
  `runs.require_scene_free` and `runs.reserve_turn`, so it reaches every route
  that already refuses `scene_busy`), answers 409 `branch_closed`. Absorb
  (`POST .../absorb`, `PUT .../chronicle`) refuses a closed scene the same way.
  Reading, exporting, deleting and renaming stay allowed — deleting a closed
  sibling is how a player discards a road not taken.

### Branching an absorbed scene

`fork_campaign(..., from_scene, from_index=None)` gains `from_index`. After the
existing `_cut_after(new_cid, from_scene)`, it runs
`cascade.delete_from(new_cid, from_scene, from_index + 1)` on the copy when
`from_index` is not the scene's last post, then the tracker's after-cut prune.
`cascade.delete_from` already reverses what the cut posts' absorb wrote and
un-absorbs the scene in the copy. The report gains `cut_at` (the index kept
through). `ForkCampaign` gains `from_index: int | None`. The source campaign is
never written (unchanged guarantee).

## Routes

- `POST /campaigns/{cid}/scenes/{sid}/branch` body `{through: int, title?: str}`
  → `{id: <new sid>, scene: <payload>}`. 409 `absorbed_use_fork`,
  `branch_closed`, `scene_busy`, `run_in_flight` (repad); 400 for a bad index.
  It is a write to the campaign, so the revision middleware stamps it.
- `POST /campaigns/{cid}/fork` accepts `from_index`.

## Replay on the primitive (#151)

Replay today cuts the scene **in place** and keeps the cut posts only in
`replay.json`. On the primitive:

- `POST .../replay` gains `branch: bool` (default **true** for an unabsorbed
  scene). With it, the server branches the whole transcript
  (`through = len - 1`), begins the replay **inside the sibling** at the same
  index, and answers with the sibling's id; the client navigates there. The
  original scene is never touched, so a replay that goes wrong costs nothing:
  delete the sibling, or keep both and absorb the better one (which closes the
  other).
- `branch: false` keeps today's in-place replay (and is the only mode for an
  absorbed scene, whose branch is a campaign fork — the existing "Fork first"
  path, now able to fork **at the post** being replayed).
- **Seed** (the other half of #151) is out of scope: no adapter sends one, and
  OpenRouter cannot guarantee a deterministic replay across upstream providers.
  The issue's "fork" half is what this delivers.

## Play view

- **"Branch from here"** (`⑂`) in the post gutter, beside ⏩, on every post of
  an active scene. On an unabsorbed scene it calls the branch route and
  navigates to the sibling. On an absorbed scene it opens the existing fork
  dialog (name prompt) with `from_scene` and `from_index` filled, and explains
  in the confirm text that an absorbed scene branches into a copy of the
  campaign.
- **Scene list** (`ScenesView`, the play view's scene head): a `branch` chip on
  members of a group with more than one member, and a `closed` chip on closed
  members (title "A sibling branch was absorbed").
- **A closed scene** renders like an absorbed one — no composer, no per-post
  actions — with a banner naming the absorbed sibling and a link to it.
- **Replay dialog**: a "Replay in a branch (keeps this scene)" option, on by
  default for an unabsorbed scene.

## Non-goals

- Merging branches.
- Branching across campaigns other than by the fork above.
- A branch-tree visualisation beyond the chips.
- Seeded replay.

## Testing

Backend (store and routes):
- branching at `through` keeps exactly `messages[:through+1]`, rewinds
  location/time history, clamps `turn_sizes`, and sorts directly after the
  source;
- the sibling has a fresh identity, `branch_of` = source identity, both share
  `branch_group`; a second branch of the source joins the same group; a branch
  of the branch joins it too;
- response records are cloned with new ids and their snapshots copied;
  deleting the source leaves the sibling's records and snapshots intact, and
  rerolling a response in the sibling works;
- appearances membership and presence exist for the sibling; kept roll lines
  carry matching `rolls.json` entries and the audit's roll lines for the sibling
  list exactly them;
- branching an absorbed scene is refused `absorbed_use_fork`; branching a
  closed scene is refused `branch_closed`; branching while a turn holds the
  source is refused `scene_busy`;
- absorbing one member closes the others; chat, edit, cut, roll and absorb on a
  closed member answer 409 `branch_closed`; deleting and renaming it work;
  cutting into the absorbed member (un-absorb) reopens the others;
- fork with `from_index` keeps the scene through that post in the copy,
  reverses the cut posts' absorb writes there, and leaves the source campaign
  byte-identical;
- replay with `branch: true` leaves the original transcript byte-identical and
  replays in the sibling; `branch: false` behaves as today.

Frontend:
- `⑂` on an unabsorbed scene calls the branch route and navigates to the new
  sid; on an absorbed scene it opens the fork dialog with `from_index`;
- branch and closed chips render; a closed scene shows the banner and no
  composer;
- the replay dialog's branch option sends `branch: true` and navigates.
