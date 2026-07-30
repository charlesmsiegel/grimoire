"""Guard: pydantic usage stays v1/v2-agnostic, so the Android pydantic-1.10 pin
stays an install-time choice rather than a code change (CLAUDE.md, Android).

The rule is *stricter* than "no v2-only API", and the distinction matters:
``Field``, ``validator`` and ``root_validator`` all exist in pydantic 1.10, so
banning them is not a v1-compatibility requirement. It is the project's "plain
``BaseModel`` fields only" requirement, which exists so request parsing behaves
identically on both lines and the APK's 1.10 pin never becomes a code change.

Reach, stated plainly rather than implied — in the spirit of
``test_atomic_guard.py``, whose honesty about its own limits is the reason to
trust it:

- **Attribute matching is receiver-agnostic, deliberately.** ``obj.model_dump()``
  cannot be resolved to a type statically, so *any* attribute with a banned name
  is flagged. An unrelated object with a same-named method is therefore a false
  positive; the exemption marker is the remedy. Bare *names* are not matched this
  way — those are resolved through the module's pydantic imports, so an ordinary
  local function called ``validator`` is not flagged.
- **A fully dynamic call is not matched at all** — ``getattr(m, "model_" +
  "dump")()`` defeats any syntactic check.
- **Class-body ``model_config`` assignment is matched separately**, because the
  canonical v2 form ``model_config = {"extra": "forbid"}`` mentions no pydantic
  name and is not a call, so import-aware matching alone would miss it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from . import guard_markers

# The trailing colon is load-bearing. `guard_markers._stated_reason` returns
# `body[len(MARKER):].strip()`, so without it `# pydantic-ok:` would yield ":"
# -- truthy -- and a reasonless exemption would be accepted.
MARKER = "pydantic-ok:"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire"

V2_ONLY = {
    "model_dump", "model_dump_json", "model_validate", "model_validate_json",
    "model_json_schema", "model_copy", "model_construct", "model_rebuild",
    "model_fields", "model_fields_set", "model_config", "ConfigDict",
    "TypeAdapter", "RootModel", "field_validator", "model_validator",
    "field_serializer", "model_serializer", "computed_field", "validate_call",
}
# Present in 1.10 too, banned anyway: the models stay plain typed fields.
PROJECT_BANNED = {"Field", "validator", "root_validator"}
BANNED = V2_ONLY | PROJECT_BANNED


def _pydantic_aliases(tree: ast.AST) -> dict[str, str]:
    """{local name: pydantic name} for this module's pydantic imports.

    Name-only matching is trivially evaded by ``from pydantic import ConfigDict
    as CD``, so bindings are resolved before anything is flagged.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "pydantic" or mod.startswith("pydantic."):
                for a in node.names:
                    aliases[a.asname or a.name] = a.name
    return aliases


def scan(src: str) -> list[tuple[ast.AST, str]]:
    """Flagged ``(node, api name)`` pairs for one module's source."""
    tree = ast.parse(src)
    aliases = _pydantic_aliases(tree)
    found: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        # `pydantic.ConfigDict()`, `obj.model_dump()`, `m.model_fields`.
        # Receiver-agnostic on purpose -- see the module docstring.
        if isinstance(node, ast.Attribute) and node.attr in BANNED:
            found.append((node, node.attr))
        # A bare name, only when a pydantic import bound it.
        elif isinstance(node, ast.Name) and aliases.get(node.id) in BANNED:
            found.append((node, aliases[node.id]))
        # `model_config = {...}` in a class body: no pydantic name, not a call.
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                targets = (stmt.targets if isinstance(stmt, ast.Assign)
                           else [stmt.target] if isinstance(stmt, ast.AnnAssign) else [])
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == "model_config":
                        found.append((stmt, "model_config"))
    return found


def _reason(src: str, node: ast.AST, others=()) -> str | None:
    return guard_markers.marker_reason(MARKER, src, node, others)


PROHIBITED = [
    ("attribute call", "def f(m):\n    return m.model_dump()\n"),
    ("qualified", "import pydantic\nx = pydantic.ConfigDict()\n"),
    ("aliased import", "from pydantic import ConfigDict as CD\nx = CD()\n"),
    ("aliased field", "from pydantic import Field as F\nclass M:\n    a: int = F(1)\n"),
    ("bare decorator", "from pydantic import validator\n@validator\ndef f():\n    pass\n"),
    ("decorator call",
     "from pydantic import field_validator\n@field_validator('a')\ndef f():\n    pass\n"),
    ("type adapter", "from pydantic import TypeAdapter\nx = TypeAdapter(int)\n"),
    ("class dict config", "class M:\n    model_config = {'extra': 'forbid'}\n"),
    ("annotated config", "class M:\n    model_config: dict = {'extra': 'forbid'}\n"),
]


@pytest.mark.parametrize("label,src", PROHIBITED, ids=[p[0] for p in PROHIBITED])
def test_prohibited_forms_are_flagged(label, src):
    assert scan(src), f"{label} slipped past the guard"


ALLOWED = [
    ("plain model", "from pydantic import BaseModel\nclass M(BaseModel):\n    a: int\n"),
    # The case that proves bare names are resolved through imports rather than
    # matched by spelling: an ordinary local `validator` must not be flagged.
    ("local function named validator", "def validator(x):\n    return x\nvalidator(1)\n"),
    ("dict key", "d = {'model_config': 1}\n"),
    ("module-level name", "model_config = 1\n"),
    ("unrelated import", "from json import dumps\nx = dumps({})\n"),
]


@pytest.mark.parametrize("label,src", ALLOWED, ids=[a[0] for a in ALLOWED])
def test_allowed_forms_are_not_flagged(label, src):
    assert not scan(src), f"{label} was flagged and should not be"


def test_a_valid_marker_exempts_the_call():
    src = "def f(m):\n    return m.model_dump()  # pydantic-ok: v1/v2 shim\n"
    node, _ = scan(src)[0]
    assert _reason(src, node) == "v1/v2 shim"


def test_a_reasonless_marker_does_not_exempt():
    src = "def f(m):\n    return m.model_dump()  # pydantic-ok:\n"
    node, _ = scan(src)[0]
    assert not _reason(src, node), "a bare marker must not silence the guard"


def test_a_marker_inside_a_string_does_not_exempt():
    src = "def f(m):\n    msg = '# pydantic-ok: nope'\n    return m.model_dump()\n"
    node, _ = scan(src)[0]
    assert not _reason(src, node)


def test_the_package_uses_no_v2_pydantic_api():
    """The real scan: every flagged call carries a reasoned exemption."""
    files = sorted(SRC.rglob("*.py"))
    assert len(files) > 50, f"the scan found only {len(files)} files -- glob broken?"

    violations: list[str] = []
    exempted: list[str] = []
    for path in files:
        src = path.read_text(encoding="utf-8")
        found = scan(src)
        nodes = [n for n, _ in found]
        for node, api in found:
            others = [o for o in nodes if o is not node]
            rel = path.relative_to(SRC.parents[1]).as_posix()
            if _reason(src, node, others):
                exempted.append(f"{rel}:{node.lineno} {api}")
            else:
                violations.append(f"{rel}:{node.lineno} uses {api}")

    assert not violations, (
        "pydantic v2 API outside the routes.common._dump shim (CLAUDE.md):\n  "
        + "\n  ".join(violations))

    # Non-vacuity. There is deliberately nothing to exempt: `routes.common._dump`
    # reaches model_dump through `getattr(model, "model_dump", None)` so that one
    # function works on both pydantic lines, and that form is not statically
    # detectable (see the module docstring). The package therefore has zero
    # flagged sites today, and this guard's whole value is catching the first
    # one -- which makes "found nothing" indistinguishable from "scanner broken"
    # unless we prove the scanner works against real module source. So: take a
    # real file, append one violation, and require that it is caught.
    assert len(files) > 50
    probe = (SRC / "routes" / "common.py").read_text(encoding="utf-8")
    assert not scan(probe), "routes/common.py should have no flagged site"
    assert scan(probe + "\n_x = _m.model_dump()\n"), \
        "the scanner found nothing in real module source -- it is not wired correctly"
    assert not exempted, f"unexpected exemptions appeared: {exempted}"
