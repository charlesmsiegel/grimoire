"""The overview benchmark (roadmap 04-C3b): the four overview reads on a
synthetic library, per scenario, with counters and timings and nothing else.

    python -m scripts.bench_overview --library DIR [--repeat N] [--json OUT]
                                     [--register shipped|none|all|KIND[,KIND]]
                                     [--clocks injected|wait]

Run from backend/. `DIR` must be a library `scripts/synth_library.py
--profile` built: this refuses a directory without its `.synthetic-library`
marker, and refuses the default store (`~/.grimoire`), the store the bootstrap
pointer names, the `GRIMOIRE_HOME` it inherited, and any path inside any of
them. It reads only the pointer's path value, never the store. Every scenario
runs on a temporary copy of `DIR`, so the library itself is never written.

The output holds endpoint names, scenario names, counters and timings, and
nothing else -- no name, id or path, even though the library is synthetic. One
output shape for every library is how a later edit cannot start printing
names. No timing figure is committed anywhere (spec 04 Open question 10).

Clocks: spec 03 records a `sources` row only for a file older than
`PERSIST_WINDOW_NS`, and statcache's in-process memos refuse a stamp younger
than `RACY_WINDOW_NS`. POSIX ctime cannot be backdated, so a fresh copy is
"young". `--clocks injected` (the default) moves both clocks forward, as the
counter assertions in `make check-py` do; `--clocks wait` sleeps the window
out after each copy, measuring real time throughout, and says so.

`--register` chooses which overview kinds are in the compiled registry for the
run: `shipped` (whatever `compiled.KINDS` holds), `none`, `all`, or a comma
list. Spec 03 section 14's rule -- a kind that does not win warm-after-restart
is not wired -- is decided by comparing those runs.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import statistics
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

ENDPOINTS = ("worlds", "campaigns", "todo", "todo_campaign", "shell_campaign")
SCENARIOS = ("cold", "warm_same_process", "warm_after_restart", "relevant_change",
             "relevant_change_ledger", "unrelated_change", "one_campaign_in_large",
             "visit_after_turn", "cache_off")


# ---- the privacy guard -------------------------------------------------------

def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def guard(library: Path, *, inherited_home: str | None) -> Path:
    """`library`, resolved, or ValueError: unmarked, or the default store, the
    pointer's store or the inherited `GRIMOIRE_HOME`, or inside one."""
    from grimoire.store import paths
    from scripts import synth_library
    resolved = Path(library).expanduser().resolve()
    if not (resolved / synth_library.SYNTHETIC_MARKER).is_file():
        raise ValueError("not a synthetic library: its marker is missing")
    real_roots = [paths.DEFAULT_HOME]
    pointer = paths._parse_pointer(paths.pointer_path()) or {}
    if pointer.get("data_dir"):
        real_roots.append(Path(str(pointer["data_dir"])).expanduser())
    if inherited_home:
        real_roots.append(Path(inherited_home).expanduser())
    for real in real_roots:
        root = real.resolve()
        if resolved == root or _inside(resolved, root) or _inside(root, resolved):
            raise ValueError("refusing a library that is, holds or sits inside a real store")
    _no_links(resolved)
    return resolved


def _no_links(library: Path) -> None:
    """Refuse a library holding any link or reparse point. The generator never
    makes one, so one inside a marked directory was put there -- and a copy
    would carry it, letting the requests read whatever it points at."""
    for directory, dirs, files in os.walk(library, followlinks=False):
        for name in (*dirs, *files):
            path = os.path.join(directory, name)
            info = os.lstat(path)
            if os.path.islink(path) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("refusing a library that holds a link")
        for name in files:
            # A hard link is a second name for bytes that live elsewhere too:
            # `copytree` would copy them, and a request would read them.
            if os.lstat(os.path.join(directory, name)).st_nlink > 1:
                raise ValueError("refusing a library that holds a hard link")


# ---- what a request read (harness-only counts) ---------------------------------

_audit_lock = threading.Lock()
_audit: list[list[int]] = []
_audit_installed = False


def _hook(event: str, args: tuple) -> None:
    if not _audit or event not in ("open", "os.listdir", "os.scandir"):
        return
    with _audit_lock:
        for counts in _audit:
            counts[0 if event == "open" else 1] += 1


@contextlib.contextmanager
def _audited() -> Iterator[list[int]]:
    global _audit_installed  # noqa: PLW0603 -- one process-wide hook
    if not _audit_installed:
        sys.addaudithook(_hook)
        _audit_installed = True
    counts = [0, 0]
    with _audit_lock:
        _audit.append(counts)
    try:
        yield counts
    finally:
        with _audit_lock:
            _audit.remove(counts)


# ---- the environment each scenario runs in -----------------------------------

class Bench:
    """One working copy of the library, and the switches a scenario flips."""

    def __init__(self, library: Path, *, register: str, clocks: str) -> None:
        self.library = library
        self.register = register
        self.clocks = clocks
        self.work: Path | None = None
        self._undo: list[Callable[[], None]] = []
        self._client = None

    # -- setup
    def __enter__(self) -> Bench:
        from grimoire.store import compiled, statcache
        self._tmp = tempfile.TemporaryDirectory(prefix="grimoire-bench-")
        self.work = Path(self._tmp.name) / "library"
        shutil.copytree(self.library, self.work, symlinks=True)
        self._set_env("GRIMOIRE_HOME", str(self.work))
        self._set_env("GRIMOIRE_COMPILED_CACHE", None)
        if self.register != "shipped":
            chosen = (set() if self.register == "none" else set(compiled.OVERVIEW_CANDIDATES)
                      if self.register == "all" else set(self.register.split(",")))
            unknown = chosen - set(compiled.OVERVIEW_CANDIDATES)
            if unknown:
                raise ValueError(f"unknown overview kinds: {sorted(unknown)}")
            kinds = {k: v for k, v in compiled.KINDS.items() if k not in compiled.OVERVIEW_CANDIDATES}
            kinds.update({k: compiled.OVERVIEW_CANDIDATES[k] for k in chosen})
            self._swap(compiled, "KINDS", kinds)
            # A kind registered for measurement is measured at every size.
            from grimoire.store.overview import read as overview_read
            self._swap(overview_read, "SCENE_TURNS_PERSIST_MIN_BYTES", 0)
        if self.clocks == "injected":
            ahead = 60 * 1_000_000_000
            real_clock = compiled.filesystem_clock

            def forward(root):
                clock = real_clock(root)
                return None if clock is None else compiled.FilesystemClock(clock.now_ns + ahead, clock.device)
            self._swap(compiled, "filesystem_clock", forward)
            self._swap(statcache, "time", _AheadTime(ahead))
        self._largest = self._find_largest()
        self.restart()
        return self

    def __exit__(self, *exc) -> None:
        from grimoire.store import compiled
        self._close_client()
        compiled.close()
        for undo in reversed(self._undo):
            undo()
        self._tmp.cleanup()

    def _set_env(self, key: str, value: str | None) -> None:
        old = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
        self._undo.append(lambda: os.environ.__setitem__(key, old) if old is not None
                          else os.environ.pop(key, None))

    def _swap(self, owner, name: str, value) -> None:
        old = getattr(owner, name)
        setattr(owner, name, value)
        self._undo.append(lambda: setattr(owner, name, old))

    def settle(self) -> None:
        """With `--clocks wait`, sleep out both trust windows."""
        if self.clocks == "wait":
            from grimoire.store import compiled, statcache
            time.sleep((max(compiled.PERSIST_WINDOW_NS, statcache.RACY_WINDOW_NS) + 1_000_000_000) / 1e9)

    # -- the app
    def client(self):
        if self._client is None:
            from fastapi.testclient import TestClient

            from grimoire.main import create_app
            self._client = TestClient(create_app())
            self._client.__enter__()
            # One request that reads no overview, so what is measured next is
            # the read and not the app's first-request start-up.
            self._client.get("/api/config")
        return self._client

    def _close_client(self) -> None:
        if self._client is not None:
            self._client.__exit__(None, None, None)
            self._client = None

    def restart(self) -> None:
        """A new app with every in-process memo cleared; `.cache/compiled` kept."""
        from grimoire.store import characters, compiled, statcache
        from grimoire.store.overview import read as overview_read
        from grimoire.store.scenes import read as scenes_read
        self._close_client()
        compiled.close()
        for pool in (statcache._cache, scenes_read._SCENE_POOL, characters._IDENTITY_POOL,
                     overview_read._OVERVIEW_POOL, overview_read._MEMBER_STAMPS):
            pool.clear()

    def cold(self) -> None:
        self.restart()
        assert self.work is not None
        shutil.rmtree(self.work / ".cache" / "compiled", ignore_errors=True)

    def cache_off(self) -> None:
        self._set_env("GRIMOIRE_COMPILED_CACHE", "0")
        self.restart()

    # -- what the library holds, by shape rather than name
    @staticmethod
    def _find_largest() -> str:
        """The campaign with the most scenes, read through the store's own
        listings (never the overview, whose memos are what is measured);
        `restart` then clears every pool this touched."""
        from grimoire.store import campaigns, scenes
        rows = campaigns.read.list_campaigns()
        return max(rows, key=lambda c: (len(scenes.read.list_scenes(c["id"])), c["id"]))["id"]

    def largest_campaign(self) -> str:
        return self._largest

    def url(self, endpoint: str) -> str:
        largest = self.largest_campaign()
        return {"worlds": "/api/worlds", "campaigns": "/api/campaigns", "todo": "/api/todo",
                "todo_campaign": f"/api/todo?campaign={largest}",
                "shell_campaign": f"/api/shell?campaign={largest}"}[endpoint]

    def get(self, endpoint: str) -> dict:
        """One request, measured: wall ms, the counters, and the opens and
        listings it made."""
        from grimoire.store import readstats
        url = self.url(endpoint)
        with readstats.collect() as counters, _audited() as audit:
            started = time.perf_counter()
            response = self.client().get(url)
            elapsed = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise RuntimeError(f"{endpoint} answered {response.status_code}")
        return {"ms": elapsed, "counters": counters.snapshot(),
                "opened": audit[0], "listed": audit[1]}

    def warm(self) -> None:
        for endpoint in ENDPOINTS:
            self.get(endpoint)

    # -- the changes
    def _open_scene(self, cid: str) -> str:
        from grimoire.store import overview
        summary = overview.scene_summary(cid)
        return (summary["open"] or summary["rows"])[0]["sid"]

    def append_post(self, cid: str | None = None) -> None:
        from grimoire.store import scenes
        cid = cid or self.largest_campaign()
        scenes.append_message(cid, self._open_scene(cid), "assistant", "Salt and lamps.",
                              speaker="Mara")

    def edit_commitments(self, cid: str | None = None) -> None:
        from grimoire.store import commitments
        cid = cid or self.largest_campaign()
        commitments.set_movement(cid, "oath-0", "", "", "advanced", None, "Gulls over the quay.",
                                 self._open_scene(cid))

    def edit_lore(self) -> None:
        from grimoire.store import entities, worlds
        wid = worlds.read.list_world_rows()[0]["id"]
        root = worlds.world_root(wid)
        listed = entities.list_entities(root, "lore")
        if listed:
            entities.update_entity(root, "lore", listed[0]["id"], body="Fog over the reeds.")
        else:
            entities.create_entity(root, "lore", "Saltmarch Fog", "Fog over the reeds.")

    def turn(self) -> None:
        """One chat turn through the route, with the fake LLM, its follow-up
        warm included."""
        from grimoire import routes
        from grimoire.store import scenes
        from tests.llm_fakes import decision_reply, from_entries
        cid = self.largest_campaign()
        sid = self._open_scene(cid)
        client = self.client()
        client.put("/api/llm-connections/openrouter", json={"api_key": "sk-bench"})
        # The copy's Primary, as a user would set it; the fake answers it.
        from grimoire.store import config, inference_keys
        config.write_config(**{inference_keys.role_key("primary", "provider"): "openrouter",
                               inference_keys.role_key("primary", "model"): config.DEFAULT_MODEL})
        fake = from_entries([
            {"when": {"system_contains": "keeping a running summary"}, "reply": "Salt."},
            {"when": {"system_contains": "You answer closed questions"},
             "reply": decision_reply({"over": False}, rationales=["Mid-scene."])},
            {"when": {}, "reply": "The lamps are lit."},
        ], "bench-overview")
        client.app.dependency_overrides[routes.get_llm] = lambda: fake
        response = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "Onward."})
        if response.status_code != 200:
            raise RuntimeError(f"the turn answered {response.status_code}: {response.text[:300]}")
        subject = ("scene", cid, scenes.scene_identity(cid, sid))
        for run in client.app.state.runs.for_subject(subject):
            run.terminal.wait(timeout=30.0)


class _AheadTime:
    """statcache's `time`, `ahead` nanoseconds in the future."""

    def __init__(self, ahead: int) -> None:
        self.ahead = ahead

    def time_ns(self) -> int:
        return time.time_ns() + self.ahead


# ---- the scenarios -------------------------------------------------------------

def _scenario(bench: Bench, name: str, endpoint: str) -> dict:
    """Put `bench` in `name`'s state for `endpoint`, then measure one request."""
    if name in ("cold", "cache_off"):
        if name == "cache_off":
            bench.cache_off()
        else:
            bench.cold()
        return bench.get(endpoint)
    bench.cold()
    bench.settle()              # past both trust windows, so the first read remembers
    bench.warm()
    if name == "warm_same_process":
        return bench.get(endpoint)              # the second request
    if name == "warm_after_restart":
        bench.restart()         # `sources` and artifacts kept, memory cleared
        return bench.get(endpoint)
    if name == "relevant_change":               # a post appended to one scene
        bench.append_post()
        return bench.get(endpoint)
    if name == "relevant_change_ledger":        # separately: that campaign's ledger
        bench.edit_commitments()
        return bench.get(endpoint)
    if name == "one_campaign_in_large":
        bench.append_post()
        bench.edit_commitments()
        return bench.get(endpoint)
    if name == "unrelated_change":
        bench.edit_lore()
        return bench.get(endpoint)
    if name == "visit_after_turn":
        bench.turn()            # the client remembers nothing after a write (spec 6.3)
        return bench.get(endpoint)
    raise ValueError(f"unknown scenario: {name}")


def measure(library: Path, *, repeat: int = 3, register: str = "shipped", clocks: str = "injected",
            scenarios: tuple[str, ...] = SCENARIOS, endpoints: tuple[str, ...] = ENDPOINTS) -> dict:
    """Median wall time per scenario and endpoint over `repeat` runs, the last
    run's counters, and its opens and listings. Each (scenario, endpoint,
    repeat) runs on its own fresh copy of the library."""
    inherited = os.environ.get("GRIMOIRE_HOME")
    library = guard(library, inherited_home=inherited)
    out: dict[str, dict] = {}
    for name in scenarios:
        out[name] = {}
        for endpoint in endpoints:
            samples, last = [], {}
            for _ in range(repeat):
                with Bench(library, register=register, clocks=clocks) as bench:
                    last = _scenario(bench, name, endpoint)
                samples.append(last["ms"])
            out[name][endpoint] = {"median_ms": round(statistics.median(samples), 3),
                                   "counters": last["counters"],
                                   "opened": last["opened"], "listed": last["listed"]}
    return {"schema": 1, "register": register, "clocks": clocks, "repeat": repeat, "scenarios": out}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--json", type=Path, dest="out")
    parser.add_argument("--register", default="shipped")
    parser.add_argument("--clocks", choices=("injected", "wait"), default="injected")
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    options = parser.parse_args()
    if options.repeat < 1:
        parser.error("--repeat must be at least 1")
    try:
        result = measure(options.library, repeat=options.repeat, register=options.register,
                         clocks=options.clocks, scenarios=tuple(options.scenario or SCENARIOS))
    except ValueError as error:
        parser.error(str(error))
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if options.out:
        options.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
