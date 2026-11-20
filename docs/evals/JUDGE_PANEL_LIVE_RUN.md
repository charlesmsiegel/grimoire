# Judge panel live run (Slice 069)

**Status:** approved and not yet run. CMS approved one live capture of **108 billed calls**
under a **$10 USD** cap. No billed calls have been made, so no defect or theory judge
accuracy is claimed. The harness and its offline proof are described in
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
judge per trial round, which is about 43k input tokens for the whole run. At list prices
on 2026-11-03, the worst case (every answer at its 8000-token limit) is about $9, and the
likely cost is about $2.

## Commands

The key must be in `ANTHROPIC_API_KEY` in the environment the session starts with. Run
these from `backend/` with `PYTHONPATH=src`:

```sh
python -m grimoire.judgepanel plan    --corpus tests/fixtures/calibration/panel --panel "$PANEL"
python -m grimoire.judgepanel capture --corpus tests/fixtures/calibration/panel --panel "$PANEL" \
    --out capture --confirm-billing --spend-cap "10 USD" --max-calls 108
python -m grimoire.judgepanel score   --corpus tests/fixtures/calibration/panel --panel "$PANEL" \
    --capture capture --coding-skills <checkout>
```

- `plan` must report 108 planned calls before `capture` runs.
- `--spend-cap` is recorded in the capture, not enforced. What holds the run under $10 is
  `--max-calls 108` (the harness refuses a 109th call) with `attempts: 1` and each answer
  bounded at 8000 output tokens, which caps the worst case at about $9. Do not raise either
  bound. The provider-enforced limit that [CALIBRATION.md](../CALIBRATION.md#spending-approval)
  requires is the key's prepaid credit: CMS said the key stops when its credits run out.
  That limit may be above $10, so the call and token bounds are what keep this run within
  the approval.
- The $9 worst case assumes the Opus judge costs $4 input and $20 output per million
  tokens. If the resolved `<OPUS_MODEL_ID>` is priced higher, stop and ask before capture.
- The `capture` folder must not exist yet. Every call is written before the next starts,
  and an interrupted call is recorded too. Do not rerun a capture to get a better result.
- `<checkout>` is a clone of `coding-skills` at commit
  `74f40dbd4bc301e276702b2854478f0e2eb2ac6a`. It must be completely clean, with no
  ignored files either, or scoring refuses it.
- `score` makes no calls; it replays the capture.

## Unconfirmed

The 013 adapter sends to the OpenAI-compatible chat completions path,
`https://api.anthropic.com/v1/chat/completions`. That this path streams usage and reports
the served model has not been checked against a live call. If the first calls come back
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
