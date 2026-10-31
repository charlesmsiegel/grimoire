# V0.2.1 cost and voice baseline (Sprint 015, first slice)

**Status:** baseline capture pending. The opt-in capture harness exists and has been proven end
to end against the unmodified `0.2.1` app in **dry-run** mode, whose numbers are synthetic. No
live, provider-reported capture has been run yet, so **no cost parity with V0.2.1 is claimed**.

## What is measured

The synthetic scenario `guild_betrayal` (`backend/src/grimoire/evals/benchmarks/guild_betrayal/`)
is played through the real `0.2.1` backend at commit `e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce`
(the `0.2.1` tag; the commit, not the movable tag, is pinned). Four NPCs are present and two
speak each turn, so 0.2.1 must make exactly **one generation call per speaking NPC
contribution**: six calls for three turns, and nothing else. 0.2.1 is driven with its
**two-call profile**:

| 0.2.1 setting | Value | Why |
|---|---|---|
| group order | `list`, naming the two speakers; the other two NPCs `sitting_out` | no speaker-selection decide call; no narrator |
| `tracker` (campaign) | `off` | no `tracker-update` call per post |
| `rolling_summary_every`, `scene_break_every` (campaign) | `"0"` | no background follow-up calls |
| `fallback_connection_id`, `send_images` | `""`, `off` | one route; no image input |
| `decide_turn_plan`, `decide_play_tool`, `epistemic_decide`, history/semantic recall | off (0.2.1 defaults, asserted) | no optional decides |
| `decide_speaker_sampling` | 0.2.1 default (on) | samples an answer already asked for; adds no call |

Any other model call 0.2.1 makes under this profile fails the capture (`unplanned_v021_call`)
rather than being dropped. For each call the harness records 0.2.1's own usage-ledger row:
prompt (input) tokens including cache slices, cache read/write tokens, completion (output) tokens,
cost and its basis, attempts and wall latency. 0.2.1 has no reasoning-token field, and it records
only the attempt that served a call: a retried call's earlier attempts are booked as **failed with
unreported usage**, so such a capture is never "complete".

## Running it

The capture needs a separate Python environment with 0.2.1 installed from a clean checkout of
the pinned commit. Nothing is installed by the harness.

```bash
git worktree add --detach ../grimoire-v021 e4c4ec4abacd0beb87fc9f1cdd06e6787d5a88ce
```

```bash
python3 -m venv ../v021-venv && ../v021-venv/bin/pip install -e "../grimoire-v021/backend[dev]"
```

Dry run (no network, no key; 0.2.1's real provider adapter over its own test transport):

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

`--out` must be a new or empty directory outside this repository, the 0.2.1 checkout and every
Grimoire library. It receives `run.json` (`grimoire.story-quality/2`), `evidence.json` (0.2.1's
usage rows and the scene's contributions; no prompts, no key) and `capture-report.txt` (ids and
counts only). On any failure nothing is written there.

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
live or provider-reported measurement. The gate cannot verify the provider's own billing
receipt.

## Known limitations of the first slice

- **Live text and markers.** The scenario's choice and continuity checks match exact phrases. Live
  0.2.1 replies will rarely contain them, so a live capture is likely to report hard findings and
  be unusable as a pass/fail baseline. The cost and voice figures are still recorded. Revising the
  markers, or adding reviewed labels, for live text is a later milestone (accepted for this slice).
- **Choice labels.** A live capture's `choice_outcomes` are always `unlabelled`; the harness never
  invents a label.
- **Rolls.** A 0.2.1 reply that pauses for a roll is a second call for one contribution, which
  fails the capture (`multi_call_contribution`).
- **Matched comparison.** A V2-vs-V0.2.1 comparison needs V2's provider adapter (013) and
  generation runtime (027); `/2` reserves a `v2-runtime` source for it.

## Privacy

No credential, prompt or private library content may enter this repository, CI logs or the
capture outputs. The key is read from the named environment variable by the 0.2.1 child process
only, never passed on a command line, and the temporary 0.2.1 library holding it is deleted when
the capture ends (also on failure or interrupt). The harness refuses to write outputs that contain
the key. A measured capture is checked in only by a later, reviewed PR, as counts without text.
