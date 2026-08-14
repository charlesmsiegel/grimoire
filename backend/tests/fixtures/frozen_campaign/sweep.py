"""The read-only sweep the frozen-campaign harness snapshots (#205).

`sweep()` walks a store from the outside in — worlds, then the campaign, then
each scene — calling only read paths, and returns one JSON-able mapping of
`label -> value`. `test_frozen_campaign.py` runs it against a copy of `home/`
and compares the result to `snapshot.json`.

Two frozen artifacts, two different rules:

- **`home/`** — the input tree. Never regenerated. It is a store as some past
  version of the app wrote it, and its whole value is that today's code has to
  keep reading it.
- **`snapshot.json`** — the expected output. Regenerated *deliberately*, when a
  render or an assembly genuinely changed and the new text was reviewed as
  correct, in the same commit as the change that moved it:

      PYTHONPATH=backend/src backend/.venv/bin/python \
          -m tests.fixtures.frozen_campaign.sweep

  (run from `backend/`). Regenerating it to make a red test green throws away
  the only thing standing between a store refactor and a silently broken read.

What deliberately stays out of the sweep:

- **Anything that calls an LLM.** The sweep must run offline and free; the
  fakes in `tests/llm_fakes.py` (#204) cover the generating paths.
- **Token counts.** `context.tokens.count_tokens` falls back to a
  characters/4 heuristic when tiktoken is missing, and tiktoken is a `desktop`
  extra — so `make check-pydantic1`, which installs the Android dependency set,
  would disagree with `make check-py` on every count. Section *composition* is
  snapshotted; the numbers are not.
- **Weather.** A drawn forecast belongs to a campaign that has one; this
  fixture deliberately has none, and `test_weather_draw.py`'s vector file
  already freezes the generator itself.

Two couplings worth naming rather than hiding, because both can move this
snapshot without anything in `store/` having regressed:

- The assembled prompt for the dated scene contains a `# Today` section naming
  US federal holidays from the `holidays` package; a release that renames one
  moves the snapshot. The suite already carries that exposure
  (`test_calendars.py` and `test_context.py` both assert holiday names), so the
  section is snapshotted rather than stripped.
- The campaign binds the **built-in** `d20-basic` module pack, which ships
  inside `grimoire.store.builtin_modules` rather than in the fixture, so an
  edit to that pack moves the sheet, rules and check-roster sections. That is
  deliberate: a builtin pack edit really does change every campaign bound to
  it, and this is the only place that shows up as a diff.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from grimoire import store

HERE = Path(__file__).resolve().parent
HOME = HERE / "home"
SNAPSHOT = HERE / "snapshot.json"


def normalize(value, home: Path):
    """JSON-able, diff-able, path-free.

    Multi-line strings become `{"__text__": [line, ...]}` so a changed prompt
    shows up as changed *lines* in review instead of one 6 KB line nobody
    reads. Absolute paths are folded to `<HOME>`: the sweep runs from a
    tmp_path copy, so a real path in the snapshot would make it machine-local.
    """
    if isinstance(value, dict):
        return {str(k): normalize(v, home) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [normalize(v, home) for v in value]
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        text = value.replace(str(home), "<HOME>").replace(str(home).replace("\\", "/"), "<HOME>")
        return {"__text__": text.split("\n")} if "\n" in text else text
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return repr(value)


def _sections(breakdown: dict) -> list[dict]:
    """The context inspector's rows without their token counts — see the module
    docstring on why the numbers are not snapshotted."""
    return [{"label": r["label"], "tier": r["tier"], "dropped": r["dropped"],
             "trimmed": r["trimmed"]} for r in breakdown["sections"]]


def sweep(home: Path) -> dict:
    """Every read the harness guards, labelled by the call that produced it."""
    out: dict[str, object] = {}
    out["worlds.list_worlds"] = store.worlds.list_worlds()

    for w in out["worlds.list_worlds"]:
        wid = w["id"]
        wroot = store.worlds.world_root(wid)
        out[f"worlds.read_world[{wid}]"] = store.worlds.read_world(wid)
        out[f"tags.read_tags[{wid}]"] = store.tags.read_tags(wroot)
        out[f"characters.list_characters[{wid}]"] = store.characters.list_characters(wroot)
        for c in out[f"characters.list_characters[{wid}]"]:
            for v in c["versions"]:
                out[f"characters.read_card[{wid}/{c['id']}/{v['id']}]"] = \
                    store.characters.read_card(wroot, c["id"], v["id"])
        for kind in ("locations", "lore"):
            listed = store.entities.list_entities(wroot, kind)
            out[f"entities.list_entities[{wid}/{kind}]"] = listed
            for e in listed:
                out[f"entities.read_entity[{wid}/{kind}/{e['id']}]"] = \
                    store.entities.read_entity(wroot, kind, e["id"])
        out[f"greetings.list_greetings[{wid}]"] = store.greetings.list_greetings(wroot)
        out[f"greetings.read_plotmap[{wid}]"] = store.greetings.read_plotmap(wroot)
        for g in out[f"greetings.list_greetings[{wid}]"]:
            out[f"greetings.read_greeting[{wid}/{g['id']}]"] = \
                store.greetings.read_greeting(wroot, g["id"])

    out["campaigns.list_campaigns"] = store.campaigns.list_campaigns()
    for c in out["campaigns.list_campaigns"]:
        _campaign(out, c["id"])
    return normalize(out, home)


def _campaign(out: dict, cid: str) -> None:
    croot = store.campaigns.campaign_root(cid)
    out[f"campaigns.read_campaign[{cid}]"] = store.campaigns.read_campaign(cid)
    out[f"modules.resolve[{cid}]"] = store.modules.resolve(cid)
    # The overlay's view, not the world's: what a campaign actually reads is
    # the merge of its own files over the world's, and every listing below is
    # the campaign-side call the app makes.
    out[f"overlay.list_characters[{cid}]"] = store.overlay.list_characters(cid)
    out[f"overlay.list_pcs[{cid}]"] = store.overlay.list_pcs(cid)
    for kind in ("locations", "lore"):
        listed = store.overlay.list_entities(cid, kind)
        out[f"overlay.list_entities[{cid}/{kind}]"] = listed
        for e in listed:
            out[f"overlay.read_entity[{cid}/{kind}/{e['id']}]"] = \
                store.overlay.read_entity(cid, kind, e["id"])
    out[f"overlay.list_greetings[{cid}]"] = store.overlay.list_greetings(cid)
    # The scene ledger's composed half (#88). It is a NEW reader over OLD data
    # -- played.json (which has a legacy bare-list form), the greeting files,
    # and the plot map whose gates decide which greeting is an idea at all --
    # which is exactly what this fixture exists to catch. The stored half needs
    # no entry: there is no old scene_ideas.json to misread, and an absent one
    # reading as empty is covered directly in test_scene_ideas_store.
    out[f"playing.greeting_ideas[{cid}]"] = store.playing.greeting_ideas(cid)
    out[f"appearances.roster[{cid}]"] = store.appearances.roster(cid)
    out[f"chronicle.read_chronicle[{cid}]"] = store.chronicle.read_chronicle(cid)
    out[f"chronicle.recent[{cid}]"] = store.chronicle.recent(cid, 10)
    out[f"plot.read[{cid}]"] = store.plot.read(cid)
    out[f"plot.open_threads[{cid}]"] = store.plot.open_threads(cid)
    out[f"plot.render_open[{cid}]"] = store.plot.render_open(cid, with_id=True)
    out[f"commitments.read[{cid}]"] = store.commitments.read(cid)
    out[f"commitments.open_commitments[{cid}]"] = store.commitments.open_commitments(cid)
    out[f"commitments.render_open[{cid}]"] = store.commitments.render_open(cid, with_id=True)
    out[f"relationships.read[{cid}]"] = store.relationships.read(cid)
    out[f"sheets.coverage[{cid}]"] = store.sheets.coverage(cid)

    for ch in out[f"overlay.list_characters[{cid}]"]:
        out[f"dossiers.read[{cid}/{ch['id']}]"] = store.dossiers.read(croot, ch["id"])
        sheet = store.sheets.read(cid, "characters", ch["id"])
        if sheet:
            out[f"sheets.read[{cid}/characters/{ch['id']}]"] = sheet

    scenes = store.scenes.list_scenes(cid)
    out[f"scenes.list_scenes[{cid}]"] = scenes
    for s in scenes:
        _scene(out, cid, s["id"])


def _scene(out: dict, cid: str, sid: str) -> None:
    out[f"scenes.read_scene[{cid}/{sid}]"] = store.scenes.read_scene(cid, sid)
    out[f"appearances.scene_cast[{cid}/{sid}]"] = store.appearances.scene_cast(cid, sid)
    out[f"scenes.get_location_history[{cid}/{sid}]"] = store.scenes.get_location_history(cid, sid)
    out[f"scenes.get_time_history[{cid}/{sid}]"] = store.scenes.get_time_history(cid, sid)
    out[f"chronicle.scene_facts[{cid}/{sid}]"] = store.chronicle.scene_facts(cid, sid)
    out[f"checks.available_checks[{cid}/{sid}]"] = store.checks.available_checks(cid, sid)
    # The prompt itself: the reason this harness exists. A store refactor that
    # drops a section, or a template edit that changes the assembled text,
    # lands here as a line-level diff.
    out[f"context.build_messages[{cid}/{sid}]"] = store.context.build_messages(cid, sid)
    out[f"context.sections[{cid}/{sid}]"] = _sections(store.context.context_breakdown(cid, sid))


def _write_snapshot() -> int:
    """Regenerate `snapshot.json` from a scratch copy of `home/`. Deliberate —
    see the module docstring."""
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        shutil.copytree(HOME, home)
        os.environ["GRIMOIRE_HOME"] = str(home)
        store.migrations.migrate_scene_ids()
        data = sweep(home)
    SNAPSHOT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT} ({len(data)} sections)")
    return 0


if __name__ == "__main__":
    sys.exit(_write_snapshot())
