"""The play gate (roadmap 02 §4, slice 02-S5): what a play decision does to
PLAY, at what cost, measured live on fixed synthetic fixtures before anyone is
offered its switch.

`--gate` cannot gate these features: it compares a legacy parse with a decide
parse, and every 02 site but the speaker draw is new (§4.1). So each feature
is run through the app itself -- a real `POST /chat` on a seeded throwaway
store -- once per CONFIGURATION (a scene-route selection, a Decision
selection and the feature's setting) and `--repeat` times each, and the
result is graded twice: per run (the feature's own graders, the turn
checks) and across runs (the hard criteria of §4.3, stance diversity,
speaker spread, latency).

Three rules carried from 01a:

- **The isolate is seeded, never the real store.** `read_selected` reads the
  selected providers' files out of the real store BEFORE the isolate is
  entered; `seed` then writes exactly those, the format-2 role selections and
  the feature's switch into the isolate. Nothing here writes the real store,
  and a configuration whose decision ran on a provider the isolate never got
  would measure "off" under an "on" label -- the "on is really on" criterion
  is what catches that.
- **Drain before harvest.** A landed turn schedules detached follow-ups, so a
  run hands its app to `runner.drain` and is harvested only once nothing is
  live (`runner.DRAIN_CEILING_S`). The isolate turns the tracker, the
  rolling summary and the scene-break check off: they are identical with the
  feature off and on, and only cost.
- **Rows are the eval scope's** (`runner.harvest`): stamped `scope: "eval"`,
  carrying the FIXTURE campaign's id, never a real one.

Fixtures use placeholder names only (Seraphine, Mara, Winifred, Saltmarch,
Realm), and no report may quote or measure a real store (§3.7).
"""

from __future__ import annotations

import contextlib
import statistics
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire import decisions, draws
from grimoire.store import (
    appearances,
    campaigns,
    characters,
    llm_connections,
    pcs,
    responses,
    sampler_presets,
    scenes,
    turn_plan,
    worlds,
)
from grimoire.store import config as store_config
from grimoire.store import response_protocol as rp
from grimoire.store.inference import settings as inference_settings

from .graders import Check

#: `--play`'s features: which switch each sets, the values it compares, and
#: the tasks it adds calls under ("" for none: the speaker draw adds no call).
FEATURES: dict[str, dict[str, Any]] = {
    "speaker": {"key": "decide_speaker_sampling", "off": "off", "on": "on", "tasks": ()},
    "intent": {"key": "decide_turn_plan", "off": "off", "on": "intent",
               "tasks": ("turn-intent",)},
    "plan": {"key": "decide_turn_plan", "off": "off", "on": "plan", "tasks": ("turn-plan",)},
    "tool": {"key": "decide_play_tool", "off": "off", "on": "on",
             "tasks": ("turn-tool-decision",)},
}

#: The tasks whose calls come before a contribution's first token: the
#: speaker pick and the turn decisions. "Added pre-generation time" is theirs.
PRE_GENERATION_TASKS = frozenset({"response-selector", "turn-intent", "turn-plan"})

#: `--repeat`'s default in this mode (§4.2): one live run is an anecdote.
DEFAULT_REPEAT = 5

#: A fixture's intended speaker when any present NPC is a fine answer.
ANY = "*"

#: The cast: the player's character and two NPCs, placeholder names.
PLAYER = "Seraphine"
NPCS = ("Mara", "Winifred")


@dataclass(frozen=True)
class Fixture:
    """One synthetic scene and the post sent into it. `intended` is the
    speaker name the fixture expects to open the round (`ANY`, or None for a
    hand-back); `addressed` says the trigger names someone."""

    id: str
    posts: tuple[tuple[str, str, str], ...]
    send: dict
    intended: str | None
    addressed: bool = False


#: §5.7's fixtures: an addressed NPC (Seraphine asks Mara), an open remark to
#: two NPCs, a pending player decision, and a director note naming Winifred.
FIXTURES: tuple[Fixture, ...] = (
    Fixture("addressed",
            (("user", "", "Seraphine sets the lantern on the crate between Mara and Winifred."),
             ("assistant", "Winifred", "You are late again.")),
            {"content": "Mara, where were you when the tide bell rang?"}, "Mara", True),
    Fixture("open-remark",
            (("user", "", "Seraphine shakes the rain off her coat on the night dock."),),
            {"content": "Well? One of you has something to tell me."}, ANY),
    Fixture("pending-decision",
            (("user", "", "Seraphine finds a sealed crate stamped with the harbour seal."),
             ("assistant", "Mara", "Open it or leave it. Your call, not mine.")),
            {"content": "I keep my hand on the lid and think it over before I decide."}, None),
    Fixture("director-note",
            (("user", "", "Seraphine asks the two of them about the missing ledger."),),
            {"content": "Winifred answers first, guarded.", "director": True}, "Winifred",
             True),
)

BY_ID = {f.id: f for f in FIXTURES}


@dataclass(frozen=True)
class Selection:
    """A provider and model (and, for the scene route, a sampler preset)."""

    provider: str
    model: str
    preset: str = ""

    @classmethod
    def parse(cls, spelled: str) -> Selection:
        """`provider/model[/preset]`. A model id may hold a slash
        (`vendor/model`), so a preset is only read from a fourth part, as
        `provider/vendor/model/preset`, or after `#`: `provider/model#preset`."""
        head, _, preset = spelled.partition("#")
        provider, sep, model = head.partition("/")
        if not (provider and sep and model):
            raise ValueError(f"{spelled!r} is not provider/model[#preset]")
        return cls(provider, model, preset)

    def spelled(self) -> str:
        return f"{self.provider}/{self.model}" + (f"#{self.preset}" if self.preset else "")


@dataclass(frozen=True)
class PlayConfig:
    """One configuration of a feature's matrix (§4.2)."""

    id: str
    feature: str
    setting: str
    scene: Selection
    decision: Selection
    cutoff: str = "1/(2n)"

    def axes(self) -> dict[str, str]:
        """01a-C3's open axes (§4.2)."""
        out = {"feature": self.setting, "scene_selection": self.scene.spelled(),
               "decision_selection": self.decision.spelled()}
        if self.feature == "speaker":
            out["sampling"] = self.setting
            out["cutoff"] = self.cutoff
        return out

    @property
    def on(self) -> bool:
        return self.setting != FEATURES[self.feature]["off"]


def matrix(feature: str, scenes_: Sequence[Selection],
           deciders: Sequence[Selection]) -> list[PlayConfig]:
    """A feature's configurations: off, then on, for each Decision selection
    on the first scene selection; for `speaker`, a `cutoff: 0` arm as well
    (open question 9); for `plan`, the plan on every further scene selection
    too (§7.5's B, C, D against A)."""
    if feature not in FEATURES:
        raise ValueError(f"no such feature: {feature!r}")
    if not scenes_ or not deciders:
        raise ValueError("a matrix needs a scene selection and a Decision selection")
    spec = FEATURES[feature]
    out: list[PlayConfig] = []

    def add(setting: str, scene: Selection, decision: Selection, cutoff: str = "1/(2n)"):
        out.append(PlayConfig(f"c{len(out) + 1}", feature, setting, scene, decision, cutoff))

    for decision in deciders:
        add(spec["off"], scenes_[0], decision)
        add(spec["on"], scenes_[0], decision)
        if feature == "speaker":
            add(spec["on"], scenes_[0], decision, cutoff="0")
    if feature == "plan":
        for scene in scenes_[1:]:
            add(spec["on"], scene, deciders[0])
    return out


# --- seeding the isolate --------------------------------------------------------


@dataclass
class Selected:
    """What `read_selected` read out of the real store: each provider's files
    by name, and each preset's."""

    providers: dict[str, dict[str, bytes]] = field(default_factory=dict)
    presets: dict[str, bytes] = field(default_factory=dict)


def _provider_files(provider: str) -> list[Path]:
    return [llm_connections._path(provider), llm_connections._sidecar_path(provider),
            llm_connections.regex_path(provider), llm_connections.facts_path(provider)]


def read_selected(configs: Sequence[PlayConfig]) -> Selected:
    """Every provider (and preset) the configurations select, read out of the
    ACTIVE store -- the real one, before any isolate is entered. A provider
    with no record is a `ValueError` naming it: the run would otherwise
    measure a refusal."""
    out = Selected()
    for conf in configs:
        for sel in (conf.scene, conf.decision):
            if sel.provider not in out.providers:
                files = {p.name: p.read_bytes() for p in _provider_files(sel.provider)
                         if p.exists()}
                if not files:
                    raise ValueError(f"no provider {sel.provider!r} in this store")
                out.providers[sel.provider] = files
            if sel.preset and sel.preset not in out.presets:
                path = sampler_presets._path(sel.preset)
                if not path.exists():
                    raise ValueError(f"no sampler preset {sel.preset!r} in this store")
                out.presets[sel.preset] = path.read_bytes()
    return out


def seed(conf: PlayConfig, selected: Selected) -> None:
    """Seed the ACTIVE store (an isolate) with exactly `conf`'s providers and
    preset, its Primary and Decision selections, and the feature's switch;
    the tracker, the rolling summary and the scene-break check off. Writes
    nothing else."""
    for provider in {conf.scene.provider, conf.decision.provider}:
        folder = llm_connections._dir()
        folder.mkdir(parents=True, exist_ok=True)
        for name, data in selected.providers[provider].items():
            (folder / name).write_bytes(data)
    if conf.scene.preset:
        path = sampler_presets._path(conf.scene.preset)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(selected.presets[conf.scene.preset])
    primary = {"provider": conf.scene.provider, "model": conf.scene.model}
    if conf.scene.preset:
        primary["preset"] = conf.scene.preset
    inference_settings.write("global", "", {"roles": {
        "primary": {"selection": primary},
        "decision": {"selection": {"provider": conf.decision.provider,
                                   "model": conf.decision.model}}}})
    store_config.write_config(**{FEATURES[conf.feature]["key"]: conf.setting,
                                 "tracker": "off", "rolling_summary_every": "0",
                                 "scene_break_every": "0"})


def seeded_providers() -> set[str]:
    """The providers the ACTIVE store holds a KEY for: what an isolate's
    acceptance test compares with the configuration's selections. A built-in
    the store materialises on its own (the keyless subscription provider)
    carries no key, and so no way to spend."""
    out = set()
    for conn in llm_connections.list_connections():
        try:
            if llm_connections.read_connection_raw(conn["id"]).get("api_key"):
                out.add(conn["id"])
        except llm_connections.ConnectionNotFound:
            continue
    return out


# --- one run ---------------------------------------------------------------------


def build(fixture: Fixture) -> dict:
    """The fixture's scene in the ACTIVE store: Seraphine as the player,
    Mara and Winifred as NPCs, the fixture's posts. Directed order, no
    automatic rounds."""
    wid = worlds.create_world("Realm")
    wroot = worlds.world_root(wid)
    refs: dict[str, str] = {}
    cid = campaigns.create_campaign("Saltmarch", wid)
    croot = campaigns.campaign_root(cid)
    sid = scenes.create_scene(cid, "The Night Dock")
    for name in NPCS:
        npc, _ = characters.create_character(wroot, name, "default",
                                             characters.blank_card(name))
        appearances.appear(cid, sid, "characters", npc, "default", "npc", narrate=False)
        refs[name] = f"characters:{npc}"
    pid, _ = pcs.create_pc(croot, PLAYER, [], persona=pcs.blank_persona(PLAYER))
    appearances.appear(cid, sid, "pcs", pid, "default", "player", narrate=False)
    for role, speaker, content in fixture.posts:
        scenes.append_message(cid, sid, role, content, **({"speaker": speaker} if speaker
                                                          else {}))
    return {"cid": cid, "sid": sid, "refs": refs}


@dataclass
class PlayRun:
    """What one run of one fixture under one configuration did."""

    fixture: Fixture
    config: PlayConfig
    repeat: int
    cid: str = ""
    error: str = ""
    picks: list[dict] = field(default_factory=list)
    speakers: list[str] = field(default_factory=list)
    replies: list[dict] = field(default_factory=list)
    rows: tuple[dict, ...] = ()
    wall_ms: int = 0
    refs: dict[str, str] = field(default_factory=dict)
    #: The fixture's ids in the isolate (`build`): `cid`, `sid`, `refs`.
    ctx: dict = field(default_factory=dict)


def play(fixture: Fixture, conf: PlayConfig, *, client=None, repeat: int = 0,
         app_factory: Callable[[], Any] | None = None) -> tuple[PlayRun, dict]:
    """Send the fixture's post through the app in the ACTIVE store (seeded by
    the caller). Returns the run and the drain context `runner.drain` waits
    on (`app`, `shutdown`). `client` replaces the app's gateway (a test's
    fake); live, the app builds its own."""
    from fastapi.testclient import TestClient

    from grimoire import routes
    from grimoire.main import create_app

    ctx = build(fixture)
    app = (app_factory or create_app)()
    if client is not None:
        app.dependency_overrides[routes.get_llm] = lambda: client
    tc = TestClient(app)
    tc.__enter__()
    drain_ctx = {"app": app, "shutdown": lambda: tc.__exit__(None, None, None)}
    run = PlayRun(fixture, conf, repeat, cid=ctx["cid"], refs=ctx["refs"], ctx=ctx)
    started = time.monotonic()
    with _cutoff(conf):
        response = tc.post(f"/api/campaigns/{ctx['cid']}/scenes/{ctx['sid']}/chat",
                           json=fixture.send)
    run.wall_ms = int((time.monotonic() - started) * 1000)
    if response.status_code != 200 or '"error"' in response.text:
        run.error = f"status {response.status_code}" if response.status_code != 200 \
            else "the turn failed"
    return run, drain_ctx


@contextlib.contextmanager
def _cutoff(conf: PlayConfig) -> Iterator[None]:
    """The `cutoff: 0` arm (open question 9): the pick's eligibility with no
    cutoff while inside, restored after."""
    if conf.feature != "speaker" or conf.cutoff != "0":
        yield
        return
    original = rp.pick_eligibility

    def no_cutoff(offered, addressed_refs):
        return draws.Eligibility(exclude=(decisions.NONE_KEY,),
                                 only=tuple(addressed_refs) or None)

    rp.pick_eligibility = no_cutoff
    try:
        yield
    finally:
        rp.pick_eligibility = original


def collect(run: PlayRun) -> None:
    """Read what the run wrote -- the rounds' picks and speakers, and each
    contribution's text, intent and issue -- out of the isolate, after the
    drain."""
    cid, sid = run.ctx["cid"], run.ctx["sid"]
    scope = responses._scope(cid, sid, responses._read(cid))
    for rnd in scope["rounds"].values():
        if rnd.get("pick") is not None:
            run.picks.append(rnd["pick"])
        run.speakers.extend(rnd.get("used") or [])
    for record in scope["responses"].values():
        variant = next((v for v in record["variants"] if v["id"] == record["active_variant"]),
                       {})
        run.replies.append({"actor": record["actor_ref"], "text": variant.get("content", ""),
                            "intent": record.get("intent"), "issue": variant.get("issue"),
                            "tool": variant.get("tool")})


# --- the graders ---------------------------------------------------------------------


#: What the intent block says that a reply must never repeat (§6.6's leak
#: check): its fixed wording. A stance id alone is an ordinary English word
#: ("act", "press"), so it is looked for only as the block's own clause.
LEAK_PHRASES = ("approach in this reply", "Never name or explain the approach",
                "already knows, they")


def leaks(text: str) -> list[str]:
    """The intent block's wording a reply repeats: its fixed phrases, and
    any stance, disclosure or tension clause verbatim."""
    clauses = [*LEAK_PHRASES,
               *(turn_plan.stance_description(s) for s in turn_plan.STANCES),
               *(turn_plan.disclosure_description(d) for d in turn_plan.DISCLOSURES),
               *(turn_plan.tension_description(n) for n in range(len(turn_plan.TENSION_LEVELS)))]
    lowered = text.lower()
    return [c for c in clauses if c.lower() in lowered]


def drawn_weights(pick: dict) -> dict[str, float]:
    """The weights a pick record was actually drawn from: its reported
    distribution after its own eligibility (`only`, `exclude`, `cutoff`),
    normalised -- `draws.replay`'s reading. Empty for a record that drew
    nothing."""
    if pick.get("basis") != "sampled" or not pick.get("distribution"):
        return {}
    e = pick.get("eligibility") or {}
    only = e.get("only")
    exclude = set(e.get("exclude") or ())
    cutoff = float(e.get("cutoff") or 0.0)
    kept = {k: float(w) for k, w in pick["distribution"]
            if (only is None or k in only) and k not in exclude and float(w) >= cutoff}
    total = sum(kept.values())
    return {k: w / total for k, w in kept.items()} if total > 0 else {}


def mass_on_intended(pick: dict, intended_ref: str) -> float:
    """The probability the draw selects the intended speaker: exact over the
    drawn weights, or the answer's 0 or 1 when nothing was sampled (§5.7)."""
    weights = drawn_weights(pick)
    if weights:
        return weights.get(intended_ref, 0.0)
    return 1.0 if pick.get("selected") == intended_ref else 0.0


def run_checks(run: PlayRun) -> list[Check]:
    """The per-run checks: the turn checks every contribution keeps, and the
    feature's own (§5.7, §6.6)."""
    checks: list[Check] = []
    if run.error:
        return [Check("play.turn", False, run.error)]
    intended = (None if run.fixture.intended is None else
                run.refs.get(run.fixture.intended, run.fixture.intended))
    # Turn checks: the reply was written, spoke for nobody else, and handed
    # off validly.
    empty = [r["actor"] for r in run.replies if not r["text"].strip()]
    authority = [r["actor"] for r in run.replies
                 if r["issue"] == "response attempted to speak for another actor"]
    handoff = [r["actor"] for r in run.replies
               if r["issue"] in ("missing or invalid handoff", "ineligible or repeated speaker")]
    checks += [Check("turn.nonempty", not empty, f"empty reply from {empty}"),
               Check("turn.authority", not authority, f"spoke for another actor: {authority}"),
               Check("turn.handoff", not handoff, f"invalid handoff from {handoff}")]
    # Legality and no sampled abstention (§4.3 hard criteria 1, 2).
    illegal = [p["selected"] for p in run.picks
               if p.get("selected") is not None
               and (p["selected"] not in p.get("offered", ()) or p["selected"] == decisions.NONE_KEY)]
    checks.append(Check("gate.legal", not illegal, f"drew {illegal}, not offered"))
    sampled_none = [p for p in run.picks if p.get("basis") == "sampled" and p.get("answer") is None]
    stances = [r["intent"] for r in run.replies if r.get("intent")]
    sampled_none += [i for i in stances
                     if (i.get("draw") or {}).get("basis") == "sampled" and i.get("stance") is None]
    bad_stance = [i.get("stance") for i in stances
                  if i.get("stance") is not None and i["stance"] not in turn_plan.STANCES]
    # The decide tool's selections (§8.6): within the options the model
    # offered, and those within the shape's two to six.
    tools = [r["tool"] for r in run.replies if r.get("tool")]
    bad_tool = [t for t in tools if t.get("selected") is not None
                and (t["selected"] not in (t.get("options") or ())
                     or not 2 <= len(t.get("options") or ()) <= 6)]
    checks.append(Check("gate.legal_tool", not bad_tool,
                        f"{len(bad_tool)} tool selection(s) outside the options offered"))
    checks += [Check("gate.no_sampled_abstention", not sampled_none,
                     f"{len(sampled_none)} draw(s) over an answer of None"),
               Check("gate.legal_stance", not bad_stance, f"stances {bad_stance}")]
    # Hand-back preserved: a fixture whose intended answer is null.
    if run.fixture.intended is None:
        checks.append(Check("speaker.hand_back", not run.speakers,
                            f"a hand-back fixture was answered by {run.speakers}"))
    elif intended != ANY and run.picks:
        mass = mass_on_intended(run.picks[0], intended)
        checks.append(Check("speaker.mass_on_intended", True, f"n/a: {mass:.3f}"))
    # The leak check, on every reply.
    leaked = {r["actor"]: leaks(r["text"]) for r in run.replies if leaks(r["text"])}
    checks.append(Check("intent.leak", not leaked, f"the block's wording in {leaked}"))
    return checks


@dataclass(frozen=True)
class Graded:
    run: PlayRun
    checks: tuple[Check, ...]


def feature_rows(run: PlayRun) -> list[dict]:
    tasks = FEATURES[run.config.feature]["tasks"]
    return [row for row in run.rows if row.get("task") in tasks]


def gate(graded: Sequence[Graded]) -> list[Check]:
    """The hard criteria across runs (§4.3): legality and no sampled
    abstention on every run; no turn check that passes on every repeat with
    the feature off and fails on every repeat with it on; off files no row
    under the feature's task; on is really on (never skipped, and its task
    filed rows -- for the speaker draw, a pick on every round that picked)."""
    out: list[Check] = []
    every = [c for g in graded for c in g.checks if c.name.startswith("gate.")]
    for name in sorted({c.name for c in every}):
        bad = [c.detail for c in every if c.name == name and not c.ok]
        out.append(Check(name, not bad, "; ".join(bad[:3])))
    # Consistent turn-check regression, per fixture.
    regressions = []
    for fx in {g.run.fixture.id for g in graded}:
        off = [g for g in graded if g.run.fixture.id == fx and not g.run.config.on]
        on = [g for g in graded if g.run.fixture.id == fx and g.run.config.on]
        for name in {c.name for g in off + on for c in g.checks if c.name.startswith("turn.")}:
            passes_off = off and all(_ok(g, name) for g in off)
            fails_on = on and all(not _ok(g, name) for g in on)
            if passes_off and fails_on:
                regressions.append(f"{fx}: {name}")
    out.append(Check("gate.no_turn_regression", not regressions, "; ".join(regressions)))
    off_rows = [g.run.config.id for g in graded if not g.run.config.on and feature_rows(g.run)]
    out.append(Check("gate.off_is_free", not off_rows,
                     f"configs {sorted(set(off_rows))} filed rows under the feature's task"))
    not_on = sorted({g.run.config.id for g in graded if g.run.config.on and _not_on(g.run)})
    out.append(Check("gate.on_is_on", not not_on,
                     f"configs {not_on} skipped the feature or filed none of its calls"))
    return out


def _ok(g: Graded, name: str) -> bool:
    return all(c.ok for c in g.checks if c.name == name)


def _not_on(run: PlayRun) -> bool:
    """Whether a run labelled "on" did not run its feature: the turn decision
    skipped or filed no call, or -- for the speaker draw, which adds no call
    -- a pick was asked (a `response-selector` row) and no draw was stored."""
    if run.error:
        return False
    if run.config.feature == "speaker":
        picked = any(r.get("task") == "response-selector" for r in run.rows)
        return picked and not run.picks
    if run.config.feature == "tool":
        # On means offered to every NPC contribution; whether the model used
        # it is the call rate, reported, never a failure.
        npc = [r for r in run.replies if r.get("actor") != "grimoire"]
        return any(not (r.get("tool") or {}).get("offered") for r in npc)
    if run.config.feature in ("intent", "plan"):
        if run.fixture.send.get("director"):
            return False    # a typed note is the player's direction: never asked (§6.3)
        npc = [r for r in run.replies if r.get("actor") != "grimoire"]
        if any(not r.get("intent") or r["intent"].get("skipped") for r in npc):
            return True
        return bool(npc) and not feature_rows(run)
    return not feature_rows(run)


# --- what is reported for the user to judge ---------------------------------------


def latency(run: PlayRun) -> tuple[int, int]:
    """`(added pre-generation ms, generation ms)` for one run, summed from
    its rows' own durations: the speaker pick and the turn decisions before
    the first token, and the contributions."""
    pre = sum(int(r.get("duration_ms") or 0) for r in run.rows
              if r.get("task") in PRE_GENERATION_TASKS)
    gen = sum(int(r.get("duration_ms") or 0) for r in run.rows
              if r.get("task") in ("chat", "continuation"))
    return pre, gen


def summary(graded: Sequence[Graded]) -> list[str]:
    """Per configuration: added latency (median and maximum), stance
    diversity, speaker spread on open fixtures, departures and addressed
    overrides, and the mean mass on the intended speaker. Never a money
    figure: those are the run file's, per task and per column (01a-C1)."""
    lines = []
    for conf_id in sorted({g.run.config.id for g in graded}, key=lambda c: int(c[1:])):
        runs = [g.run for g in graded if g.run.config.id == conf_id]
        conf = runs[0].config
        pres = [latency(r)[0] for r in runs]
        stances = {i.get("stance") for r in runs for i in
                   (x.get("intent") for x in r.replies) if i and i.get("stance")}
        open_speakers = {s for r in runs if r.fixture.intended == ANY for s in r.speakers[:1]}
        picks = [p for r in runs for p in r.picks]
        departures = sum(1 for p in picks if p.get("selected") != p.get("answer"))
        overrides = sum(1 for p in picks if (p.get("eligibility") or {}).get("only")
                        and p.get("answer") not in (p["eligibility"]["only"] or ()))
        masses = [mass_on_intended(r.picks[0], r.refs.get(r.fixture.intended, ""))
                  for r in runs if r.picks and r.fixture.intended not in (None, ANY)]
        axes = " ".join(f"{k}={v}" for k, v in conf.axes().items())
        lines.append(f"{conf_id} {axes}")
        lines.append(f"  added pre-generation ms: median "
                     f"{statistics.median(pres) if pres else 0:.0f}, max {max(pres, default=0)}")
        lines.append(f"  stance diversity: {sorted(stances)}")
        lines.append(f"  open-fixture spread: {len(open_speakers)} distinct opener(s)")
        tools = [x["tool"] for r in runs for x in r.replies if x.get("tool")]
        offered = [t for t in tools if t.get("offered")]
        called = sum(1 for t in offered if t.get("called"))
        declined = sum(1 for t in offered if t.get("declined") == "after_text")
        if offered:
            lines.append(f"  tool calls: {called}/{len(offered)} offered; declined after "
                         f"text: {declined}/{len(offered)}")
        lines.append(f"  departures: {departures}/{len(picks)}; addressed overrides: "
                     f"{overrides}/{len(picks)}; mean mass on intended: "
                     f"{statistics.mean(masses) if masses else 0:.3f}")
    return lines


def adherence_item(reply: str) -> decisions.Item:
    """§6.6's adherence grader as a decision item: which stance does this
    reply take? The same options as the intent, asked of the generated reply
    by the Decision role -- a model judging a model, an approximate second
    opinion, reported as such and never a gate."""
    return decisions.Item(f"The reply: {reply}", (turn_plan.stance_question(),))


# --- the run ----------------------------------------------------------------------


class PlayRefusedError(RuntimeError):
    """A play run refused before anything was sent (a selection the real
    store does not have), or stopped because a run's follow-ups outlived the
    drain."""


def _case(feature: str, fixture: Fixture):
    """A play run as the report and the run file see a case: one per
    (feature, fixture), metered as the turn it plays."""
    from .cases import Case

    return Case(id=f"play-{feature}-{fixture.id}",
                hypothesis=f"the {feature} play decision on the {fixture.id} fixture",
                build=dict, prompt=lambda _ctx: [], grade=lambda _ctx, _out: [],
                recordings=(), task="chat")


def run_all(feature: str, configs: Sequence[PlayConfig], isolate, *, repeat: int,
            selected: Selected, real_home: Path, rates=None, client=None,
            fixtures: Sequence[Fixture] = FIXTURES, run_id: str = "",
            run_day: str = "") -> tuple[list, list[Graded]]:
    """Every (configuration, fixture, repeat) in its own `isolate()`: seeded
    (`seed`), played through the app (`play`), drained (`runner.drain`) and
    only then harvested, behind the tripwire before the fixture is built and
    again before the harvest. Returns the `runner.Result`s the report and the
    run file read, and the graded runs the gate reads. A run whose follow-ups
    outlive the drain stops the whole run (`PlayRefusedError`), as `live_all`
    does: a straggler would write into whatever home came next."""
    from grimoire.store import paths, usage

    from . import costs, runner

    run_id = run_id or str(uuid.uuid4())
    run_day = run_day or usage._today()
    results: list = []
    graded: list[Graded] = []
    for conf in configs:
        for fixture in fixtures:
            case = _case(feature, fixture)
            for n in range(repeat):
                with isolate():
                    if runner._is_real_home(real_home):
                        raise PlayRefusedError(runner.ISOLATE_ERROR)
                    seed(conf, selected)
                    run, drain_ctx = play(fixture, conf, client=client, repeat=n)
                    if not runner.drain(drain_ctx):
                        raise PlayRefusedError(
                            f"{case.id} [{conf.id} #{n + 1}]: follow-ups still running "
                            f"past {runner.DRAIN_CEILING_S}s; its isolate is kept at "
                            f"{paths.home()}")
                    if runner._is_real_home(real_home):
                        raise PlayRefusedError(runner.ISOLATE_ERROR)
                    run.rows = runner.harvest(run_day, run_id, case.id)
                    collect(run)
                checks = run_checks(run)
                graded.append(Graded(run, tuple(checks)))
                results.append(runner.Result(
                    case, conf.id, checks, " | ".join(r["text"] for r in run.replies),
                    error=run.error, error_kind="turn" if run.error else "",
                    rows=run.rows, wall_ms=run.wall_ms, bucket=costs.fold(run.rows, rates),
                    by_task=costs.by_task(run.rows, rates), rates=rates,
                    configs=(conf.id,), repeat=n))
    return results, graded


def report(graded: Sequence[Graded]) -> tuple[str, bool]:
    """The gate's verdict lines (§4.3's hard criteria) and the reported
    figures, and whether every hard criterion passed."""
    hard = gate(graded)
    lines = ["play gate:"]
    lines += [f"  [{'ok  ' if c.ok else 'FAIL'}] {c.name}" + (f": {c.detail}" if c.detail
                                                              and not c.ok else "")
              for c in hard]
    lines += ["", "reported (for you to judge):", *(f"  {line}" for line in summary(graded))]
    return "\n".join(lines), all(c.ok for c in hard)
