"""Which grimoire packages are pure, and what "pure" allows.

A pure package computes. It does not touch files, the network, processes,
threads, the environment, clocks or randomness, it does not load code
dynamically, and it depends on no provider, adapter, test harness or
third-party package. Pure standard-library computation (``re``,
``unicodedata``, ``hashlib`` ...) is fine.

The rule is an allowlist on purpose. Audit hooks cannot see ``stat``, clocks,
randomness or environment reads, so a module that is not listed here is not
trusted to be pure. Widening the list is a reviewed one-line change:
add a module only when its purpose is computation.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
GRIMOIRE = SRC / "grimoire"

#: Standard-library modules whose purpose is computation.
PURE_STDLIB = frozenset({
    "__future__", "abc", "binascii", "bisect", "collections", "copy", "dataclasses", "decimal", "difflib",
    "enum", "fractions", "functools", "hashlib", "heapq", "itertools", "math", "numbers",
    "operator", "re", "string", "textwrap", "types", "typing", "unicodedata",
})

#: Builtins that read input, write output, run code or reach interpreter state.
BANNED_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "input", "print", "breakpoint",
    "globals", "locals", "vars", "__builtins__", "builtins", "__loader__", "__spec__",
})

#: Attributes that open files, import modules or escape to interpreter internals.
#: ``compile`` is deliberately absent: ``re.compile`` is pure.
BANNED_ATTRIBUTES = frozenset({
    "open", "import_module", "__globals__", "__builtins__", "__subclasses__", "__loader__",
    "exec", "eval", "__import__", "input", "breakpoint",
})
#: Modules a pure module happens to hold (``dataclasses.inspect.os``, ``typing.sys``).
#: Reaching them from an imported name is banned; ``self.os`` or ``config.modules`` is not.
MODULE_ROUTES = frozenset({"os", "sys", "inspect", "importlib", "builtins", "bltns", "modules",
                           "__dict__"})
#: Reflection that reaches an attribute by its name.
REFLECTION = frozenset({"getattr", "setattr", "delattr", "hasattr"})


def _impure_module_attribute(module_name: str, attr: str) -> bool:
    """Whether `module_name.attr` is a module outside the pure allowlist (``enum.bltns``)."""
    import importlib
    import types
    try:
        value = getattr(importlib.import_module(module_name), attr, None)
    except ImportError:
        return False
    return isinstance(value, types.ModuleType) and value.__name__.split(".")[0] not in PURE_STDLIB

#: Modules nothing in a pure package may pull in, even transitively -- its file
#: edge included (whose ``importlib.resources`` legitimately loads ``tempfile``).
NETWORK_MODULES = ("socket", "ssl", "select", "selectors", "http.client", "urllib.request",
                   "subprocess", "multiprocessing", "asyncio", "ctypes", "sqlite3",
                   "grimoire.capture", "grimoire.ledgers", "grimoire.library", "grimoire.peersync",
                   "grimoire.providers", "grimoire.store", "grimoire.sync", "grimoire.testing")
#: ...and what its pure modules must not pull in either.
CAPABILITY_MODULES = NETWORK_MODULES + ("threading", "tempfile", "shutil", "glob", "random", "time",
                                         "datetime", "uuid", "secrets")

#: grimoire packages that are adapters or test infrastructure, not pure.
ADAPTERS = frozenset({"grimoire.adjudicationstore", "grimoire.app", "grimoire.archaeologystore", "grimoire.campaignapi", "grimoire.capture", "grimoire.cardstore", "grimoire.changesetstore", "grimoire.chatstore", "grimoire.contextstore", "grimoire.continuitystore", "grimoire.continuitywire", "grimoire.decisionstore", "grimoire.devicetag", "grimoire.effective", "grimoire.effectstore",
                      "grimoire.evidencestore", "grimoire.graphstore", "grimoire.holdingstore", "grimoire.imagestore",
                      "grimoire.import_v1", "grimoire.ledgers", "grimoire.library", "grimoire.mediaapi", "grimoire.migrate_v1",
                      "grimoire.obstaclestore", "grimoire.partystore", "grimoire.peersync", "grimoire.possessionstore", "grimoire.providers",
                      "grimoire.play", "grimoire.quickstart", "grimoire.rebasestore", "grimoire.reviewapi",
                      "grimoire.runtime", "grimoire.scenestore", "grimoire.shelfstore", "grimoire.sourceapi",
                      "grimoire.store", "grimoire.sync", "grimoire.testing", "grimoire.textapi"})


@dataclass(frozen=True)
class PurePackage:
    name: str
    #: grimoire packages its modules may import. Widen deliberately.
    may_import: tuple[str, ...]
    #: grimoire packages it must not import even though they fall under `may_import`.
    never_import: tuple[str, ...] = ()
    #: modules (paths relative to the package) that are its file-reading edge.
    io_modules: frozenset[str] = frozenset()
    #: standard-library modules (dotted prefixes) only those edge modules may add.
    io_stdlib: frozenset[str] = frozenset()
    #: builtins only those edge modules may use (a CLI prints; a loader opens).
    io_names: frozenset[str] = frozenset()

    @property
    def directory(self) -> Path:
        return SRC.joinpath(*self.name.split("."))


PURE_PACKAGES = (
    # The fiction domain. `core.inference` is a separate vocabulary that core does not import.
    PurePackage("grimoire.core", ("grimoire.core",), never_import=("grimoire.core.inference",)),
    PurePackage("grimoire.core.inference", ("grimoire.core.inference",)),
    PurePackage("grimoire.intent", ("grimoire.intent",)),
    # Pure continuity graph and provenance values; native JSON/envelope codecs live in continuitywire.
    PurePackage("grimoire.continuity", ("grimoire.continuity", "grimoire.core", "grimoire.scene"),
                never_import=("grimoire.core.inference",)),
    # World-optional chats (107): values, codec and navigation; the store-backed service is an adapter.
    PurePackage("grimoire.chats", ("grimoire.chats", "grimoire.core"), never_import=("grimoire.core.inference",)),
    # Reviewed source rebase and chat-canon definitions (042): field review only; the store-backed service is an adapter.
    PurePackage("grimoire.rebase", ("grimoire.rebase", "grimoire.core"), never_import=("grimoire.core.inference",)),
    # Character, scenario and lorebook cards (037): parse decoded documents, map to records; the importer is an adapter.
    # Its lore readers build 031's ``LoreEntry`` values, and 031 imports nothing of 037's: one direction.
    # It reads 018's slot vocabulary (``grimoire.images``) to name the slots card art fills (038).
    PurePackage("grimoire.cards", ("grimoire.cards", "grimoire.core", "grimoire.context", "grimoire.images"),
                never_import=("grimoire.core.inference",)),
    # Typed relationship graph (017): values, codec, derived index and actor claims; the service is an adapter.
    PurePackage("grimoire.graph", ("grimoire.graph", "grimoire.core"), never_import=("grimoire.core.inference",)),
    # Party membership and scene casts (033): pure state over 017 relations; the service is an adapter.
    PurePackage("grimoire.party", ("grimoire.party", "grimoire.graph", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Stored events and evidence (020): codec and assembly; the store-backed service is an adapter.
    # It reads 017's claim-shape value type, stored with the propositions it shapes.
    PurePackage("grimoire.evidence", ("grimoire.evidence", "grimoire.graph", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Accepted turn effects (021): values, codecs, authority, planning and checks; the service is an adapter.
    # Its holdings part is a 023 operation's pure lines, so it may use the pure holdings package.
    PurePackage("grimoire.effects", ("grimoire.effects", "grimoire.graph", "grimoire.holdings", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Image identity and placements (018): values, codec and resolver; storage is an adapter.
    PurePackage("grimoire.images", ("grimoire.images", "grimoire.core"), never_import=("grimoire.core.inference",)),
    # The ledger merge (028): merges two devices' 021 events, 017 relations and 023 holder files with their
    # pure planners and codecs; sync wiring and the owner's review are adapters, later.
    PurePackage("grimoire.ledgermerge", ("grimoire.ledgermerge", "grimoire.effects", "grimoire.graph",
                                          "grimoire.holdings", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Counted holdings (023): values, codec and the deterministic mutator; the service is an adapter.
    PurePackage("grimoire.holdings", ("grimoire.holdings", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Unique possessions (034): values, record codec and the deterministic mutator; the service is an adapter.
    PurePackage("grimoire.possessions", ("grimoire.possessions", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Fiction-first adjudication (029): frames, assessment, decisions and codecs; the service is an adapter.
    PurePackage("grimoire.adjudication", ("grimoire.adjudication", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Open obstacles (040): obstacles, the frame rules, codecs and derivation over 029's values;
    # the service is an adapter. A consequence's authored changes are 017 predicates and 023 lines.
    PurePackage("grimoire.obstacles", ("grimoire.obstacles", "grimoire.adjudication", "grimoire.core",
                                       "grimoire.graph", "grimoire.holdings"),
                never_import=("grimoire.core.inference",)),
    PurePackage("grimoire.security", ("grimoire.security",)),
    # Tags, pins and saved filters: values, tag rules and codec; the files are the adapter ``grimoire.shelfstore``.
    PurePackage("grimoire.shelf", ("grimoire.shelf", "grimoire.core"), never_import=("grimoire.core.inference",)),
    # World Change Sets (043): values, codec and diffs; reviewing and applying is an adapter.
    PurePackage("grimoire.changesets", ("grimoire.changesets", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Context packing (031): audience rules, triggering and the packer; the store join is an adapter.
    # It reads 017's claim shapes through ``claims_about``, never the director's graph.
    PurePackage("grimoire.context", ("grimoire.context", "grimoire.graph", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Diegetic communication (032): who an in-scene message reaches; reads 008's routed intents
    # and 020's storage limits.
    PurePackage("grimoire.comms", ("grimoire.comms", "grimoire.core", "grimoire.intent", "grimoire.evidence"),
                never_import=("grimoire.core.inference",)),
    # Scene staging (041): who and what is where in one scene; builds 032 presences and 012 perceptions.
    PurePackage("grimoire.staging", ("grimoire.staging", "grimoire.core", "grimoire.comms", "grimoire.evidence",
                                      "grimoire.party"),
                never_import=("grimoire.core.inference",)),
    # Narration continuity referee (047): checks labelled claims against 041, 023, 034, 012, 029 and 040 values.
    PurePackage("grimoire.referee", ("grimoire.referee", "grimoire.core", "grimoire.staging", "grimoire.comms",
                                      "grimoire.holdings", "grimoire.possessions", "grimoire.obstacles",
                                      "grimoire.goals"),
                never_import=("grimoire.core.inference",)),
    # Voice evidence: shares the gate's text matcher so screening and scoring agree.
    PurePackage("grimoire.voice", ("grimoire.voice", "grimoire.core", "grimoire.evals.text",
                                   "grimoire.evals.checks"),
                never_import=("grimoire.core.inference",)),
    # NPC goals (039): authored agendas and the deterministic policy that chooses from the NPC's own
    # knowledge. It calls no model, so it never touches the inference contracts.
    PurePackage("grimoire.goals", ("grimoire.goals", "grimoire.core"), never_import=("grimoire.core.inference",)),
    # Recorded NPC decisions (045): the record of a 039 choice, its evidence and codec, and replay; the
    # ledger is an adapter. It calls no model, so it never touches the inference contracts.
    PurePackage("grimoire.decisions", ("grimoire.decisions", "grimoire.goals", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Transcripts (036): stored pages of posts and variants; SillyTavern chat reading over decoded lines.
    PurePackage("grimoire.transcripts", ("grimoire.transcripts", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    PurePackage("grimoire.archaeology", ("grimoire.archaeology", "grimoire.transcripts", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Per-NPC generation (027): plans, voices, briefs and screens each call; the runtime is an adapter.
    # Briefs render 031's packs and 039's choices (their value types), and neither imports anything of
    # 027's: one direction.
    PurePackage("grimoire.generation", ("grimoire.generation", "grimoire.core", "grimoire.core.inference",
                                        "grimoire.voice", "grimoire.evals.text", "grimoire.evals.checks",
                                        "grimoire.context", "grimoire.goals")),
    # Labelling (035 milestone 2): the facts a labeller may cite, its prompt and a decoded reply bounded into
    # 029 factors and stakes. The model call is the runtime's; JSON decoding stays in that adapter.
    PurePackage("grimoire.labelling", ("grimoire.labelling", "grimoire.adjudication", "grimoire.context",
                                       "grimoire.graph", "grimoire.core"),
                never_import=("grimoire.core.inference",)),
    # Played chat turns (035): posts, takes, labellings (029's factor and stake values), proposed effects (021's
    # proposal and endorsement values and the proposal's codec), recorded 039 intentions (039's caps and outcomes),
    # who heard what, and their codec; the runner is an adapter.
    PurePackage("grimoire.scene", ("grimoire.scene", "grimoire.core", "grimoire.core.inference",
                                   "grimoire.comms", "grimoire.generation", "grimoire.transcripts",
                                   "grimoire.adjudication", "grimoire.effects", "grimoire.goals")),
    # Operational tracing (048): spans, call samples and cost attribution; the clock and prices are passed in.
    PurePackage("grimoire.tracing", ("grimoire.tracing", "grimoire.core.inference")),
    # Offline evaluation: of the domain core it uses only the inference contracts (sprint 015);
    # only its loader and CLI read files.
    PurePackage("grimoire.evals", ("grimoire.evals", "grimoire.core.inference"),
                io_modules=frozenset({"loader.py", "story_quality.py"}),
                io_stdlib=frozenset({"json", "pathlib", "importlib.resources", "sys", "argparse"}),
                io_names=frozenset({"print"})),
)


def _under(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def owning_package(path: Path) -> PurePackage | None:
    """The most specific pure package that `path` belongs to."""
    owners = [p for p in PURE_PACKAGES if path.resolve().is_relative_to(p.directory)]
    return max(owners, key=lambda p: len(p.directory.parts), default=None)


def pure_sources() -> list[tuple[Path, PurePackage]]:
    sources = {path for package in PURE_PACKAGES for path in package.directory.rglob("*.py")}
    return [(path, owning_package(path)) for path in sorted(sources)]


def module_package(path: Path) -> str:
    """The package a module's relative imports resolve against."""
    return ".".join(path.resolve().parent.relative_to(SRC).parts)


def _imported_names(node: ast.Import | ast.ImportFrom, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.level:
        parts = package.split(".")
        if node.level > len(parts):
            return ["<relative import beyond the top-level package>"]
        base = ".".join(parts[:len(parts) - node.level + 1])
        module = f"{base}.{node.module}" if node.module else base
    else:
        module = node.module or ""
    # `from x import y` may bind a submodule x.y, so both names must be allowed.
    return [module, *(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")]


def _chain(node: ast.Attribute) -> tuple[str | None, list[str]]:
    """``a.b.c`` as ("a", ["b", "c"]); the root is None unless it is a plain name."""
    attrs = []
    while isinstance(node, ast.Attribute):
        attrs.append(node.attr)
        node = node.value
    return (node.id if isinstance(node, ast.Name) else None), attrs[::-1]


def _reaches_impure_module(module_name: str, attrs: list[str]) -> bool:
    """Whether ``module_name.attrs...`` passes through a module outside the allowlist."""
    import importlib
    import types
    try:
        value = importlib.import_module(module_name)
    except ImportError:
        return False
    for attr in attrs:
        value = getattr(value, attr, None)
        if value is None:
            return False
        if isinstance(value, types.ModuleType) and value.__name__.split(".")[0] not in PURE_STDLIB:
            return True
    return False


def _root_name(node: ast.expr) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _imported_aliases(tree: ast.Module) -> dict[str, str | None]:
    """Each name an import binds, with the stdlib module it is (None if not a plain module)."""
    names: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                names[bound] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            names |= {alias.asname or alias.name: None for alias in node.names}
    return {name: (module if module and module.split(".")[0] in sys.stdlib_module_names else None)
            for name, module in names.items()}


def boundary_problems(source: str, filename: str, package: PurePackage, within: str) -> list[str]:
    """Why `source`, a module of package `within` owned by `package`, is not pure.

    `filename` is the module's path under ``src`` (``grimoire/evals/loader.py``).
    """
    edge = package.name.replace(".", "/") + "/"
    io = any(filename.replace("\\", "/") == edge + module for module in package.io_modules)
    io_stdlib = package.io_stdlib if io else frozenset()
    banned_names = BANNED_NAMES - (package.io_names if io else frozenset())
    banned_attributes = BANNED_ATTRIBUTES
    problems = []
    tree = ast.parse(source, filename)
    imported = _imported_aliases(tree)
    modules = {name for name, module in imported.items() if module}
    # A module object may only be used to reach its own attributes: assigned or passed
    # around (`helper = dataclasses`, `getattr(dataclasses, ...)`) it hides what is reached.
    attribute_roots = {id(node.value) for node in ast.walk(tree)
                       if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)}
    io_edge = [f"{package.name}.{Path(m).with_suffix('').as_posix().replace('/', '.')}"
               for m in package.io_modules] if not io else []
    for node in ast.walk(tree):
        where = f"{filename}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Name) and node.id in banned_names:
            problems.append(f"{where} uses {node.id}")
        elif (isinstance(node, ast.Name) and node.id in modules and isinstance(node.ctx, ast.Load)
              and id(node) not in attribute_roots):
            problems.append(f"{where} uses module {node.id} as a value")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in REFLECTION
              and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
              and node.args[1].value in MODULE_ROUTES | BANNED_ATTRIBUTES):
            problems.append(f"{where} reaches .{node.args[1].value} by name")
        elif isinstance(node, ast.Attribute) and (
                node.attr in banned_attributes
                or (node.attr in MODULE_ROUTES and _root_name(node.value) in imported)
                or ((root := _chain(node))[0] in modules
                    and _reaches_impure_module(imported[root[0]], root[1]))):
            problems.append(f"{where} uses .{node.attr}")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and not node.level and (node.module or "").split(".")[0] != "grimoire":
                # `from typing import sys`: a module held by an allowed one is still that module.
                # So is one held under another name (`from enum import bltns`).
                problems += [f"{where} imports {alias.name} through {node.module}" for alias in node.names
                             if (alias.name in sys.stdlib_module_names and alias.name not in PURE_STDLIB
                                 and not any(_under(alias.name, m) for m in io_stdlib))
                             or _impure_module_attribute(node.module, alias.name)]
            for name in _imported_names(node, within):
                if any(_under(name, edge) for edge in io_edge):
                    problems.append(f"{where} imports the file edge {name}")
                    continue
                top = name.split(".")[0]
                if top == "grimoire":
                    if name == "grimoire":
                        continue  # the namespace itself; its members are checked separately
                    if (not any(_under(name, p) for p in package.may_import)
                            or any(_under(name, p) for p in package.never_import)):
                        problems.append(f"{where} imports {name}")
                elif top not in PURE_STDLIB and not any(_under(name, m) for m in io_stdlib):
                    problems.append(f"{where} imports {name}")
    return problems
