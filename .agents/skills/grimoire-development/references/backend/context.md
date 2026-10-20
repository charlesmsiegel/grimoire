# Prompt perspective, activation and voice

Source: [epistemic/](../../../../../backend/src/grimoire/store/context/epistemic/), [activation.py](../../../../../backend/src/grimoire/store/context/activation.py), [voice_anchors.py](../../../../../backend/src/grimoire/store/voice_anchors.py).

- **History reaches a prompt only through the view built for that prompt's
  reader** (roadmap 11-S3). Each compose step retrieves for its own
  perspective (`routes/history_recall.gather_view`: the narrator, or one NPC
  whose query is built from what it observed and its own seeds, never
  reranked), `store/context/epistemic.classify` says which parts that reader
  has, and compose renders only what the view allows: the narrator's
  `history_recall` and `history_narrator_only`, an NPC's `history_known` and
  `history_suspected` from `visible()` alone. Compose refuses a view built for
  another scene or speaker, and with no view renders no history. A failure
  narrows: the narrator keeps 09's items unannotated, an NPC gets nothing. The
  basis codes are inspector data and never reach a message.

  **The Decision stage only narrows the gap it was asked about** (11-S6,
  `routes/epistemic.refine`). Behind `epistemic_decide`, an NPC step's
  `unknown` parts may be put to a `decide` call, but only on a basis that
  proves nothing either way (`epistemic.ELIGIBLE_BASES`) and only with a
  structural channel (`store/context/epistemic_items`). The call is capped at
  8 parts per step and 16 per round, the round's count living on its record.
  It runs under one `EPISTEMIC_CEILING_S` deadline, never on a replay or a
  continuation, and once per (round, speaker).
  - The answers re-enter `classify(..., decided=)`. A model can make a part
    `known` or `suspected` or leave it `unknown`, never `witnessed` or
    `narrator_only`, and every failure leaves the part withheld.
  - The view a step composed with rides its response's snapshot
    (`epistemic.to_record`, no withheld text), so a roll resume in a later
    request recomposes with it.
  - The switch is off by default and drawn by no Settings control until its
    play gate is ratified. With it off, nothing past the switch is read.
- **World-info activation is one engine, `store/context/activation.py`.**
  Keys, secondary-key logic, per-entry scan depth, recursion, presence and
  sticky/cooldown are all decided there. Sticky/cooldown are *derived by
  replaying the transcript* rather than stored, so a cut, retcon or reroll
  changes them the way it changes the transcript. Two rules are easy to
  undo. **Reasons never reach the prompt**: the `reason` dicts and the
  `names` map feed the inspector rows and prompt-log captures only, and
  `test_reasons_never_reach_the_prompt` holds that. **A section may carry a
  `shed` hook** (`{"units", "render"}`) that the packer uses to drop entries
  one at a time instead of the whole section. `render(kept)` re-renders from
  bodies already macro-expanded, so shedding never re-draws a `{{random}}`.
  A store that sets none of the activation fields composes byte-identical
  prompts, which `test_lore_golden.py` pins against a golden recorded from the
  pre-engine code. Never regenerate that golden to make a change pass.
- **The voice anchor is a PROMPT input now, not only a judge input.** It
  renders per present character in `voice_anchors.j2` and is also what
  `voice_drift` judges a played scene against — both through
  `voice_anchors.effective()`, so a rule past the cap is enforced against
  neither. The drift check is therefore an approximate second opinion rather
  than a proof: it compares against the character's *current* anchor, which
  may differ from what a turn received (substitution, a packer drop, a layout
  disable, an edit between playing and absorbing, a local template edit). A
  `drift` verdict means "worth looking at". `store/voice_anchors.py`'s
  docstring carries the reasoning, including what was given up to get here.
