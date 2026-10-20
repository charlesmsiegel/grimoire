# Model settings migration and retirement

Source: [migrate.py](../../../../../backend/src/grimoire/store/inference/migrate.py), [retire.py](../../../../../backend/src/grimoire/store/inference/retire.py), [store-guarantees.md](../../../../../docs/store-guarantees.md).

- **Model settings moved to a new layout, once, in the background**
  (`store/inference/migrate/`, started by `main.start` at startup and after a
  data-dir move). Its first protected write waits for a settings safety archive named
  `pre-inference-settings-grimoire-<stamp>.zip` (`backups.SAFETY_PREFIX`), and retention
  leaves it alone: `GET /backups` lists it beside the ordinary series, but
  `backups.sweep` counts and deletes only the ordinary series, so it stays
  until a person removes it. The same run then **retires** the legacy layout
  (`store/inference/retire.py`, run by `migrate._retire` after the marker, and
  again on any later start that finds something left): it persists the
  planner's derived reasoning presets, repoints and deletes the legacy keys and
  stamps `inference_retired`, failing closed on any file it cannot read, and
  when that deletes or replaces a stored value it first takes a second
  never-pruned settings archive, `pre-retirement-settings-grimoire-<stamp>.zip`
  (`backups.RETIRE_PREFIX`) -- unless this same run took the
  `pre-inference-` one. Both partial archives preserve exact bytes of
  `config.md`, recursive `llm_connections/` and `sampler_presets/`,
  `inference-retired.json`, and immediate campaigns' `campaign.md` and
  `revision.txt` (#496). They never walk images or transcripts, never satisfy
  the full-backup schedule, and need the original library or a full backup
  for manual recovery with the app stopped. Older full safety names remain
  recognized and count for scheduling: `pre-inference-grimoire-<stamp>.zip`
  and `pre-retirement-grimoire-<stamp>.zip`. Status exposes independent `active`
  and archive `progress` (`phase`, `done`, nullable `total`); polling continues
  through retirement even at format 2/done, with settings still editable.
  Until the switch lands the
  new Models settings answer 409 `not_migrated` (play carries on through the
  planner, in memory -- `store/inference/legacy_plan/`, the one reader of the
  legacy layout, which `test_legacy_reader_guard.py` holds to being the only
  one); a store a newer build switched refuses every
  model-settings write with 409 `newer_format`. Backup, marker, resume, busy
  campaigns and older builds are all in `docs/store-guarantees.md`.
