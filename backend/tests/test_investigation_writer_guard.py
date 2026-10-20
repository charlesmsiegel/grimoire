"""Roadmap 12's write posture, held by the AST (spec 12-C3, section 12).

An investigation reads a campaign through its tools and writes at most one
thing: one proposal on one continuity candidate, through
`continuity.reconcile.persist_investigation`, called from
`routes/investigation.py` and nowhere else. Nothing under
`store/investigation/` and nothing in that route module may reach a mutator of
the stores it reads -- the ledger, the transcript, knowledge overrides (11-C3),
entities, play state, dossiers or the chronicle.

The scan is an allowlist per watched module rather than a list of mutators:
a module's mutator added tomorrow is caught without anyone remembering to name
it here, and a reader the package starts to use is one line below. Bindings
are resolved as `test_continuity_writer_guard` resolves them (every load, not
only calls, so a mutator handed to a fail-soft helper is seen too).
"""

import ast
import importlib
import pathlib

import grimoire

from .test_continuity_writer_guard import _references

PACKAGE_ROOT = pathlib.Path(grimoire.__file__).parent
STORE_PACKAGE = "grimoire.store.investigation"
ROUTE_MODULE = "routes/investigation.py"

#: What may be named of each watched module: readers, exception types and
#: constants. Anything else of these modules is a write.
ALLOWED: dict[str, frozenset[str]] = {
    "grimoire.store.plot": frozenset({"read", "get", "open_threads"}),
    "grimoire.store.commitments": frozenset({"read", "get", "open_commitments"}),
    "grimoire.store.facts": frozenset({"read", "get", "active", "retired"}),
    "grimoire.store.events": frozenset({"read", "get", "list_events"}),
    "grimoire.store.relationships": frozenset({"read", "actor_name", "feeling_key"}),
    "grimoire.store.continuity.doc": frozenset({"read"}),
    "grimoire.store.continuity.review": frozenset(),
    "grimoire.store.continuity.candidates": frozenset({"read", "records", "malformed",
                                                       "investigation_trace",
                                                       "TRACE_VERSION"}),
    "grimoire.store.knowledge": frozenset({"read", "read_checked"}),
    "grimoire.store.scenes.write": frozenset(),
    "grimoire.store.entities": frozenset({"normalize_secrecy", "EntityNotFound", "UnknownKind",
                                          "GM_ONLY", "PUBLIC", "SECRET", "ENTITY_KINDS"}),
    "grimoire.store.playstate": frozenset({"read_state"}),
    "grimoire.store.dossiers": frozenset({"read"}),
    "grimoire.store.chronicle": frozenset({"read_chronicle", "get_record"}),
}

#: The one write, and the one module allowed to make it.
PERSIST = ("grimoire.store.continuity.reconcile", "persist_investigation")
#: Reconcile's other writers, never reachable from an investigation.
RECONCILE_WRITES = frozenset({"persist_found", "persist_proposals", "_commit"})
# Both the compatible facade and its sole persistence owner expose the writers.
PERSIST_MODULES = frozenset({PERSIST[0], PERSIST[0] + "._persistence"})


def _scanned() -> list[tuple[str, pathlib.Path, str]]:
    """(label, file, package its relative imports resolve against)."""
    store = PACKAGE_ROOT / "store" / "investigation"
    out = [(f"store/investigation/{p.name}", p, STORE_PACKAGE) for p in sorted(store.glob("*.py"))]
    route = PACKAGE_ROOT / ROUTE_MODULE
    if route.is_file():
        out.append((ROUTE_MODULE, route, "grimoire.routes"))
    return out


def problems(src: str, package: str, *, may_persist: bool) -> list[str]:
    out = []
    for line, module, name, rendered in _references(ast.parse(src), package):
        if (module in ALLOWED and name not in ALLOWED[module]) or (module in PERSIST_MODULES and (name in RECONCILE_WRITES
                                       or (name == PERSIST[1] and not may_persist))):
            out.append(f"{line}: {rendered}")
    return out


def test_no_investigation_code_writes():
    offenders = []
    for label, path, package in _scanned():
        found = problems(path.read_text(encoding="utf-8"), package,
                         may_persist=label == ROUTE_MODULE)
        offenders += [f"{label}:{p}" for p in found]
    assert not offenders, (
        "an investigation reads, and writes only one proposal through "
        "`reconcile.persist_investigation` from routes/investigation.py:\n  "
        + "\n  ".join(offenders))


def test_the_package_is_scanned():
    labels = {label for label, _p, _pkg in _scanned()}
    assert {"store/investigation/tools.py", "store/investigation/readers.py",
            "store/investigation/final.py"} <= labels


def test_the_allowlist_names_things_that_exist():
    missing = []
    for module, names in sorted(ALLOWED.items()):
        mod = importlib.import_module(module)
        missing += [f"{module}.{n}" for n in sorted(names) if not hasattr(mod, n)]
    reconcile = importlib.import_module(PERSIST[0])
    missing += [n for n in (PERSIST[1], *RECONCILE_WRITES) if not hasattr(reconcile, n)]
    assert not missing, f"the guard names things that no longer exist: {missing}"


_INVESTIGATION = "grimoire.store.investigation"


def test_the_guard_catches_a_planted_write():
    assert problems("from .. import facts\ndef go(c):\n    facts.record(c, 't', '', 's')\n",
                    _INVESTIGATION, may_persist=False) == ["3: facts.record"]
    assert problems("from ..continuity import review\n"
                    "def go(c):\n    _soft(review.dismiss, None, c, 'k')\n",
                    _INVESTIGATION, may_persist=False) == ["3: review.dismiss"]
    assert problems("from ..scenes import write\ndef go(c):\n    write.set_excluded(c, 's', 1, True)\n",
                    _INVESTIGATION, may_persist=False) == ["3: write.set_excluded"]
    assert problems("from ..knowledge import grant as g\ndef go(c):\n    g(c)\n",
                    _INVESTIGATION, may_persist=False) == ["3: g"]
    persist = ("from ..store.continuity import reconcile\n"
               "def go(c):\n    reconcile.persist_investigation(c, 'k', {})\n")
    assert problems(persist, "grimoire.routes", may_persist=False) \
        == ["3: reconcile.persist_investigation"]
    assert problems(persist, "grimoire.routes", may_persist=True) == []
    assert problems("from ..store.continuity import reconcile\n"
                    "def go(c, s):\n    reconcile.persist_proposals(c, s, {})\n",
                    "grimoire.routes", may_persist=True) \
        == ["3: reconcile.persist_proposals"]


def test_the_guard_passes_a_planted_reader():
    assert problems("from .. import facts, events\n"
                    "def go(c):\n    facts.get(c, 'f')\n    events.list_events(c)\n",
                    _INVESTIGATION, may_persist=False) == []


def test_a_relocated_persistence_writer_still_requires_the_route():
    source = ("from ..continuity import reconcile\n"
              "from ..continuity.reconcile import _persistence\n"
              "def go(c):\n    _persistence.persist_investigation(c, 'k', {})\n")
    assert problems(source, _INVESTIGATION, may_persist=False)
    assert problems(source, _INVESTIGATION, may_persist=True) == []
    assert problems(source.replace("persist_investigation", "persist_proposals"),
                    _INVESTIGATION, may_persist=True)
