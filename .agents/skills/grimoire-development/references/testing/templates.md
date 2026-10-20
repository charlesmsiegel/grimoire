# Prompt verification

Source: [README.md](../../../../../templates/README.md), [verify_templates.py](../../../../../scripts/verify_templates.py), [README.md](../../../../../evals/README.md).

- **After editing anything in `templates/`**, `make check` covers both harnesses
  that guard prompts: `scripts/verify_templates.py` (builders and templates agree
  byte-for-byte) and `evals/run.py` (see `evals/README.md`). Offline, the eval
  suite proves the *instructions* are still in the assembled prompt — it
  renders the budget, reply-format, roll-protocol, active-speaker and
  available-art sections and requires each one verbatim in the prompt (so all
  five length knobs, the whole check roster, the nomination the speaker layer
  derived and the art handles it offered are covered),
  plus every key of the absorb contract and owned-lore containment — and that
  the graders still score recorded output correctly. It runs inside
  `pytest backend`.
  Whether the model still *follows* a reworded instruction is a question only
  `evals/run.py --live` answers; that makes real LLM calls and is opt-in.
