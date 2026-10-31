# V0.2.1 cost and voice baseline (Sprint 015, first slice)

**Status:** baseline capture pending. The opt-in capture harness exists and has been proven end
to end against the unmodified `0.2.1` app in **dry-run** mode, whose numbers are synthetic. No
live, provider-reported capture has been run yet, so **no cost parity with V0.2.1 is claimed**.

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

Prompt assembly, provider adapters, retry and the usage meter are 0.2.1's own.

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
- **The live path has not been exercised.** It differs from the dry run only in the transport and
  the key, but no live capture has been run (no key was available to this slice).
- **Windows.** The real driver has been run on Linux only.
- **Matched comparison.** A V2-vs-V0.2.1 comparison needs V2's provider adapter (013) and
  generation runtime (027); `/2` reserves a `v2-runtime` source for it.

## Privacy

No credential, prompt or private library content may enter this repository, CI logs or the
capture outputs. The key is read from the named environment variable by the 0.2.1 child process
only, never passed on a command line, and the temporary 0.2.1 library holding it is deleted when
the capture ends (also on failure or interrupt). The harness refuses to write outputs that contain
the key. A measured capture is checked in only by a later, reviewed PR, as counts without text.
