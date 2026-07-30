"""Guard: filesystem access goes through the designated resolvers, so no
packaged resource or data path assumes a repo checkout or a desktop ``~``
(docs/android-architecture.md, CLAUDE.md).

This is a *use-the-resolver* rule, not a ban on ``Path.home()``:
``store.paths`` builds ``home()`` out of it by design, and ``store.proclock``
uses it deliberately for machine-local lock state. What the guard catches is a
new caller reaching the disk *around* the resolvers.

Reach, stated plainly rather than implied — in the spirit of
``test_atomic_guard.py``, whose honesty about its own limits is the reason to
trust it. The guard flags a fixed list of idioms:

- ``Path.home()`` / ``pathlib.Path.home()``, including an aliased ``Path`` —
  and *only* where ``Path`` really is pathlib's. An attribute named ``home`` on
  anything else (notably ``paths.home()``, the resolver call this rule exists to
  encourage) is not flagged.
- ``.expanduser()``, and ``expanduser`` imported bare from ``os.path``.
- ``os.environ["HOME"]``, ``os.environ.get("HOME")``, ``os.getenv("HOME")``,
  and the same three for ``USERPROFILE``.
- ``Path(__file__).resolve().parents[N]`` and ``.parent.parent`` chains — but
  *only* when the chain bottoms out at ``__file__``. Walking up from a path
  inside the data store (``p.parents[2].name`` on a glob result, to recover a
  record id) is ordinary path arithmetic and encodes no repo layout.

What it cannot see, and does not claim to: a root assigned to an intermediate
variable and used later, ``os.path.dirname(__file__)`` chains, a path built from
a string literal, and anything reached through a library that takes an output
path. A guard claiming to *prove* all filesystem access uses the resolvers would
be worse than one that says where it stops.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from . import guard_markers

# The trailing colon is load-bearing -- see test_pydantic_guard.py.
MARKER = "paths-ok:"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire"
HOME_KEYS = {"HOME", "USERPROFILE"}


def _path_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(local names bound to pathlib.Path, local names bound to the pathlib module)."""
    path_names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "pathlib":
                    modules.add(a.asname or "pathlib")
        elif isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            for a in node.names:
                if a.name == "Path":
                    path_names.add(a.asname or "Path")
    return path_names, modules


def _is_path_home(node: ast.Attribute, path_names: set[str], modules: set[str]) -> bool:
    """``Path.home`` where ``Path`` really is pathlib's, or ``pathlib.Path.home``.

    Matching every attribute named ``home`` would flag ``paths.home()`` -- the
    resolver call this rule exists to encourage -- and so would invert the guard.
    """
    if node.attr != "home":
        return False
    base = node.value
    if isinstance(base, ast.Name):
        return base.id in path_names
    return (isinstance(base, ast.Attribute) and base.attr == "Path"
            and isinstance(base.value, ast.Name) and base.value.id in modules)


def _rooted_at_file(node: ast.AST) -> bool:
    """Whether this expression chain bottoms out at ``__file__``.

    ``parents[N]`` and ``.parent.parent`` are only a portability concern when
    they walk up from the *module's own location* — that is what encodes a repo
    checkout layout. The same idioms applied to a path from inside the data store
    (``p.parents[2].name`` on a glob result, to recover a record id) are ordinary
    path arithmetic and must not be flagged: marking them would assert they are
    exceptions to a rule they never broke.
    """
    cur = node
    while True:
        if isinstance(cur, ast.Name):
            return cur.id == "__file__"
        if isinstance(cur, (ast.Attribute, ast.Subscript)):
            cur = cur.value
        elif isinstance(cur, ast.Call):
            cur = cur.args[0] if cur.args else cur.func
        else:
            return False


def _is_home_env(node: ast.AST) -> bool:
    """``os.environ["HOME"]``, ``os.environ.get("HOME")`` or ``os.getenv("HOME")``."""
    if isinstance(node, ast.Subscript):
        return (isinstance(node.value, ast.Attribute) and node.value.attr == "environ"
                and isinstance(node.slice, ast.Constant) and node.slice.value in HOME_KEYS)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
        arg = node.args[0]
        if not (isinstance(arg, ast.Constant) and arg.value in HOME_KEYS):
            return False
        if node.func.attr == "getenv":
            return True
        # `.get("HOME")` only counts on os.environ, not on any dict
        return (node.func.attr == "get" and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "environ")
    return False


def scan(src: str) -> list[tuple[ast.AST, str]]:
    """Flagged ``(node, idiom)`` pairs for one module's source."""
    tree = ast.parse(src)
    path_names, modules = _path_bindings(tree)
    found: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if _is_path_home(node, path_names, modules):
                found.append((node, "Path.home()"))
            elif node.attr == "expanduser":
                found.append((node, "expanduser()"))
            elif (node.attr == "parent" and isinstance(node.value, ast.Attribute)
                  and node.value.attr == "parent" and _rooted_at_file(node)):
                found.append((node, ".parent.parent from __file__"))
        elif isinstance(node, ast.Name) and node.id == "expanduser":
            found.append((node, "expanduser()"))
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
              and node.value.attr == "parents" and _rooted_at_file(node)):
            found.append((node, "parents[N] from __file__"))
        elif _is_home_env(node):
            found.append((node, "HOME from the environment"))
    return found


def _reason(src: str, node: ast.AST, others=()) -> str | None:
    return guard_markers.marker_reason(MARKER, src, node, others)


PROHIBITED = [
    ("Path.home", "from pathlib import Path\np = Path.home()\n"),
    ("aliased Path", "from pathlib import Path as P\np = P.home()\n"),
    ("qualified home", "import pathlib\np = pathlib.Path.home()\n"),
    ("expanduser method", "from pathlib import Path\np = Path('~/x').expanduser()\n"),
    ("expanduser bare", "from os.path import expanduser\np = expanduser('~')\n"),
    ("environ subscript", "import os\np = os.environ['HOME']\n"),
    ("environ get", "import os\np = os.environ.get('HOME')\n"),
    ("getenv", "import os\np = os.getenv('USERPROFILE')\n"),
    ("parents index", "from pathlib import Path\np = Path(__file__).resolve().parents[2]\n"),
    ("parent chain", "from pathlib import Path\np = Path(__file__).parent.parent\n"),
]


@pytest.mark.parametrize("label,src", PROHIBITED, ids=[p[0] for p in PROHIBITED])
def test_prohibited_idioms_are_flagged(label, src):
    assert scan(src), f"{label} slipped past the guard"


ALLOWED = [
    # The case that keeps the guard from inverting its own rule.
    ("resolver call", "from grimoire.store import paths\np = paths.home() / 'x'\n"),
    ("unrelated home", "p = obj.home()\n"),
    ("single parent", "from pathlib import Path\np = Path(__file__).parent\n"),
    ("unrelated env", "import os\np = os.environ.get('GRIMOIRE_HOME')\n"),
    ("unrelated dict get", "p = cfg.get('HOME')\n"),
    ("locally-defined Path", "class Path:\n    pass\np = Path.home\n"),
]


@pytest.mark.parametrize("label,src", ALLOWED, ids=[a[0] for a in ALLOWED])
def test_allowed_idioms_are_not_flagged(label, src):
    assert not scan(src), f"{label} was flagged and should not be"


def test_a_valid_marker_exempts_the_call():
    src = "from pathlib import Path\np = Path.home()  # paths-ok: the resolver itself\n"
    node, _ = scan(src)[0]
    assert _reason(src, node) == "the resolver itself"


def test_a_reasonless_marker_does_not_exempt():
    src = "from pathlib import Path\np = Path.home()  # paths-ok:\n"
    node, _ = scan(src)[0]
    assert not _reason(src, node), "a bare marker must not silence the guard"


def test_a_marker_inside_a_string_does_not_exempt():
    src = "from pathlib import Path\nmsg = '# paths-ok: nope'\np = Path.home()\n"
    node, _ = scan(src)[0]
    assert not _reason(src, node)


def test_the_package_reaches_the_disk_only_through_the_resolvers():
    """The real scan: every flagged site carries a reasoned exemption."""
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 50, f"the scan found only {len(files)} files -- glob broken?"

    violations: list[str] = []
    exempted: list[str] = []
    for path in files:
        src = path.read_text(encoding="utf-8")
        found = scan(src)
        nodes = [n for n, _ in found]
        for node, idiom in found:
            others = [o for o in nodes if o is not node]
            rel = path.relative_to(SRC.parents[1]).as_posix()
            if _reason(src, node, others):
                exempted.append(f"{rel}:{node.lineno} {idiom}")
            else:
                violations.append(f"{rel}:{node.lineno} uses {idiom}")

    assert not violations, (
        "filesystem access outside the resolvers (docs/android-architecture.md):\n  "
        + "\n  ".join(violations))
    # Non-vacuous: the known sanctioned sites must still be found, so a broken
    # glob or a renamed package cannot pass by finding nothing to check.
    assert len(exempted) >= 8, f"expected at least 8 exempted sites, found: {exempted}"
