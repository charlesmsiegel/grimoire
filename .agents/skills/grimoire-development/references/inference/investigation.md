# Reader-started investigations

Source: [investigation.py](../../../../../backend/src/grimoire/routes/investigation.py), [budget.py](../../../../../backend/src/grimoire/store/investigation/budget.py).

- **An investigation is a bounded tool loop a reader starts** (roadmap 12,
  `routes/investigation.py` over `store/investigation/`). Each entry point is
  its own task on the `investigation` route (`requires=("tools",)`, born at
  format 2), its own default-off switch and its own budget
  (`store/investigation/budget.py`, the one home of its numbers); its decide
  tool is `investigation-decide` on `investigation_decide`. Every loop turn
  files a ledger row under its task with `run_id` and `loop_turn`, so its cost
  lands in the three columns like any call, and a trace's money is those rows
  folded the same way, never added. The tools only read --
  `test_investigation_writer_guard.py` -- and what the model names is checked,
  never taken: a proposal cites only scenes the run READ, an answer only
  citations read, and the one write is `reconcile.persist_investigation`.
