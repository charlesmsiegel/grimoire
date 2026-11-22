# Judge panel live run (Slice 069)

**Status:** run twice, with `claude-opus-5-5` as the Opus judge; the proof is
**not established**. CMS approved one live capture of **108 billed calls** under a
**$10 USD** cap on 2026-11-20 and a second on 2026-11-22, and both have been used:
[2026-11-20 results](../CALIBRATION.md#live-capture-2026-11-20) and
[2026-11-22 results](../CALIBRATION.md#live-capture-2026-11-22). The second, under parser
version 2, completed the defect panel and failed only
`theory_panel_scored_and_discriminates`. Another capture needs a new approval.
The harness and its offline proof are described in
[CALIBRATION.md](../CALIBRATION.md#independent-judge-panel).

## Panel

Three different Claude models are used, so the panel is not one model judging itself.
Save this as a file outside the repository, and set `PANEL` to its absolute path:

```json
{"trials": 3, "judges": [
 {"judge_id": "opus", "temperature": null, "model_config": {"provider_id": "anthropic", "base_url": "https://api.anthropic.com/v1", "model": "<OPUS_MODEL_ID>", "key_env": "ANTHROPIC_API_KEY", "max_output_tokens": 8000, "attempts": 1}},
 {"judge_id": "sonnet", "temperature": null, "model_config": {"provider_id": "anthropic", "base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-5-5", "key_env": "ANTHROPIC_API_KEY", "max_output_tokens": 8000, "attempts": 1}},
 {"judge_id": "haiku", "temperature": null, "model_config": {"provider_id": "anthropic", "base_url": "https://api.anthropic.com/v1", "model": "claude-haiku-5-5", "key_env": "ANTHROPIC_API_KEY", "max_output_tokens": 8000, "attempts": 1}}]}
```

- `<OPUS_MODEL_ID>` is the current Opus model's exact id, the one in the same release
  family as the Sonnet and Haiku judges. Take it from Anthropic's model list
  (`GET /v1/models`) and do not add a date suffix.
- `temperature` is null because these models reject sampling settings.
- `max_output_tokens` is 8000 because the Opus judge cannot turn thinking off. A shorter
  limit risks truncated answers, which count as `unknown`.
- `attempts` is 1: a retry is a second billed call.

Checked offline: this panel loads and plans 108 calls, about 16.7k prompt characters per
judge per trial round, which is about 43k input tokens for the whole run. Under parser
version 3, each of the 18 theory answers may be re-asked once if it puts a step off the
ladder, so a capture makes at most **126 calls**. At list prices on 2026-11-03, the worst
case for 108 calls (every answer at its 8000-token limit) is about $9. The 18 re-asks, each
carrying the judge's first answer back and bounded the same way, add at most about $2, so the
worst case for 126 calls is about **$11**. The two live captures cost about $0.55 and $0.58,
and the second would have needed 2 re-asks.

## Commands

The key must be in `ANTHROPIC_API_KEY` in the environment the session starts with. Run
these from `backend/` with `PYTHONPATH=src`:

```sh
python -m grimoire.judgepanel plan    --corpus tests/fixtures/calibration/panel --panel "$PANEL"
python -m grimoire.judgepanel capture --corpus tests/fixtures/calibration/panel --panel "$PANEL" \
    --out capture --confirm-billing --spend-cap "<approved> USD" --max-calls 126
python -m grimoire.judgepanel score   --corpus tests/fixtures/calibration/panel --panel "$PANEL" \
    --capture capture --coding-skills <checkout>
```

- `plan` must report `planned_calls` 108, `reasks_at_most` 18 and `max_calls` 126 before
  `capture` runs. `capture` refuses a `--max-calls` below 126, because a re-ask is a billed
  call.
- `--spend-cap` is recorded in the capture, not enforced. What bounds the run is
  `--max-calls 126` (the harness refuses a 127th call) with `attempts: 1` and each answer
  bounded at 8000 output tokens, which caps the worst case at about $11. The approved cap
  must cover that worst case. Do not raise either bound. The provider-enforced limit that [CALIBRATION.md](../CALIBRATION.md#spending-approval)
  requires is the key's prepaid credit: CMS said the key stops when its credits run out.
  That limit may be above the cap, so the call and token bounds are what keep this run within
  the approval.
- The worst case assumes the Opus judge costs $4 input and $20 output per million
  tokens. If the resolved `<OPUS_MODEL_ID>` is priced higher, stop and ask before capture.
- The `capture` folder must not exist yet. Every call is written before the next starts,
  and an interrupted call is recorded too. Do not rerun a capture to get a better result.
- `<checkout>` is a clone of `coding-skills` at commit
  `74f40dbd4bc301e276702b2854478f0e2eb2ac6a`. It must be completely clean, with no
  ignored files either, or scoring refuses it.
- `score` makes no calls; it replays the capture.

## Provider path

The 013 adapter sends to the OpenAI-compatible chat completions path,
`https://api.anthropic.com/v1/chat/completions`. On the 2026-11-20 run this path streamed usage and reported
the served model on all 108 calls; it reported no reasoning or cache token counts. If the first calls come back
refused, or their answers are `unknown` with reason `model_mismatch`, stop the capture and
report what the capture records show. Do not change the panel and run again without a
new approval, because that is a second billed run.

## Checking the slice

Mark Slice 069 in [README.md](../roadmap/README.md) and
[IMPLEMENTATION_AUDIT.md](../roadmap/IMPLEMENTATION_AUDIT.md#slice-069) as established
only if the score report's `proof.established` is true. Then record in the audit's Evidence
line the panel's defect false-positive and false-negative rates, its repeatability, the
theory score range, and whether the theory grade repeats. If it is false, name the proof
keys that failed, keep 069 unchecked, and do not narrow the requirement.

## Next capture

Parser version 4 re-asks a theory judge once when a step below 1.0 kept no listed line,
as well as when a step is off the ladder (see
[CALIBRATION.md](../CALIBRATION.md#independent-judge-panel)). The first fault is what left
one cohesive trial of the 2026-11-22 parser 3 capture unscored. Each answer is re-asked at
most once for both faults together, so the plan is still 108 calls plus at most 18 re-asks,
and `capture` still takes `--max-calls 126`. The prompts, the corpus and the panel did not
change. A new capture needs:

- CMS's fresh approval of a cap that covers the worst case of about $11. The three runs so
  far cost about $0.55, $0.58 and $0.58.
- A new, empty `capture` folder.
- The score report's `proof.captured_for_this_parser` to be true: a capture taken by this
  code records parser version 4 in its manifest. Check `parsing` per judge too. Name in the
  audit any judge that was re-asked (`reasked`), was still off the ladder or uncited after a
  re-ask (`off_ladder`, `uncited`), or had most of its answers read out of prose,
  `ambiguous` or missing citations, and give each judge's `dropped_shapes`.

Re-scoring the 2026-11-22 parser 3 capture under version 4 makes no calls. Its trials are
read as before: it holds no citation re-asks, and its manifest names parser version 3. It
does report each judge's `dropped_shapes`, which shows what form the dropped citations took.

### Parser version 3 (2026-11-22)

Parser version 3 re-asks a theory judge once when its answer puts a step off the rubric's
ladder. That is what left the 2026-11-22 parser 2 capture's cohesive unit unscored.
Re-scoring that capture under version 3 makes no calls but changes nothing: it holds no
re-asks, and its manifest names parser version 2.

### Parser version 2 (2026-11-20)

Parser version 2 (see [CALIBRATION.md](../CALIBRATION.md#independent-judge-panel)) reads an
answer out of prose before its JSON object and drops citations of lines the listing lacks.
The prompts, the corpus and the panel did not change, so the plan stayed 108 calls. The
second capture, on 2026-11-22, was taken under it, and needed:

- CMS's fresh approval of a cap. The first run's token counts come to about 0.55 USD at list
  prices, and the worst case is still about $9.
- A new, empty `capture` folder. The 2026-11-20 capture is not reused for the proof.
- The score report's `proof.captured_for_this_parser` to be true: a capture taken by this
  code records parser version 2 in its manifest. Check `parsing` per judge too: a judge whose
  answers mostly carry prose before the JSON, are `ambiguous`, or drop citations, is worth
  naming in the audit.

Optionally, re-score the 2026-11-20 capture first, if its folder still exists: `score` makes
no calls. That shows what version 2 changes on real answers, but it is not the proof, and the
report says so: its `established` stays false.

