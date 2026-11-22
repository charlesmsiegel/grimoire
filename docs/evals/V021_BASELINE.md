# V0.2.1 cost and voice baseline (Sprint 015, first slice)

**Status:** measured baseline captured on 2026-11-20 (third live attempt), with
`claude-sonnet-4-5-20250929` on the `anthropic` provider:
[results](#measured-baseline-2026-11-20). Its token counts are provider-reported and its
evidence verifies. The provider reports no cost, so **no cost in money and no cost parity with
V0.2.1 is claimed**. Dry-run numbers elsewhere stay synthetic.

## What is measured

The synthetic scenario `guild_betrayal` (`backend/src/grimoire/evals/benchmarks/guild_betrayal/`)
is played through the real `0.2.1` backend at commit `e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce`
(the `0.2.1` tag; the commit, not the movable tag, is pinned). Four NPCs are present and two
speak each turn, so 0.2.1 must make exactly **one generation call per speaking NPC
contribution**: six calls for three turns, and nothing else. 0.2.1 is driven with the
harness's **two-call profile**, which is *not* 0.2.1's default play: by default 0.2.1 also picks
follow-on speakers with a model call, runs a tracker call per post and schedules rolling-summary
and scene-break calls. The baseline is therefore a **lower bound** on ordinary 0.2.1 cost, the
cost of its one-generation-per-speaking-NPC core:

| 0.2.1 setting | Value | Why |
|---|---|---|
| group order | `list`, naming the two speakers; the other two NPCs `sitting_out` | no speaker-selection decide call; no narrator |
| `tracker` (campaign) | `off` | no `tracker-update` call per post |
| `rolling_summary_every`, `scene_break_every` (campaign) | `"0"` | no background follow-up calls |
| provider route | one connection, no fallback (a fresh library has none; every other provider kind's client refuses all requests) | one route |
| `send_images` | `off` (0.2.1 default, asserted) | no image input |
| `decide_turn_plan`, `decide_play_tool`, `epistemic_decide`, history/semantic recall | off (0.2.1 defaults, asserted) | no optional decides |
| `decide_speaker_sampling` | on (0.2.1 default, asserted) | samples an answer already asked for; adds no call |

Any other model call 0.2.1 makes under this profile fails the capture (`unplanned_v021_call`)
rather than being dropped. For each call the harness records 0.2.1's own usage-ledger row:
prompt (input) tokens including cache slices, cache read/write tokens, completion (output) tokens,
cost and its basis, attempts and wall latency. 0.2.1 has no reasoning-token field, and it records
only the attempt that served a call: a retried call's earlier attempts are booked as **failed with
unreported usage**, so such a capture is never "complete" (an opt-in test injects a rate limit
into a real 0.2.1 dry run to prove this). `conditions.model` is the model that actually served
every call (a dated snapshot of the requested one is accepted; any other model fails the capture
with `model_mismatch`), and `conditions.budget` is the `max_tokens` 0.2.1 actually sent. If the
capture fails after billed calls, the error output ends with a partial ledger of ids and counts.

### What the driver changes at runtime

The 0.2.1 source is not edited: the harness refuses a checkout that is not the pinned commit or
has modified or untracked files, before and after the run. At runtime the driver, which runs 0.2.1
in-process through FastAPI's `TestClient` (lifespan entered, as 0.2.1's own tests do):

- redirects 0.2.1's machine-local lock directory and bootstrap pointer into the temporary home
  (`store.proclock.lock_dir`, `store.paths.DEFAULT_HOME`, `store.paths.pointer_path`; the same
  seams 0.2.1's test suite uses), because 0.2.1 finds them from the account's real home;
- gives the gateway's provider HTTP client a counting transport that refuses anything but the chat
  endpoint and anything over `planned calls × (1 + llm_retries)`, built with 0.2.1's own CA bundle
  and timeouts and the environment's `HTTPS_PROXY`/`ALL_PROXY` (live) or 0.2.1's
  `tests/llm_fakes.SSEProvider` (dry run);
- in a dry run only, forces 0.2.1's token counter onto its offline heuristic
  (`store.tokens._encoder`), so a `[desktop]` install does not download a tokenizer.

The counting transport also keeps each provider reply in memory (never written or printed). When
a turn fails, the driver reports 0.2.1's error kind and each reply's shape: its status, its
length, the prose 0.2.1 reads from it (0.2.1's own `ResponseWatcher`, state-block split and macro
expansion), its hidden fences and whose labels that prose uses (0.2.1's own
`store.scenes._markers`). It changes nothing in the run.

Prompt assembly, provider adapters, retry and the usage meter are 0.2.1's own.

## Capture prerequisites and spending approval

The first slice stayed **baseline capture pending** until a real provider-reported capture
was obtained ([measured baseline](#measured-baseline-2026-11-20)). Passing replay or dry-run tests does not close that gate.
Before running the live command, the operator needs:

- An explicitly approved supported provider (`openrouter` or `anthropic`) and exact model/route.
  A credential or a model name alone does not establish a no-additional-cost route; establish
  that the selected route incurs no incremental charge before treating it as free.
- A named credential environment variable configured through secure environment settings,
  with network access permitted to the selected provider endpoint. Never paste the credential
  into chat, commands, documentation or PR logs, or recover one from a private V1 library.
- For a paid route, explicit approval of the maximum spend and currency for this capture,
  backed by a provider-enforced limit that covers the credential's requests. If that bound
  cannot be established, do not start the capture. `--confirm-billing` acknowledges billing;
  it does **not** enforce a monetary cap.

The approved scenario is `guild_betrayal`: four NPCs present, two speakers per turn, three
turns and **six planned contributions**. The transport permits at most
`6 × (1 + llm_retries)` requests, using the pinned V1 configuration's retry count. That request
ceiling and the recorded `max_tokens` response budget are not monetary spending limits.
Failed or incomplete captures can still incur charges; missing usage is never counted as free.

Use the pinned clean checkout and isolated interpreter described below. Keep raw outputs
outside the repository and all libraries, verify the evidence with the scoring command, and
review counts-only publication before changing the pending status. Consistent evidence is not
an authenticated provider billing receipt. Existing fixtures must remain labelled synthetic
or dry-run; no replay can substitute for the missing live measurement.

This baseline does not depend on V2 provider execution (013) or the generation runtime (027).
Those interfaces are needed for the later matched V2 comparison, which remains separate.

## Running it

The capture needs a separate Python environment with 0.2.1 installed from a clean checkout of
the pinned commit (keep that environment outside the checkout, or have it gitignored). Nothing is
installed by the harness. Run the V2 commands with V2 installed (`pip install -e "backend[dev]"`)
or from `backend/` with `PYTHONPATH=src`.

```bash
git worktree add --detach ../grimoire-v021 e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce
```

```bash
python3 -m venv ../v021-venv && ../v021-venv/bin/pip install -e "../grimoire-v021/backend[dev]"
```

Dry run (no provider request and no key; 0.2.1's real provider adapter over its own test
transport):

```bash
python -m grimoire.capture.v021 --scenario guild_betrayal --v1-checkout ../grimoire-v021 \
    --v1-python ../v021-venv/bin/python --dry-run --provider openrouter --out ../capture-dry
```

Live (billed; `openrouter` or `anthropic` only):

```bash
python -m grimoire.capture.v021 --scenario guild_betrayal --v1-checkout ../grimoire-v021 \
    --v1-python ../v021-venv/bin/python --live --provider openrouter --model MODEL \
    --api-key-env OPENROUTER_API_KEY --confirm-billing --out ../capture-live
```

`--out` must be a new or empty directory outside this repository, the 0.2.1 checkout, 0.2.1's
machine-local state directory and every Grimoire library. `--api-key-env` must name an ordinary
variable (not `PATH`, `HOME`, `GRIMOIRE_*`, `XDG_*`, `PYTHON*` or a TLS/proxy setting). It receives `run.json` (`grimoire.story-quality/2`), `evidence.json` (0.2.1's
usage rows and the scene's contributions; no prompts, no key) and `capture-report.txt` (ids and
counts only). On any failure nothing is written there.

The opt-in end-to-end test drives the real dry run (both providers, plus an injected retry):

```bash
GRIMOIRE_V021_CHECKOUT=../grimoire-v021 GRIMOIRE_V021_PYTHON=../v021-venv/bin/python \
    python -m pytest backend/tests/test_v021_real_dry_run.py -q
```

Score it, verifying the evidence:

```bash
python -m grimoire.evals.story_quality SCENARIO.json run.json run.json \
    --evidence-baseline evidence.json --evidence-candidate evidence.json
```

### Comparing V2 against the capture (068)

`python -m grimoire.capture.v2 --scenario guild_betrayal --baseline <capture dir> --out <new dir> --dry-run`
plays the same three turns on V2's own generation runtime and gates V2 against the capture. It takes the
provider, served model, output budget and attempt limit from the verified capture, so the two runs share
their recording conditions (a reply from another model is recorded as served and makes the runs
incomparable). `--live --confirm-billing --api-key-env VAR` makes it billed and is refused
against a dry-run capture; `--with-planning` adds a second V2 run with 068's optional planning stage.
`--samples N` plays V2 N times and `--baseline` may be repeated with other captures of the same play,
so both sides are gated over all their samples.
Against an `anthropic` capture V2 calls the Messages API, as 0.2.1 did, so cache reads are reported
and the cost can be compared.
See [ACTOR_INFERENCE](../ACTOR_INFERENCE.md#the-v2-against-021-comparison) for what it does and does
not prove. A live comparison needs its own spending approval, separate from this capture's.

## What "measured" means, and cannot mean

A `/2` run is reported **measured** only when its rebuilt usage ledger is complete and entirely
provider-reported **and** the supplied `evidence.json` matches the run's evidence digest and
re-converts to exactly the same run. Without verified evidence a live capture reads
"provider-reported (declared by capture, unverified)". A dry-run or synthetic source cannot hold
provider-reported numbers at all, and a test fails if any checked-in run or evidence claims a
live or provider-reported measurement. Verification proves the run and its evidence are
**consistent**, not where the evidence came from: the bundle is not authenticated, and the gate
cannot verify the provider's own billing receipt. The report says both.

## Known limitations of the first slice

- **Live text and markers.** The scenario's choice and continuity checks match exact phrases. Live
  0.2.1 replies will rarely contain them, so a live capture is likely to report hard findings and
  be unusable as a pass/fail baseline. The cost and voice figures are still recorded. Revising the
  markers, or adding reviewed labels, for live text is a later milestone (accepted for this slice).
- **Choice labels.** A live capture's `choice_outcomes` are always `unlabelled`; the harness never
  invents a label.
- **Rolls.** A 0.2.1 reply that pauses for a roll is a second call for one contribution, which
  fails the capture (`multi_call_contribution`).
- **Live captures usually fail.** Twelve of fourteen live attempts failed; see
  [earlier attempts](#earlier-attempts-the-same-day), the
  [2026-11-22 attempt](#sampled-capture-attempt-2026-11-22) and the
  [ten attempts with the failure report](#capture-attempts-2026-11-22-with-the-failure-report). The two with
  `claude-sonnet-4-5-20250929` match what 0.2.1's own empty-response check produces, which is
  reproduced offline but not confirmed live; the Sonnet 5.5 one was the prefill 400
  ([why](#why-turn-1-fails-offline-2026-11-22)). The driver now reports a failed turn by 0.2.1's
  error kind and the shape of each reply.
- **Windows.** The real driver has been run on Linux only.
- **Matched comparison.** A V2-vs-V0.2.1 comparison needs V2's provider adapter (013) and
  generation runtime (027); `/2` reserves a `v2-runtime` source for it.

## Measured baseline (2026-11-20)

The operator's play model is not an Anthropic model; they first chose `claude-sonnet-5-5` as
its stand-in and, when that could not work (below), approved any model that could.
`claude-sonnet-4-5-20250929` was used because it accepts the request shape 0.2.1 sends.

| Condition | Value |
|---|---|
| 0.2.1 commit | `e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce` |
| Provider, served model | `anthropic`, `claude-sonnet-4-5-20250929` (all six calls) |
| Profile, budget, retries | two-call, `max_tokens=16000`, `llm_retries` 2 |
| Evidence sha256 | `1824a08145de72fe527fd790102c4f67299a59e28cc62dbfc7b51d34a3d24ff0` |

Scored with `grimoire.evals.story_quality` and both `--evidence-…` options: usage status
**measured** (provider-reported, evidence consistent; origin and provider receipt unverified),
gate PASS on the self-comparison, 0 findings.

| Metric | Value |
|---|---|
| Calls, attempts | 6 `npc_generation`, 6 attempts, 0 unmetered |
| Input tokens | 15,618 |
| Output tokens | 1,396 |
| Cache read / write tokens | 0 / 0 |
| Reasoning tokens | unreported (0.2.1 has no field) |
| Cost in USD | unreported by the provider (6 of 6 calls) |
| Usage by actor | `npc-oda` 7,612 in / 789 out; `npc-bellamy` 8,006 in / 607 out |
| NPC lines, leak rate | 6, 0 |
| Cast distinguishability | 0.833 |
| Choices | 3 unlabelled, 0 negated, 0 unrealized |
| Total call latency | 35,473 ms |

Per call, in order (input / output tokens): 2,324 / 224; 2,408 / 64; 2,454 / 271;
2,556 / 295; 2,834 / 294; 3,042 / 248.

What this does and does not establish:

- It is a lower bound on ordinary 0.2.1 cost, for the two-call profile only, on one
  stand-in model. It says nothing about the operator's own play model.
- The ledger holds no cost. At an assumed list price of 3 USD input and 15 USD output per
  million tokens the six calls come to about 0.07 USD; that is an estimate, not a receipt.
- 0.2.1 sends each second speaker's request ending in the first speaker's reply, an assistant
  turn. On this model that turn acts as a prefill, which is 0.2.1's own behaviour on this route.
- The capture was produced by a copy of `backend/tools/v021_driver.py` with one added line that
  writes a turn's error frames to a scratch file when a turn fails. No turn failed, so the line
  did not run, but the driver was not byte-identical to the checked-in one.
- Raw `run.json` and `evidence.json` are kept outside the repository; only these counts are
  recorded here.

### Earlier attempts the same day

Both failed in turn 1, wrote nothing and were billed for the calls that had completed.

1. `claude-sonnet-5-5`: the first speaker's call succeeded (3,367 input, 484 output tokens);
   the second failed with no usage. Sonnet 5.5 rejects a conversation that ends in an assistant
   message with a 400 ("does not support assistant message prefill"). A dry run with request
   roles logged shows the second call of every turn ending in `assistant`, and a direct request
   of that shape to the model returned the 400. Unmodified 0.2.1 therefore cannot play this
   scenario on the `anthropic` provider with a model that rejects prefill.
2. `claude-sonnet-4-5-20250929`: both calls completed (2,324 / 210 and 2,410 / 19 tokens) and
   0.2.1 still reported the turn as failed. The cause was not captured; the 19-token second
   reply suggests 0.2.1's empty-response check, which is unconfirmed. The same command
   succeeded on the next attempt, so a live capture on this route can fail intermittently.

## Sampled capture attempt (2026-11-22)

068's sampled comparison needs five captures of the same play, so four more were approved, with
the same command, model (`claude-sonnet-4-5-20250929`) and stock driver. Only the first was
started, and it failed, so **no 2026-11-22 capture exists** and the measured baseline above is
still the only one.

| Sample | Result | Calls completed | Input tokens | Output tokens | Cast distinguishability |
|---|---|---:|---:|---:|---|
| 2 | failed in turn 1, nothing written | 2 | 4,721 | 213 | not measured |
| 3, 4, 5 | not started | 0 | 0 | 0 | not measured |

Both of sample 2's calls completed (2,324 / 194 and 2,397 / 19 tokens, no cache tokens) and
0.2.1 still reported the turn as failed. That is the same pattern as the second attempt on
2026-11-20, down to the 19-token second reply. The driver reports only the HTTP status, so the
cause is still not captured; 0.2.1's empty-response check remains the unconfirmed suspect. A
failed billed step was not to be retried, so the remaining captures were not run. At an assumed
list price of 3 USD input and 15 USD output per million tokens the two calls come to about
0.02 USD, as an estimate.

On this route the capture has now failed this way on two of three attempts with this model.
Five captures in a row are unlikely to succeed until the cause is known; it was found offline
afterwards (below).

## Why turn 1 fails (offline, 2026-11-22)

Two of the three live failures, both with `claude-sonnet-4-5-20250929`, failed in turn 1 with both
calls completed, a short second reply (19 output tokens) and HTTP 200. Their error kind was not
recorded live. Offline, with no provider request, on the pinned 0.2.1 commit and its own test
transport, 0.2.1's own `empty_response` check produces exactly that result:

- Each turn's second speaker is asked with a conversation that ends in the first speaker's reply, as
  an assistant message carrying that speaker's label, so the second reply continues it.
- 0.2.1 reads a reply's prose as what comes after its leading `perception` block and before its
  `handoff` fence, with a trailing `state` block split off and macros such as `{{user}}` expanded.
  It keeps that prose only where it comes before any speaker label or under the speaker's own label.
  Text under another label (the player's, the first speaker's or `You`) is dropped, and a reply left
  with no text fails the turn with `empty_response`.
- A dry run whose turn-1 second reply is empty, or holds only the fences 0.2.1's prompt asks for
  (the `perception` block it asks every reply to open with, the `handoff` fence it asks every reply
  to end with, or a `state` block), or is written only under another label, fails like the live
  runs: both calls complete and are booked `ok`, and the turn fails with HTTP 200. The same play
  with the second reply under its own label, or under no label, succeeds.

So the live failures are consistent with 0.2.1's own behaviour on this route, not with a fault in
the capture; that is not confirmed. A 19-token reply fits more than one of those shapes (a `handoff`
fence alone, or one short line under another label, for example), so which one the model wrote is
not known. The third failure (`claude-sonnet-5-5`) was different: the provider refused the prefilled
request with a 400 and no usage was booked.

The driver now records it. A failed turn is reported by 0.2.1's error kind; for each reply 0.2.1
streamed, the actor it was for and how many characters its client saw; and for each provider reply
of that turn, its HTTP status if it was not a success, else its length, how much of it 0.2.1 reads
as prose (with 0.2.1's own response watcher and normalising steps), which of the fences 0.2.1 hides
it held (`perception`, `handoff`, `state`) and whose labels that prose is written under (actor ids,
`you` or `another name`; never text). For example: `provider reply 2: 40 characters, 40 of prose, no
fence, prose under the label pc-wren`.

For 068 this means a capture can fail on any of its three turns (each second speaker's request ends
the same way) whenever that reply keeps no text of its own, and that retrying a capture is playing
0.2.1 again, not repairing it. A failed capture writes nothing and costs its completed calls (about
0.02 USD in turn 1). The comparison can only use captures that finish, so its baseline is 0.2.1's
completed plays; how often 0.2.1 fails a play is a separate measurement of 0.2.1, to be recorded
with the captures.

## Capture attempts (2026-11-22, with the failure report)

068's sampled comparison needs four more captures. Up to 10 attempts were approved, to stop at
four successes and to carry on after a failure only when 0.2.1 named it `empty_response`. All
10 were used and **one succeeded**, so the comparison was not run. Same command, model
(`claude-sonnet-4-5-20250929`) and stock driver for every attempt; every failure was HTTP 200
with 0.2.1 error `empty_response`, and each call that completed was billed.

| Attempt | Result | Failed turn | Calls completed | Input tokens | Output tokens | Failure line labels |
|---:|---|---:|---:|---:|---:|---|
| 1 | `empty_response` | 1 | 2 | 4,739 | 231 | second speaker: 0 characters of prose, handoff fence only |
| 2 | `empty_response` | 1 | 2 | 4,748 | 228 | same |
| 3 | `empty_response` | 1 | 2 | 4,722 | 201 | same |
| 4 | **success** | none | 6 | 15,254 | 978 | none |
| 5 | `empty_response` | 1 | 2 | 4,743 | 250 | same |
| 6 | `empty_response` | 1 | 2 | 4,725 | 232 | same |
| 7 | `empty_response` | 3 | 5 | 12,859 | 1,152 | first speaker: prose under the other speaker's label |
| 8 | `empty_response` | 1 | 2 | 4,723 | 216 | same as attempt 1 |
| 9 | `empty_response` | 1 | 2 | 4,724 | 208 | same |
| 10 | `empty_response` | 2 | 4 | 9,845 | 613 | same |

- **Failure rate, as a 0.2.1 measurement:** 9 of 10 attempts failed here, and 11 of 13 across
  every live attempt with this model. In 8 of the 9 the second speaker's provider reply held
  no prose at all, only a handoff fence (29 to 42 characters, 15 to 20 output tokens), which
  confirms live what was [found offline](#why-turn-1-fails-offline-2026-11-22). In the ninth
  (attempt 7) the first speaker's reply put its prose under the other speaker's label. Unmodified
  0.2.1 on this route usually cannot finish a three-turn, two-speaker scene.
- **The one success (attempt 4):** evidence sha256
  `1651c8a727962f814d1bb70545ed5d15893e625630561cc4a8d83e5c3171948d`, usage status measured,
  6 calls, 15,254 input and 978 output tokens, no cache tokens, cast distinguishability 0.667,
  leak rate 0, usage by actor `npc-oda` 7,442 in / 662 out and `npc-bellamy` 7,812 in / 316 out.
  Its self-comparison gate is FAIL: `choice_negated` on turn 1 (`ask-ledger`, unrealized), with
  `baseline_invalid` for the same finding. The evidence verifies; the play itself breaks a
  hard check, so this capture is not a second clean baseline.
- **Tokens and cost:** 29 billed calls in all, 71,082 input and 4,309 output tokens. At an
  assumed list price of 3 USD input and 15 USD output per million tokens that is about
  0.28 USD, as an estimate.

The measured baseline of 2026-11-20 is still the only capture without findings.

## Privacy

No credential, prompt or private library content may enter this repository, CI logs or the
capture outputs. The key is read from the named environment variable by the 0.2.1 child process
only, never passed on a command line, and the temporary 0.2.1 library holding it is deleted when
the capture ends (also on failure or interrupt). The harness refuses to write outputs that contain
the key. A measured capture is checked in only by a later, reviewed PR, as counts without text.
