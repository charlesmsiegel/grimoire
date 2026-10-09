"""Guard: only the planner reads the legacy settings layout (inference slice I).

A store written before the roles format keeps `active_connection_id`,
`fallback_connection_id`, `embeddings_*` and one `route_<k>` per legacy route
in `config.md` (and `route_<k>` in a `campaign.md`), and its connections carry
the model fields format 2 keeps elsewhere -- `reasoning_effort` and
`sampler_preset` among them. Slice I deleted the second runtime read path
(`translate.py`, the resolver's format-1 branches, `llm_sampling`'s legacy GLM
read): play reads that layout through `legacy_plan.overlay` alone, in memory,
and the migration and retirement persist what the same planner plans. A
second reader is how the two drift -- one place carries a legacy value over
and the other does not -- so this holds the planner to being the only one.

The claims, each held against the AST of `backend/src/grimoire`:

- `test_only_the_planner_reads_the_legacy_layout`: outside `ALLOWED`, nothing
  spells a legacy config key (a `str` constant equal to a member of
  `inference_keys.LEGACY_GLOBAL_KEYS`, or any `route_<k>` spelling of a
  route), names `routing.CONFIG_KEYS` or `routing.legacy_key`, or reads a
  connection's legacy `reasoning_effort` / `sampler_preset` -- `X.get(...)` or
  `X[...]` with that literal. `sampler_preset` is matched on the key alone,
  whatever `X` is called (only a connection record carries it);
  `reasoning_effort` where `X` is a name spelled `conn`, `raw` or
  `connection`, because a preset's `params` carry it legitimately. `ALLOWED` is the planner, retirement and its record
  (`inference/retire.py` and the store-level `inference_retired.py`), and the
  modules that own the spelling or the storage of those keys: the key lists
  (`inference_keys`, `routing`), `config.md`'s reader and its write refusals
  (`config`), the connection store (`llm_connections`, which owns the model
  fields and sweeps the legacy keys on a delete), and a campaign's birth
  (`campaigns/lifecycle`). `ECHOES` names the one other place, by function:
  the legacy Settings form's round trip (see there).
- `test_only_resolve_migrate_and_retire_import_the_planner`: `legacy_plan` is
  imported by `resolve.py`, `migrate.py` and `retire.py` only -- never by
  `settings.py`, which reaches what it needs through `resolve` (N11). The
  package's `__init__.py` is its roster and binds every submodule; it reads
  nothing. `resolve.py` names one FUNCTION of the planner, `overlay` (its
  `Overlay` type is a name, not a reader), and calls it exactly once, inside
  `_overlay`.
- `test_the_guard_flags_a_planted_reader`: the scan reports a
  `cfg.get("active_connection_id")` planted in a module of its own.

Honest about its reach, in the house style. It sees spellings, not meaning:
a key assembled at runtime (`"active_" + "connection_id"`), a receiver named
anything but the three names above, or a value read through `getattr` passes.
A preset's `params["reasoning_effort"]` is legitimate -- the derived reasoning
presets put it there -- and its receiver is never one of those names. And
`migrate.py` is deliberately NOT in `ALLOWED`: anything the migration would
read of the legacy layout moves into the planner, and it calls the planner's
`global_mapped` / `campaign_mapped` / `stated` for it. As CLAUDE.md says of
every guard here: the guards do not prove absence.
"""

from __future__ import annotations

import ast
import pathlib

from grimoire.store import inference_keys, routing

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "grimoire"

#: The modules that may spell, store or read the legacy layout.
ALLOWED = frozenset({
    "store/inference/legacy_plan.py",
    "store/inference/retire.py",
    "store/inference_retired.py",
    "store/inference_keys.py",
    "store/config.py",
    "store/llm_connections.py",
    "store/routing.py",
    "store/campaigns/lifecycle.py",
})

#: Functions (or module-level names) outside `ALLOWED` that spell a legacy
#: config key without resolving anything from it, each with why. Only the
#: legacy Settings form's round trip: at format 1 those keys are still the
#: user's to write (ruling 7), so `GET /config` reports what is stored and
#: `PUT /config` confirms an embedding move before it lands. Neither is play:
#: what plays is the resolver's answer, through the planner.
ECHOES: dict[str, dict[str, str]] = {
    "routes/config.py": {
        "_public_config": "reports the stored legacy keys back to the legacy "
                          "Settings form (`GET /config`), unchanged by slice I",
        "_EMBEDDING_LEGACY_KEYS": "the legacy embedding keys `PUT /config` may "
                                  "move at format 1, confirmed first",
        "_refuse_unconfirmed_config_reembed": "names the provider a legacy "
                                              "embedding move would re-embed through",
        "_check_preset_field": "validates the `sampler_preset` a legacy connection "
                               "editor's write sends (refused at format 2 as a "
                               "model field before it gets here)",
    },
}

#: The legacy `config.md` / `campaign.md` keys, spelled out.
LEGACY_KEYS = frozenset({*inference_keys.LEGACY_GLOBAL_KEYS,
                         *(routing.config_key(r.key) for r in routing.ROUTES)})
#: The legacy connection fields a reader would take a selection's preset or
#: a GLM effort from.
LEGACY_FIELDS = frozenset({"reasoning_effort", "sampler_preset"})
#: The names a raw connection goes by in this tree.
CONNECTION_NAMES = frozenset({"conn", "raw", "connection"})
#: The legacy fields matched on the key alone, whatever the receiver is
#: called: nothing but a connection record spells this one.
KEYED_FIELDS = frozenset({"sampler_preset"})
#: `routing`'s legacy spelling helpers.
ROUTING_LEGACY = frozenset({"CONFIG_KEYS", "legacy_key"})

#: The modules that may import the planner.
IMPORTERS = frozenset({"store/inference/resolve.py", "store/inference/migrate.py",
                       "store/inference/retire.py"})
#: The package roster, which binds every submodule and reads nothing.
ROSTER = "store/inference/__init__.py"


def _modules() -> list[tuple[str, ast.Module]]:
    out = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        out.append((rel, ast.parse(path.read_text(encoding="utf-8"))))
    return out


def _owners(tree: ast.Module) -> dict[int, str]:
    """`id(node)` -> the top-level function, class or assigned name it sits in."""
    owner: dict[int, str] = {}
    for top in tree.body:
        if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = top.name
        elif isinstance(top, ast.Assign) and len(top.targets) == 1 \
                and isinstance(top.targets[0], ast.Name):
            name = top.targets[0].id
        elif isinstance(top, ast.AnnAssign) and isinstance(top.target, ast.Name):
            name = top.target.id
        else:
            continue
        for node in ast.walk(top):
            owner[id(node)] = name
    return owner


def _receiver_reads(receiver: ast.AST, key: ast.AST) -> bool:
    """Whether reading `key` off `receiver` reads a legacy connection field:
    `sampler_preset` off anything (no preset or setting is spelled so; only a
    connection record carries it), `reasoning_effort` off a name a connection
    goes by (a preset's `params` legitimately carry that one)."""
    if not isinstance(key, ast.Constant) or key.value not in LEGACY_FIELDS:
        return False
    if key.value in KEYED_FIELDS:
        return True
    return isinstance(receiver, ast.Name) and receiver.id in CONNECTION_NAMES


def _legacy_field(node: ast.AST) -> bool:
    """`X.get("<field>"...)` or `X["<field>"]` reading a legacy connection
    field (`_receiver_reads`)."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "get" and node.args:
        return _receiver_reads(node.func.value, node.args[0])
    if isinstance(node, ast.Subscript):
        return _receiver_reads(node.value, node.slice)
    return False


def scan(rel: str, tree: ast.Module) -> list[str]:
    """Every legacy read `tree` (module `rel`) makes, as `rel:line: what`."""
    if rel in ALLOWED:
        return []
    echoes = ECHOES.get(rel, {})
    owner = _owners(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        what = ""
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value in LEGACY_KEYS:
            what = f"spells the legacy key {node.value!r}"
        elif isinstance(node, ast.Attribute) and node.attr in ROUTING_LEGACY \
                and isinstance(node.value, ast.Name) and node.value.id == "routing":
            what = f"names routing.{node.attr}"
        elif isinstance(node, ast.ImportFrom) and node.module \
                and node.module.split(".")[-1] == "routing" \
                and any(a.name in ROUTING_LEGACY for a in node.names):
            what = "imports routing's legacy spelling"
        elif _legacy_field(node):
            what = "reads a connection's legacy model field"
        if what and owner.get(id(node)) not in echoes:
            found.append((getattr(node, "lineno", 0), f"{rel}:{getattr(node, 'lineno', '?')}: {what}"))
    return [hit for _, hit in sorted(found)]


def test_only_the_planner_reads_the_legacy_layout():
    found = [hit for rel, tree in _modules() for hit in scan(rel, tree)]
    assert found == [], "\n".join(found)


def test_every_echo_and_allowed_path_is_real():
    """A stale entry would exempt a file or a function that no longer exists
    -- and whatever later takes its name. Every one exists now that Task 6
    has written retirement and its record."""
    for rel in ALLOWED:
        assert (SRC / rel).is_file(), rel
    for rel, names in ECHOES.items():
        owners = set(_owners(ast.parse((SRC / rel).read_text(encoding="utf-8"))).values())
        assert set(names) <= owners, (rel, set(names) - owners)


def _imports_planner(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module.split(".")[-1] == "legacy_plan":
            return True
        return any(a.name == "legacy_plan" for a in node.names)
    if isinstance(node, ast.Import):
        return any(a.name.split(".")[-1] == "legacy_plan" for a in node.names)
    return False


def test_only_resolve_migrate_and_retire_import_the_planner():
    importers = {rel for rel, tree in _modules()
                 if rel != "store/inference/legacy_plan.py"
                 and any(_imports_planner(n) for n in ast.walk(tree))}
    assert importers - {ROSTER} <= IMPORTERS, sorted(importers - IMPORTERS - {ROSTER})
    assert "store/inference/settings.py" not in importers

    from grimoire.store.inference import legacy_plan

    tree = ast.parse((SRC / "store/inference/resolve.py").read_text(encoding="utf-8"))
    named = {n.attr for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
             and n.value.id == "legacy_plan"}
    functions = {name for name in named if not isinstance(getattr(legacy_plan, name), type)}
    assert functions == {"overlay"}, named
    calls = [(fn.name, n) for fn in ast.walk(tree)
             if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
             for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "legacy_plan"]
    assert [name for name, _ in calls] == ["_overlay"], calls


def test_the_guard_flags_a_planted_reader(tmp_path):
    planted = tmp_path / "planted.py"
    planted.write_text('def active(cfg):\n    return cfg.get("active_connection_id")\n\n'
                       'def effort(conn):\n    return conn["reasoning_effort"]\n\n'
                       'def preset(raw):\n    return raw.get("sampler_preset", "")\n\n'
                       'def routes():\n    return routing.CONFIG_KEYS\n\n'
                       'def editor(stored):\n    return stored.get("sampler_preset", "")\n',
                       encoding="utf-8")
    found = scan("routes/planted.py", ast.parse(planted.read_text(encoding="utf-8")))
    assert [hit.split(": ", 1)[1] for hit in found] == [
        "spells the legacy key 'active_connection_id'",
        "reads a connection's legacy model field",
        "reads a connection's legacy model field",
        "names routing.CONFIG_KEYS",
        # `sampler_preset` whatever the record is called.
        "reads a connection's legacy model field",
    ], found
    # A preset's own params are not a connection's legacy field.
    clean = ast.parse('def own(preset):\n    return preset["params"].get("reasoning_effort")\n')
    assert scan("routes/clean.py", clean) == []
    # And an echo is exempt only inside the function it names.
    echoed = ast.parse('def _public_config(cfg):\n    return cfg.get("embeddings_model")\n\n'
                       'def other(cfg):\n    return cfg.get("embeddings_model")\n')
    assert len(scan("routes/config.py", echoed)) == 1
