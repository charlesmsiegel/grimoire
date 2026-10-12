"""03-S1: SQLite has one runtime owner; artifacts need complete keys.

This syntax guard checks literal SQL and named public read interfaces, not
arbitrary Python data flow or SQLite's execution plan. Unknown SQL in the
owner is rejected rather than inferred safe. Maintenance exceptions belong
to later slices and must come with their own reasoned planted cases.
"""

import ast
import re
from pathlib import Path

import pytest

import grimoire

_KEY_WHERE = re.compile(r"\bFROM\s+artifacts\s+WHERE\s+kind\s*=\s*\?\s+AND\s+"
                        r"key_digest\s*(?:=\s*\?|IN\s*\(\?(?:,\?){0,127}\))\s*;?\s*\Z", re.IGNORECASE)


def _artifact_read(sql: str) -> bool:
    # Conservative about quoted/schema-qualified names and JOINs. Unknown
    # artifact SELECT shapes are rejected; only kind-bound point/IN forms pass.
    return bool(re.search(r"\bSELECT\b", sql, re.IGNORECASE) and re.search(
        r"\bartifacts\b", sql, re.IGNORECASE))


def _sql_tokens(sql: str) -> str:
    # Recognize quoting BEFORE comments: '--' inside a literal is ordinary
    # content. Mask quoted content so it cannot manufacture a bounded WHERE.
    pattern = r"'(?:(?:'')|[^'])*'|\"(?:(?:\"\")|[^\"])*\"|`[^`]*`|\[[^\]]*\]|--[^\n]*|/\*.*?\*/"

    def mask(match: re.Match[str]) -> str:
        token = match.group()
        if token.startswith("'"):
            name = token[1:-1].lower()
            return f"'{name}'" if name in ("artifacts", "sources", "materialized") else "''"
        if token.startswith(('"', "`", "[")):
            name = token[1:-1].lower()
            return f'"{name}"' if name in ("artifacts", "sources", "materialized") else '"identifier"'
        return " "

    return re.sub(pattern, mask, sql, flags=re.DOTALL)


def _sql_violations(argument: ast.expr, constants: dict[str, str] | None = None) -> list[str]:
    if isinstance(argument, ast.Name) and constants and argument.id in constants:
        argument = ast.Constant(constants[argument.id])
    if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
        return ["SQL is not statically known"]
    sql = _sql_tokens(argument.value)
    if re.search(r'\bRETURNING\b', sql, re.IGNORECASE):
        # Current readers use explicit bounded SELECTs. A write statement
        # returning rows must not become an alternate inventory interface.
        return ['RETURNING row readers are not an allowed selection shape']
    unsafe = (len(re.findall(r"\bSELECT\b", sql, re.IGNORECASE)) != 1
              or re.search(r"\b(?:OR|UNION|INTERSECT|EXCEPT|JOIN)\b", sql, re.IGNORECASE))
    if _artifact_read(sql) and (not _KEY_WHERE.search(sql) or unsafe):
        return ["artifact SELECT requires kind AND key_digest"]
    source_read = (re.search(r"\bSELECT\b", sql, re.IGNORECASE)
                   and re.search(r"\b(?:sources|materialized)\b", sql, re.IGNORECASE))
    bounded = re.search(r"\bFROM\s+(?:sources|materialized)\s+WHERE\s+path\s+IN\s*\(\?(?:,\?){0,127}\)\s*;?\s*\Z",
                        sql, re.IGNORECASE)
    if source_read and (not bounded or unsafe):
        return ["source SELECT requires supplied path set"]
    metadata = re.fullmatch(
        r"\s*SELECT\s+(?:value\s+FROM\s+cache_meta\s+WHERE\s+name\s*=\s*\?"
        r"|name\s+FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*''\s+LIMIT\s+1)\s*;?\s*",
        sql, re.IGNORECASE)
    if re.search(r"\bSELECT\b", sql, re.IGNORECASE) and not (_artifact_read(sql) or source_read or metadata):
        return ["unknown SELECT cannot establish a live set"]
    return []


def _read_interface(node: ast.FunctionDef | ast.AsyncFunctionDef,
                    constants: dict[str, str] | None = None) -> list[str]:
    if node.name.startswith("_"):
        return []
    if any(word in node.name for word in ("connection", "cursor", "session")):
        return ["public connection accessor"]
    source_names = {"known_hashes": "paths", "validate": "inputs", "validate_one": "input",
                    "collection": "collection", "derive": "inputs", "materialized": "paths"}
    texts = [item.value for item in ast.walk(node)
             if isinstance(item, ast.Constant) and isinstance(item.value, str)]
    texts.extend(constants[item.id] for item in ast.walk(node)
                 if isinstance(item, ast.Name) and constants and item.id in constants)
    reads_rows = any(_artifact_read(text) for text in texts)
    reads_sources = any(re.search(r"\bSELECT\b", text, re.IGNORECASE)
                        and re.search(r"\b(?:sources|materialized)\b", text, re.IGNORECASE) for text in texts)
    if not (reads_rows or reads_sources or node.name in source_names or node.name in ("lookup", "lookup_many")
            or node.name.startswith(("read_", "get_", "rank"))):
        return []
    positional = [*node.args.posonlyargs, *node.args.args]
    names = {arg.arg for arg in [*positional, *node.args.kwonlyargs]}
    defaulted = {arg.arg for arg in positional[len(positional) - len(node.args.defaults):]}
    defaulted.update(arg.arg for arg, default in zip(node.args.kwonlyargs,
                                                     node.args.kw_defaults, strict=True)
                     if default is not None)
    required = ({source_names[node.name]} if node.name in source_names else
                {"paths", "inputs", "input"} if reads_sources else {"key", "keys"})
    failures = [] if (names - defaulted) & required else ["public read requires supplied key or paths"]
    artifact_api = reads_rows or node.name in ("lookup", "lookup_many") or node.name.startswith("rank")
    if artifact_api and not (names - defaulted) & {"key", "keys"}:
        failures.append("public artifact read requires supplied key")
    return failures


def _literal_bindings(tree: ast.AST, *, owned_scope: bool = False) -> tuple[set[str], bool]:
    nodes = list(_owned_nodes(tree) if owned_scope else ast.walk(tree))
    bindings = {node.arg for node in nodes if isinstance(node, ast.arg)}
    star_import = False
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                star_import |= alias.name == "*"
                bindings.add(alias.asname or (alias.name.split(".")[0]
                                              if isinstance(node, ast.Import) else alias.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                               ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            bindings.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bindings.add(node.rest)
    return bindings, star_import


def _maintenance_sql(call: ast.Call, parents: dict[ast.AST, ast.AST], lines: list[str]) -> bool:
    owner = parents.get(call)
    while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        owner = parents.get(owner)
    if owner is None:
        return False
    reason = any('compiled-maintenance-sql:' in line and line.split('compiled-maintenance-sql:', 1)[1].strip()
                 for line in lines[owner.lineno:owner.end_lineno])
    return reason and _maintenance_shape(owner.name, call.args[0])


def _maintenance_shape(owner: str, argument: ast.expr) -> bool:
    if owner == '_purge_virtual_page' and isinstance(argument, ast.JoinedStr):
        return ast.dump(argument, include_attributes=False) == ast.dump(
            ast.parse("f'DROP TABLE {_quote_table(name)}'", mode='eval').body, include_attributes=False)
    if owner == '_purge_trigger_page' and isinstance(argument, ast.JoinedStr):
        return ast.dump(argument, include_attributes=False) == ast.dump(
            ast.parse("f'DROP TRIGGER {_quote_table(name)}'", mode='eval').body, include_attributes=False)
    if owner == '_purge_view_page' and isinstance(argument, ast.JoinedStr):
        return ast.dump(argument, include_attributes=False) == ast.dump(
            ast.parse("f'DROP VIEW {_quote_table(name)}'", mode='eval').body, include_attributes=False)
    if owner in {'_purge_connection', '_purge_table_page'} and isinstance(argument, ast.JoinedStr):
        return any(ast.dump(argument, include_attributes=False) == ast.dump(
            ast.parse(shape, mode='eval').body, include_attributes=False) for shape in
            ("f'DROP TABLE {_quote_table(name)}'", "f'DELETE FROM {_quote_table(name)}'"))
    if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
        return False
    if owner == '_lru_victims':
        return bool(re.fullmatch(r"SELECT rowid,CASE WHEN typeof\(last_used\)='integer' THEN last_used ELSE -1 END AS used "
                                 r'FROM (artifacts|sources|materialized) ORDER BY used,rowid LIMIT 128', argument.value))
    statements = {'_evict': ('DELETE FROM materialized WHERE (artifact_kind,artifact_key) IN '
                             '(SELECT kind,key_digest FROM artifacts WHERE rowid=?)'),
                  '_owned_tables': "SELECT rowid,name FROM sqlite_master WHERE type='table' AND (? IS NULL OR rowid>?) ORDER BY rowid LIMIT 1",
                  '_purge_view_page': "SELECT name FROM sqlite_master WHERE type='view' ORDER BY rowid LIMIT 1",
                  '_purge_trigger_page': "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY rowid LIMIT 129",
                  '_purge_virtual_page': "SELECT name FROM sqlite_master WHERE type='table' AND lower(name)<>'cache_meta' AND (rootpage=0 OR rootpage IS NULL) ORDER BY rowid LIMIT 1",
                  '_purge_cursor': "SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=256 THEN value END FROM cache_meta WHERE name=?"}
    return argument.value == statements.get(owner)


def _violations(source: str, module: str) -> list[str]:
    failures = []
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    lines = source.splitlines()
    constants = {item.targets[0].id: item.value.value for item in tree.body
                 if isinstance(item, ast.Assign) and len(item.targets) == 1
                 and isinstance(item.targets[0], ast.Name)
                 and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str)}
    # A module literal is safe only if no local/global assignment or argument
    # can shadow it. This is deliberately conservative rather than data flow.
    stores = [node.id for node in ast.walk(tree)
              if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)]
    bindings, star_import = _literal_bindings(tree)
    constants = {name: sql for name, sql in constants.items()
                 if not star_import and stores.count(name) == 1 and name not in bindings}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            sqlite_import = any(alias.name.split(".")[0] == "sqlite3" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            sqlite_import = (node.module or "").split(".")[0] == "sqlite3"
        else:
            sqlite_import = False
        if sqlite_import and module != "store.compiled":
            failures.append("sqlite3 belongs to store.compiled")
        if module == "store.compiled":
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                failures.extend(_read_interface(node, constants))
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("execute", "executemany", "executescript") and node.args
                    and not _maintenance_sql(node, parents, lines)):
                failures.extend(_sql_violations(node.args[0], constants))
    return failures


@pytest.mark.parametrize("source", ["import sqlite3", "import sqlite3 as db",
                                    "from sqlite3 import connect as open_db",
                                    "from sqlite3.dbapi2 import Connection"])
def test_sqlite_imports_have_one_runtime_owner(source):
    assert "_violations" in globals(), "compiled architecture guard is missing"
    assert _violations(source, "store.other")
    assert not _violations(source, "store.compiled")


@pytest.mark.parametrize("source", [
    "def lookup(): pass",
    "def read_artifact(root): pass",
    "class Batch:\n def lookup(self): pass",
    "def read_artifact(key=None): pass",
    "def artifact(root):\n return conn.execute('SELECT payload FROM artifacts WHERE kind=? AND key_digest=?')",
    "def get_connection(): return conn",
])
def test_public_artifact_reads_require_a_supplied_key(source):
    assert "_violations" in globals(), "compiled architecture guard is missing"
    assert _violations(source, "store.compiled")


@pytest.mark.parametrize("sql", [
    "SELECT payload FROM artifacts",
    "SELECT payload FROM artifacts WHERE kind=?",
    "SELECT payload FROM artifacts WHERE key_digest=?",
    "SELECT payload FROM artifacts WHERE kind=? OR key_digest=?",
    "SELECT payload FROM artifacts WHERE kind=? AND key_digest=? OR 1=1",
    "SELECT payload FROM artifacts -- WHERE kind=? AND key_digest=?",
    'SELECT payload FROM "artifacts"',
    'SELECT payload FROM [artifacts]',
    'SELECT payload FROM `artifacts`',
    'SELECT a.payload FROM cache_meta c JOIN artifacts a ON c.name=a.kind',
])
def test_artifact_selection_is_key_bounded(sql):
    assert "_violations" in globals(), "compiled architecture guard is missing"
    assert _violations(f"conn.execute({sql!r})", "store.compiled")


@pytest.mark.parametrize("source", [
    "conn.execute(sql)",
    "conn.execute('SELECT payload FROM ' + table)",
    "conn.execute(f'SELECT payload FROM artifacts WHERE {where}')",
])
def test_dynamic_read_sql_is_not_assumed_safe(source):
    assert "_violations" in globals(), "compiled architecture guard is missing"
    assert _violations(source, "store.compiled")


@pytest.mark.parametrize("source", [
    "def lookup(key): pass",
    "class Batch:\n def lookup(self, key): pass",
    "conn.execute('SELECT payload FROM artifacts WHERE kind=? AND key_digest=?', (kind,key))",
    "conn.execute('SELECT value FROM cache_meta WHERE name=?', ('schema',))",
    "conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' LIMIT 1\")",
    "conn.execute('PRAGMA application_id')",
    "conn.execute('PRAGMA application_id=1196575053')",
])
def test_metadata_and_explicit_keys_are_permitted(source):
    assert "_violations" in globals(), "compiled architecture guard is missing"
    assert not _violations(source, "store.compiled")


def test_runtime_package_obeys_compiled_boundaries():
    assert "_violations" in globals(), "compiled architecture guard is missing"
    root = Path(grimoire.__file__).parent
    failures = []
    for path in sorted(root.rglob("*.py")):
        module = ".".join(path.relative_to(root).with_suffix("").parts)
        failures.extend(f"{module}: {error}" for error in _violations(
            path.read_text(encoding="utf-8"), module))
    assert not failures, "\n".join(failures)


def test_nested_keyed_subquery_cannot_license_an_unbounded_outer_read():
    sql = ("SELECT a.payload FROM artifacts a CROSS JOIN "
           "(SELECT key_digest FROM artifacts WHERE kind=? AND key_digest=? ) live")
    assert _violations(f"def lookup(key): return conn.execute({sql!r}).fetchall()", "store.compiled")


@pytest.mark.parametrize("sql", ["SELECT content_hash FROM sources", "SELECT * FROM sources WHERE path=? OR 1=1",
                                  'SELECT content_hash FROM "sources"',
                                  "SELECT s.content_hash FROM sources s JOIN cache_meta m ON 1=1"])
def test_sources_require_a_supplied_path_set(sql):
    assert _violations(f"conn.execute({sql!r})", "store.compiled")


@pytest.mark.parametrize("binding", ["sql = 'SELECT payload FROM artifacts'", "global sql; sql = make_sql()"])
def test_a_rebound_sql_constant_is_not_assumed_safe(binding):
    source = ("sql = 'SELECT payload FROM artifacts WHERE kind=? AND key_digest=?'\n"
              f"def lookup(key):\n {binding}\n return conn.execute(sql)")
    assert _violations(source, "store.compiled")


@pytest.mark.parametrize("sql", [
    "SELECT payload FROM artifacts WHERE kind=? AND key_digest=? IS NOT TRUE",
    "SELECT content_hash FROM sources WHERE path IN (?) IS NOT TRUE",
])
def test_predicate_transform_cannot_reverse_supplied_selection(sql):
    assert _violations(f"conn.execute({sql!r})", "store.compiled")


@pytest.mark.parametrize("binding", [
    "from review_sql_source import inventory as SQL",
    "import review_sql_source as SQL",
    "from review_sql_source import *",
    "def SQL(): pass",
    "class SQL: pass",
    "try: pass\nexcept Exception as SQL: pass",
])
def test_non_assignment_bindings_cannot_reuse_an_earlier_sql_literal(binding):
    source = ("SQL = 'SELECT payload FROM artifacts WHERE kind=? AND key_digest=?'\n"
              + binding + "\ndef lookup(key): return conn.execute(SQL).fetchall()")
    assert _violations(source, "store.compiled")


@pytest.mark.parametrize("predicate", [
    "'1 FROM artifacts WHERE kind=? AND key_digest=? --'",
    '\"1 FROM artifacts WHERE kind=? AND key_digest=? --\"',
])
def test_sql_literals_cannot_invent_a_safe_where_clause(predicate):
    sql = f"SELECT payload FROM artifacts WHERE {predicate}"
    assert _violations(f"def lookup(key): return conn.execute({sql!r})", "store.compiled")


def test_pragma_prefix_cannot_license_a_trailing_inventory_script():
    source = ("APPLICATION_ID=1196575053\n"
              "def lookup(key):\n"
              " conn.executescript(f'PRAGMA application_id={APPLICATION_ID}; "
              "CREATE TEMP TABLE selected AS SELECT payload FROM artifacts;')\n"
              " return conn.execute('SELECT payload FROM selected').fetchall()")
    assert _violations(source, "store.compiled")


@pytest.mark.parametrize("table,column", [("artifacts", "payload"), ("sources", "content_hash")])
def test_single_quoted_table_identifier_still_requires_bounded_selection(table, column):
    sql = f"SELECT {column} FROM '{table}'"
    assert _violations(f"conn.execute({sql!r})", "store.compiled")


@pytest.mark.parametrize("name", ["known_hashes", "validate", "validate_one"])
def test_public_source_primitives_require_supplied_paths_or_inputs(name):
    assert _violations(f"def {name}(kind): pass", "store.compiled")


@pytest.mark.parametrize("module,function", [
    ("store.entities", "entity_hash"), ("store.characters", "dir_hash"),
    ("store.characters", "card_hash"), ("store.pcs", "dir_hash"),
    ("store.pcs", "version_hash"), ("store.greetings", "plotmap_hash"),
    ("store.image_store", "blob_intact"), ("store.appearances.versions", "actor_hash"),
    ("store.sync", "_actor_incoming"),
])
def test_decision_hashes_cannot_use_persisted_source_primitives(module, function):
    assert "_decision_violations" in globals(), "decision guard is missing"
    sources = {module: "from grimoire.store import compiled as cache\n"
               f"def {function}(p): return cache.Batch(p).validate(kind, [p])"}
    assert _decision_violations(sources)


def test_decision_guard_follows_aliases_and_actor_wrappers():
    assert "_decision_violations" in globals(), "decision guard is missing"
    sources = {
        "store.sync": "from .appearances import versions as actors\ndef _actor_incoming(p): return actors.actor_hash(p)",
        "store.appearances.versions": "from .. import helper as reads\ndef actor_hash(p): return reads.read(p)",
        "store.helper": "from . import compiled as cache\ndef read(p):\n with cache.batch(p) as request:\n  return request.known_hashes(kind, [p])",
    }
    assert _decision_violations(sources)


@pytest.mark.parametrize("flag,blocked", [(True, True), (False, False)])
def test_decision_policy_requires_explicit_false_kind(flag, blocked):
    assert "_decision_violations" in globals(), "decision guard is missing"
    sources = {"store.entities": "from . import compiled as cache\n"
               f"K = cache.Kind('decision', 1, (), (), {flag})\n"
               "def entity_hash(p): return cache.Batch(p).validate(K, [p])"}
    assert bool(_decision_violations(sources)) is blocked


def test_runtime_decision_sites_do_not_reach_persisted_sources():
    assert "_decision_violations" in globals(), "decision guard is missing"
    root = Path(grimoire.__file__).parent
    sources = {".".join(p.relative_to(root).with_suffix("").parts): p.read_text(encoding="utf-8")
               for p in root.rglob("*.py")}
    assert not _decision_violations(sources)


_DECISION_SITES = {
    "store.entities.entity_hash", "store.characters.dir_hash", "store.characters.card_hash",
    "store.pcs.dir_hash", "store.pcs.version_hash", "store.greetings.plotmap_hash",
    "store.image_store.blob_intact", "store.appearances.versions.actor_hash", "store.sync._actor_incoming",
}


def _reference(node: ast.expr, aliases: dict[str, str], module: str) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, f"{module}.{node.id}")
    if isinstance(node, ast.Attribute):
        parent = _reference(node.value, aliases, module)
        return f"{parent}.{node.attr}" if parent else None
    if isinstance(node, ast.Call):
        return _reference(node.func, aliases, module)
    return None


def _imports(tree: ast.AST, module: str) -> dict[str, str]:
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            base = ".".join(module.split(".")[:-node.level]) if node.level else ""
            prefix = ".".join(part for part in (base, node.module) if part)
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{prefix}.{alias.name}"
    return {name: target.removeprefix("grimoire.") for name, target in aliases.items()}


def _false_policy(value: ast.expr, aliases: dict[str, str], module: str) -> bool:
    if not isinstance(value, ast.Call) or _reference(value.func, aliases, module) != "store.compiled.Kind":
        return False
    flag = value.args[4] if len(value.args) > 4 else None
    for keyword in value.keywords:
        if keyword.arg == "allow_persisted_sources":
            flag = keyword.value
    return isinstance(flag, ast.Constant) and flag.value is False


def _bindings(node: ast.AST) -> list[tuple[ast.expr | None, ast.expr]]:
    if isinstance(node, ast.Assign):
        return [(target, node.value) for target in node.targets]
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [(node.target, node.value)]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [(item.optional_vars, item.context_expr) for item in node.items]
    return []


def _literal_policies(tree: ast.AST, module: str, aliases: dict[str, str],
                      inherited: set[str]) -> set[str]:
    declared = {target.id for node in _owned_nodes(tree) if isinstance(node, (ast.Assign, ast.AnnAssign))
                for target, value in _bindings(node) if isinstance(target, ast.Name)
                and _false_policy(value, aliases, module)}
    bindings, star_import = _literal_bindings(tree, owned_scope=True)
    stores = [node.id for node in _owned_nodes(tree)
              if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)]
    mutated = _namespace_mutations(tree)
    return {name for name in inherited | declared if not star_import and name not in bindings and name not in mutated
            and (stores.count(name) == 0 or (stores.count(name) == 1 and name in declared))}


def _source_call(call: ast.Call, target: str, aliases: dict[str, str], module: str,
                 false_kinds: set[str]) -> bool:
    if not target.startswith("store.compiled."):
        return _source_target(target)
    if target == "store.compiled.Kind":
        return not _false_policy(call, aliases, module)
    if target.rsplit('.', 1)[-1] == 'materialized':
        return True
    if target.rsplit(".", 1)[-1] not in ("validate", "validate_one", "known_hashes", "collection", "derive", "materialized"):
        return False
    kind = call.args[0] if call.args else next((kw.value for kw in call.keywords if kw.arg == "kind"), None)
    return not ((isinstance(kind, ast.Name) and kind.id in false_kinds)
                or (kind is not None and _false_policy(kind, aliases, module)))


def _references(node: ast.expr, aliases: dict[str, set[str]], module: str) -> set[str]:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, {f"{module}.{node.id}"})
    if isinstance(node, ast.Attribute):
        return {f"{parent}.{node.attr}" for parent in _references(node.value, aliases, module)}
    if isinstance(node, ast.Call):
        return _references(node.func, aliases, module)
    return set()


def _other_binding(node: ast.AST) -> str | None:
    if isinstance(node, ast.arg):
        return node.arg
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                         ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return node.name
    if isinstance(node, ast.MatchMapping):
        return node.rest
    return None


def _proof_aliases(possible: dict[str, set[str]]) -> dict[str, str]:
    # A False argument proves policy only if its constructor's origin has
    # exactly one ordinary target. Parameter/definition/import ambiguity must
    # never be mistaken for the last observed compiled.Kind binding.
    return {} if "*" in possible else {
        name: next(iter(targets)) for name, targets in possible.items() if len(targets) == 1}


class _AliasGraph:
    def __init__(self, module: str, initial: dict[str, set[str]]):
        self.module = module
        self.origins = {name: targets.copy() for name, targets in initial.items()}
        self.expressions: dict[str, list[ast.expr]] = {}
        self.resolved: dict[str, set[str]] = {}

    def value_targets(self, value: ast.expr, paths: dict) -> set[tuple[str, frozenset[str]]]:
        if isinstance(value, ast.Name):
            return paths.get(value.id, {(f"{self.module}.{value.id}", frozenset())})
        if isinstance(value, ast.Attribute):
            return {(f"{parent}.{value.attr}", visited)
                    for parent, visited in self.value_targets(value.value, paths)}
        if isinstance(value, ast.Call):
            return self.value_targets(value.func, paths)
        return {(f"{self.module}.unknown_expression", frozenset())}

    def extend(self, name: str, paths: dict) -> bool:
        targets = paths.setdefault(name, set())
        before = targets.copy()
        for expression in self.expressions.get(name, ()):
            for target, visited in self.value_targets(expression, paths).copy():
                # Carry binding names along each simple path. A back-edge
                # adds an unknown alternative without dropping concrete
                # helper targets that reach another member of the cycle.
                resolved_target = f"{self.module}.unknown_binding.{name}" if name in visited else target
                targets.add((resolved_target, visited | {name}))
        if len(targets) > 128:
            # An exhausted proof budget fails closed, independent of order.
            targets = {("unresolved_source.validate", frozenset({name})),
                       (f"{self.module}.unknown_binding.{name}", frozenset({name}))}
            paths[name] = targets
        return targets != before

    def resolve_all(self, order: list[str] | None = None) -> dict[str, set[str]]:
        names = list(self.origins.keys() | self.expressions.keys()) if order is None else order
        paths = {name: {(target, frozenset()) for target in targets}
                 for name, targets in self.origins.items()}
        # Every concrete simple path has at most one edge per binding. Unlike
        # visiting-dependent DFS memoization, this fixed point gives each
        # cycle member all reachable concrete targets under every hash seed.
        for _ in range(len(names) + 1):
            changed = [self.extend(name, paths) for name in names]
            if not any(changed):
                break
        self.resolved = {name: {target for target, _visited in targets}
                         for name, targets in paths.items()}
        return self.resolved


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, *_COMPREHENSIONS)


def _definition_inputs(node: ast.AST) -> list[ast.expr]:
    # Defaults/decorators/annotations execute where a definition is made,
    # whereas its body and parameter bindings belong to the new scope.
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = node.args
        expressions = [*args.defaults, *(value for value in args.kw_defaults if value is not None)]
        parameters = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        parameters += [arg for arg in (args.vararg, args.kwarg) if arg is not None]
        expressions += [arg.annotation for arg in parameters if arg.annotation is not None]
        if not isinstance(node, ast.Lambda):
            expressions += node.decorator_list
            if node.returns is not None:
                expressions.append(node.returns)
        return expressions
    if isinstance(node, _COMPREHENSIONS):
        return [node.generators[0].iter]
    if isinstance(node, ast.ClassDef):
        return [*node.bases, *(item.value for item in node.keywords), *node.decorator_list]
    return []


def _escaping_assignments(tree: ast.AST):
    # Iteration targets stay local to comprehensions; assignment expressions
    # bind in their enclosing scope. A nested lambda owns its own assignments.
    pending = list(ast.iter_child_nodes(tree))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            pending.extend(_definition_inputs(node))
            continue
        if isinstance(node, ast.NamedExpr):
            yield node
        else:
            pending.extend(ast.iter_child_nodes(node))


def _owned_nodes(tree: ast.AST):
    pending = [tree]
    while pending:
        node = pending.pop()
        yield node
        if node is not tree and isinstance(node, _SCOPES):
            pending.extend(_definition_inputs(node))
            if isinstance(node, _COMPREHENSIONS):
                pending.extend(_escaping_assignments(node))
            continue
        if node is tree and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            pending.extend([*args.posonlyargs, *args.args, *args.kwonlyargs])
            pending.extend(arg for arg in (args.vararg, args.kwarg) if arg is not None)
            pending.extend([node.body] if isinstance(node, ast.Lambda) else node.body)
        elif node is tree and isinstance(node, ast.ClassDef):
            pending.extend(node.body)
        elif node is tree and isinstance(node, _COMPREHENSIONS):
            pending.extend([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt])
            for index, generator in enumerate(node.generators):
                pending.extend([generator.target, *generator.ifs])
                if index:
                    pending.append(generator.iter)
        elif not isinstance(node, ast.arg):
            pending.extend(ast.iter_child_nodes(node))


def _binding_graph(tree: ast.AST, module: str, initial: dict[str, set[str]],
                   *, owned_scope: bool = False) -> _AliasGraph:
    graph = _AliasGraph(module, initial)
    handled = set()
    nodes = list(_owned_nodes(tree) if owned_scope else ast.walk(tree))
    for node in nodes:
        other = _other_binding(node)
        if other:
            graph.origins.setdefault(other, set()).add(f"{module}.unknown_binding.{other}")
        for name, target in _imports(node, module).items() if isinstance(
                node, (ast.Import, ast.ImportFrom)) else []:
            graph.origins.setdefault(name, set()).add(target)
        for target, value in _bindings(node):
            if isinstance(target, ast.Name):
                handled.add(id(target))
                graph.expressions.setdefault(target.id, []).append(value)
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and id(node) not in handled:
            graph.origins.setdefault(node.id, set()).add(f"{module}.unknown_binding.{node.id}")
    return graph


def _mutation_root(node: ast.AST) -> str | None:
    if not isinstance(node, (ast.Attribute, ast.Subscript)) or not isinstance(node.ctx, ast.Store):
        return None
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _global_names_written(tree: ast.AST) -> set[str]:
    mutations = set()
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nodes = list(_owned_nodes(function))
            declared = {name for node in nodes if isinstance(node, ast.Global) for name in node.names}
            written = {node.id for node in nodes if isinstance(node, ast.Name)
                       and isinstance(node.ctx, (ast.Store, ast.Del))}
            written.update(name for node in nodes if (name := _other_binding(node)))
            written.update(name for node in nodes if isinstance(node, (ast.Import, ast.ImportFrom))
                           for name in _imports(node, ''))
            mutations.update(declared & written)
    return mutations


def _nonlocal_names_written(tree: ast.AST) -> set[str]:
    mutations = set()

    def visit(scope: ast.AST, shadowed: set[str]) -> None:
        nodes = list(_owned_nodes(scope))
        declared = {name for node in nodes if isinstance(node, ast.Nonlocal) for name in node.names}
        globals_ = {name for node in nodes if isinstance(node, ast.Global) for name in node.names}
        written = {node.id for node in nodes if isinstance(node, ast.Name)
                   and isinstance(node.ctx, (ast.Store, ast.Del))}
        written.update(name for node in nodes if (name := _other_binding(node)))
        written.update(name for node in nodes if isinstance(node, (ast.Import, ast.ImportFrom))
                       for name in _imports(node, ''))
        mutations.update((declared & written) - shadowed)
        # A nearer function binding owns deeper nonlocal writes. Class locals
        # are not closure bindings for their methods, just as at runtime.
        nearer = shadowed if isinstance(scope, ast.ClassDef) else shadowed | (written - declared - globals_)
        for child in nodes:
            if child is not scope and isinstance(child, _SCOPES):
                visit(child, nearer)

    for child in _owned_nodes(tree):
        if child is not tree and isinstance(child, _SCOPES):
            visit(child, set())
    return mutations


def _namespace_mutations(tree: ast.AST) -> set[str]:
    return (_global_names_written(tree) | _nonlocal_names_written(tree)
            | {root for node in _owned_nodes(tree) if (root := _mutation_root(node)) is not None})


def _invalidate_origins(tree: ast.AST, module: str, possible: dict[str, set[str]]) -> set[str]:
    mutations = _namespace_mutations(tree)
    if any(target.startswith('store.compiled') for name in mutations
           for target in possible.get(name, ())):
        # An object/namespace mutation may affect aliases to the same class,
        # including a separately imported Kind. Conservative across helpers.
        mutations.update(name for name, targets in possible.items()
                         if any(target.startswith('store.compiled') for target in targets))
    for name in mutations:
        possible.setdefault(name, set()).add(f"{module}.unknown_binding.{name}")
    return mutations


def _possible_aliases(tree: ast.AST, module: str,
                      initial: dict[str, set[str]]) -> dict[str, set[str]]:
    # Model bindings as a finite graph. Definitions/parameters and every
    # unmodelled Store target are unknown alternatives, never evidence that
    # an earlier compiled.Kind binding is still the only constructor origin.
    # Recursive aliases terminate at an unknown edge rather than expanding
    # dotted strings on every pass (p = p.parent; p = p.resolve()).
    graph = _binding_graph(tree, module, initial, owned_scope=True)
    possible = graph.resolve_all()
    _invalidate_origins(tree, module, possible)
    return possible


def _source_target(target: str) -> bool:
    return (target.startswith("unresolved_source.")
            or target.rsplit(".", 1)[-1] in ("validate", "validate_one", "known_hashes", "collection", "derive", "materialized"))


def _unproven_references(node: ast.AST, module: str, possible: dict[str, set[str]],
                         proven: set[int], exports: dict) -> list[tuple[ast.AST, str, bool]]:
    # Forwarding/extracting a source method has no local policy argument.
    # Ordinary helper references also enter traversal: a nonlocal assignment
    # may forward one without retaining its origin in the caller's graph.
    return [(item, target, _source_target(target)) for item in _owned_nodes(node)
            if isinstance(item, (ast.Name, ast.Attribute)) and id(item) not in proven
            for raw in _references(item, possible, module)
            for target in _export_targets(raw, exports, set())]


def _decision_calls(node: ast.AST, module: str, false_kinds: set[str],
                    possible: dict[str, set[str]], exports: dict) -> list[tuple[ast.AST, str, bool]]:
    enclosing_possible, enclosing_false = possible, false_kinds
    possible = _scope_origins(node, module, possible, exports.get(module, possible))
    _invalidate_origins(node, module, possible)
    resolved = {name: {target for raw in targets for target in _export_targets(raw, exports, set())}
                for name, targets in possible.items()}
    aliases = _proof_aliases(resolved)
    false_kinds = _literal_policies(node, module, aliases, false_kinds)
    calls: list[tuple[ast.AST, str, bool]] = []
    proven = set()
    for call in (item for item in _owned_nodes(node) if isinstance(item, ast.Call)):
        unresolved = (isinstance(call.func, ast.Name) and call.func.id == "getattr"
                      and bool(call.args) and any(target.startswith("store.compiled")
                      for target in _references(call.args[0], possible, module)))
        targets = {target for raw in _references(call.func, possible, module)
                   for target in _export_targets(raw, exports, set())}
        blocked = {target: unresolved or _source_call(call, target, aliases, module, false_kinds)
                   for target in targets}
        if targets and not any(blocked.values()):
            proven.add(id(call.func))
        calls.extend((call, target, blocked[target]) for target in targets)
    calls += _unproven_references(node, module, possible, proven, exports)
    for child in _owned_nodes(node):
        if child is not node and isinstance(child, _SCOPES):
            # Class attributes are not closure bindings for methods.
            parent_possible = enclosing_possible if isinstance(node, ast.ClassDef) else possible
            parent_false = enclosing_false if isinstance(node, ast.ClassDef) else false_kinds
            calls += _decision_calls(child, module, parent_false, parent_possible, exports)
    return calls


def _scope_origins(tree: ast.AST, module: str, inherited: dict[str, set[str]],
                   globals_map: dict[str, set[str]]) -> dict[str, set[str]]:
    nodes = list(_owned_nodes(tree))
    nonlocals = {name for node in nodes if isinstance(node, (ast.Global, ast.Nonlocal)) for name in node.names}
    bound = {node.id for node in nodes if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
    bound.update(name for node in nodes if (name := _other_binding(node)))
    bound.update(name for node in nodes if isinstance(node, (ast.Import, ast.ImportFrom))
                 for name in _imports(node, module))
    initial = inherited.copy() if isinstance(tree, (ast.Module, ast.ClassDef)) else {
        name: targets for name, targets in inherited.items() if name not in bound - nonlocals}
    initial.update((name, globals_map[name]) for node in nodes if isinstance(node, ast.Global)
                   for name in node.names if name in globals_map)
    return _binding_graph(tree, module, initial, owned_scope=True).resolve_all()


def _export_targets(target: str, exports: dict, visited: set[str],
                    budget: list[int] | None = None) -> set[str]:
    budget = [256] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or target in visited or len(visited) >= 64:
        return {"unresolved_source.namespace"}
    modules = [name for name in exports if target.startswith(name + '.')]
    if not modules:
        return {target}
    module = max(modules, key=len)
    name, separator, suffix = target[len(module) + 1:].partition('.')
    origins = exports[module].get(name)
    if not origins:
        return {target}
    resolved = set()
    for origin in origins:
        projected = origin + separator + suffix
        resolved.update({target} if projected == target else
                        _export_targets(projected, exports, visited | {target}, budget))
        if len(resolved) > 128:
            return {"unresolved_source.namespace"}
    return resolved


def _constructor_slot(node: ast.AST) -> bool:
    return ((isinstance(node, ast.Attribute) and node.attr in {"Kind", "__new__", "__init__"})
            or (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                and node.slice.value == "Kind"))


def _opaque_constructor_receiver(node: ast.AST, receivers: set[str], exports: dict) -> bool:
    if not _constructor_slot(node):
        return False
    # A parameter or opaque factory may hand the helper the shared module.
    # Only an identified other module or standard SimpleNamespace constructor
    # proves this constructor-slot mutation independent of compiled.
    independent = {"types.SimpleNamespace", "types.SimpleNamespace.__dict__"}
    return not receivers or any(target not in exports and target not in independent for target in receivers)


def _mutation_receivers(nodes: list[ast.AST], module: str, local: dict, exports: dict) -> set[str]:
    targets = set()
    for node in nodes:
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, ast.Store):
            # Keep the full receiver, rather than only its root name:
            # aliases.cache.Kind changes the exported compiled namespace.
            receivers = set()
            for target in _references(node.value, local, module):
                receivers.update(_export_targets(target, exports, set()))
            targets.update(receivers)
            if _opaque_constructor_receiver(node, receivers, exports):
                targets.add("unresolved_source.namespace")
            root = _mutation_root(node)
            targets.update(local.get(root, ()))
    return targets


def _compiled_namespace_written(tree: ast.AST, module: str, possible: dict[str, set[str]],
                                globals_map: dict[str, set[str]] | None = None,
                                *, exports: dict | None = None) -> bool:
    # Each mutation belongs to one lexical scope. A local alias in one helper
    # cannot give a same-named, independent local in another a compiled origin.
    globals_map = possible if globals_map is None else globals_map
    nodes = list(_owned_nodes(tree))
    local_origins = _scope_origins(tree, module, possible, globals_map)
    receivers = _mutation_receivers(nodes, module, local_origins, exports or {})
    if any(target in {"store.compiled", "store.compiled.Kind", "store.compiled.__dict__"}
           or target.startswith(("unresolved_source.", "store.compiled.Kind.", "store.compiled.__dict__."))
           for target in receivers):
        return True
    parent = possible if isinstance(tree, ast.ClassDef) else local_origins
    return any(_compiled_namespace_written(node, module, parent, globals_map, exports=exports) for node in nodes
               if node is not tree and isinstance(node, _SCOPES))


def _shared_namespace_receivers(tree: ast.AST, module: str, possible: dict, globals_map: dict,
                                exports: dict) -> set[str]:
    nodes = list(_owned_nodes(tree))
    local = _scope_origins(tree, module, possible, globals_map)
    found = _mutation_receivers(nodes, module, local, exports)
    parent = possible if isinstance(tree, ast.ClassDef) else local
    for node in nodes:
        if node is not tree and isinstance(node, _SCOPES):
            found.update(_shared_namespace_receivers(node, module, parent, globals_map, exports))
    return found


def _namespace_functions(tree: ast.AST, prefix: str = ''):
    for node in _owned_nodes(tree):
        if node is tree:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield prefix + node.name, node
        elif isinstance(node, ast.ClassDef):
            # Methods resolve globals in their module, not the class body.
            # Qualified indexing follows imported Reader.digest wrappers too.
            yield from _namespace_functions(node, prefix + node.name + '.')


def _namespace_classes(tree: ast.AST, prefix: str = ''):
    for node in _owned_nodes(tree):
        if node is not tree and isinstance(node, ast.ClassDef):
            yield prefix + node.name, node
            yield from _namespace_classes(node, prefix + node.name + '.')


def _class_exports(tree: ast.AST, module: str, inherited: dict, globals_map: dict,
                   prefix: str = '') -> dict:
    namespaces = {}
    for node in _owned_nodes(tree):
        if node is tree or not isinstance(node, ast.ClassDef):
            continue
        namespace = f'{module}.{prefix}{node.name}'
        definitions = {child.name: {f'{namespace}.{child.name}'} for child in _owned_nodes(node)
                       if child is not node and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        possible = _scope_origins(node, module, {**inherited, **definitions}, globals_map)
        namespaces[namespace] = possible
        namespaces.update(_class_exports(node, module, possible, globals_map, prefix + node.name + '.'))
    return namespaces


def _revoke_shared_classes(modules: list, exports: dict) -> None:
    # Class objects are shared too: importing a setter can replace a callable
    # after its declaration was indexed. A bounded declaration graph cannot
    # prove assignment order, so revoke that namespace rather than trusting
    # the old False-policy helper from a separately imported class alias.
    receivers = set()
    for module, tree, possible, _false in modules:
        receivers.update(_shared_namespace_receivers(tree, module, possible, possible, exports))
    for namespace in set(exports) - {module for module, _tree, _possible, _false in modules}:
        if any(target == namespace or target.startswith(namespace + '.') for target in receivers):
            for origins in exports[namespace].values():
                origins.add('unresolved_source.namespace')


def _decision_functions(sources: dict[str, str]) -> tuple[dict, dict]:
    modules = []
    for module, source in sources.items():
        tree = ast.parse(source)
        globals_only = ast.Module(body=[expression for node in tree.body for expression in
            ([ast.Expr(value=value) for value in _definition_inputs(node)] if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else [node])], type_ignores=[])
        definitions = {node.name: {f"{module}.{node.name}"} for node in _owned_nodes(tree)
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        possible = _possible_aliases(globals_only, module, definitions)
        mutated = _global_names_written(tree)
        for name in mutated:
            possible.setdefault(name, set()).add(f"{module}.unknown_binding.{name}")
        false_kinds = _literal_policies(globals_only, module, _proof_aliases(possible), set()) - mutated
        modules.append((module, tree, possible, false_kinds))
    # An imported module/class is shared across modules. Revocation confined
    # to the setter's alias map would leave its caller's constructor trusted.
    exports = {module: possible for module, _tree, possible, _false in modules}
    for module, tree, possible, _false in modules:
        exports.update(_class_exports(tree, module, possible, possible))
    _revoke_shared_classes(modules, exports)
    changed = any(_compiled_namespace_written(tree, module, possible, exports=exports)
                  for module, tree, possible, _false in modules)
    functions = {}
    for module, tree, possible, original_false in modules:
        false_kinds = original_false
        if changed:
            for name, targets in possible.items():
                if any(target.startswith("store.compiled") for target in targets):
                    targets.add(f"{module}.unknown_binding.{name}")
            false_kinds = set()
        # Module control flow shares the module namespace. Keep every captured
        # definition there, while the owned walker excludes nested scopes.
        for qualified, node in _namespace_functions(tree):
            functions.setdefault(f"{module}.{qualified}", []).append((module, node, false_kinds, possible))
    return functions, exports, _constructor_classes(modules, exports)


def _constructor_classes(modules: list, exports: dict) -> dict:
    classes = {}
    for module, tree, _possible, _false in modules:
        for qualified, node in _namespace_classes(tree):
            namespace = f'{module}.{qualified}'
            bases = set()
            for base in node.bases:
                for target in _references(base, exports[namespace], module):
                    bases.update(_export_targets(target, exports, set()))
            classes[namespace] = bases
    return classes


def _constructor_targets(target: str, exports: dict, classes: dict):
    constructors, seen = [target], set()
    while constructors:
        constructor = constructors.pop()
        if constructor in seen:
            continue
        if len(seen) >= 64:
            yield 'unresolved_source.constructor_ancestry'
            return
        seen.add(constructor)
        constructors.extend(classes.get(constructor, ()))
        for suffix in ('', '.__new__', '.__init__'):
            yield from _export_targets(constructor + suffix, exports, set())


def _decision_violations(sources: dict[str, str]) -> list[str]:
    # Resolve ordinary imports, helpers and Batch/context aliases. Dynamic
    # call construction is not a general data-flow proof; named decision sites
    # and directly resolvable wrappers are the deliberately stated boundary.
    functions, exports, classes = _decision_functions(sources)
    pending = list(_DECISION_SITES)
    visited = set()
    failures = []
    while pending:
        name = pending.pop()
        if name in visited or name not in functions:
            continue
        visited.add(name)
        for module, node, false_kinds, possible in functions[name]:
            definitions = ast.Module(body=[ast.Expr(value=value) for value in _definition_inputs(node)], type_ignores=[])
            calls = _decision_calls(definitions, module, false_kinds, possible, exports)
            calls += _decision_calls(node, module, false_kinds, possible, exports)
            for _call, target, blocked in calls:
                if blocked:
                    failures.append(f"{name}: persisted source primitive {target}")
                # Constructor aliases and declared base classes use the same
                # bounded export graph as methods. Conservatively include base
                # bodies even with an override: uncertain effects are refused.
                for called in _constructor_targets(target, exports, classes):
                    if called.startswith('unresolved_source.'):
                        failures.append(f'{name}: unresolved constructor origin')
                    elif called in functions:
                        pending.append(called)
    return failures


def test_unresolved_compiled_source_target_is_not_assumed_safe():
    sources = {"store.entities": "from . import compiled as memo\n"
               "def entity_hash(p): return getattr(memo, 'validate')(kind, [p])"}
    assert _decision_violations(sources)


def test_decision_entry_cannot_declare_persisted_enabled_policy():
    sources = {"store.entities": "from . import compiled as memo\n"
               "def entity_hash(p): return memo.Kind('decision', 1, (), (), True)"}
    assert _decision_violations(sources)


@pytest.mark.parametrize("shadow", ["import", "argument", "loop"])
def test_false_decision_policy_cannot_survive_name_shadowing(shadow):
    definition = "from . import compiled as cache\nK = cache.Kind('decision', 1, (), (), False)\n"
    body = "def entity_hash(p): return cache.Batch(p).validate_one(K, [p])"
    if shadow == "import":
        definition += "from review_policy import PERSISTED as K\n"
    elif shadow == "argument":
        body = "def entity_hash(p, K): return cache.Batch(p).validate_one(K, [p])"
    else:
        body = "def entity_hash(p):\n for K in kinds:\n  return cache.Batch(p).validate_one(K, [p])"
    assert _decision_violations({"store.entities": definition + body})


def test_later_alias_rebinding_cannot_hide_an_earlier_source_call():
    sources = {"store.entities": "from . import compiled as cache\n"
               "def entity_hash(p):\n"
               " with cache.batch(p) as request:\n"
               "  read = request.validate_one\n"
               "  result = read(kind, cache.Input('body', p))\n"
               "  read = str\n"
               "  return result"}
    assert _decision_violations(sources)


@pytest.mark.parametrize("sql", [
    "'SELECT content_hash FROM sources WHERE path IN (?)'",
    "SOURCE_SQL",
])
def test_public_source_reader_requires_live_paths_even_with_a_bounded_query(sql):
    source = ("SOURCE_SQL = 'SELECT content_hash FROM sources WHERE path IN (?)'\n"
              f"def hashes(): return conn.execute({sql}, ('Realm.md',)).fetchall()")
    assert _violations(source, "store.compiled")


def test_annotated_binding_cannot_hide_a_compiled_source_alias():
    source = ("from . import compiled as cache\n"
              "def entity_hash(p):\n"
              " with cache.batch(p) as request:\n"
              "  read: object = request.validate_one\n"
              "  return read(kind, cache.Input('body', p))")
    assert _decision_violations({"store.entities": source})


def test_annotated_assignment_cannot_inherit_false_policy_proof():
    source = ("from . import compiled as cache\nK = cache.Kind('decision', 1, (), (), False)\n"
              "def entity_hash(p):\n K: cache.Kind = HOT\n return cache.Batch(p).validate_one(K, [p])")
    assert _decision_violations({"store.entities": source})


def test_nested_alias_binding_propagates_to_an_outer_delegate():
    source = ("from . import compiled as cache\n"
              "def entity_hash(p):\n"
              " with cache.batch(p) as request:\n"
              "  if True:\n   read = request.validate_one\n"
              "  delegated = read\n"
              "  return delegated(kind, cache.Input('body', p))")
    assert _decision_violations({"store.entities": source})


def test_ambiguous_constructor_alias_cannot_prove_inline_false_policy():
    sources = {"store.entities": "from . import compiled as cache\nfrom .review_policy import make_kind\n"
               "def entity_hash(p):\n"
               " ctor = make_kind\n"
               " result = cache.Batch(p).validate_one(ctor('decision', 1, (), (), False), cache.Input('body', p))\n"
               " ctor = cache.Kind\n return result",
               "store.review_policy": "def make_kind(*args): return HOT"}
    assert _decision_violations(sources)


def test_ambiguous_constructor_alias_cannot_prove_a_named_false_policy():
    sources = {"store.entities": "from . import compiled as cache\nfrom .review_policy import make_kind\n"
               "def entity_hash(p):\n"
               " ctor = cache.Kind\n"
               " if True:\n  ctor = make_kind\n"
               " K = ctor('decision', 1, (), (), False)\n"
               " result = cache.Batch(p).validate_one(K, cache.Input('body', p))\n"
               " ctor = cache.Kind\n return result",
               "store.review_policy": "def make_kind(*args): return HOT"}
    assert _decision_violations(sources)


def test_constructor_parameter_cannot_inherit_compiled_kind_origin():
    source = ("from . import compiled as cache\nfrom .compiled import Kind\n"
              "def entity_hash(p, Kind):\n"
              " return cache.Batch(p).validate_one(Kind('decision', 1, (), (), False), cache.Input('body', p))")
    assert _decision_violations({"store.entities": source})


def test_forwarded_source_method_reference_requires_proven_false_policy():
    source = ("from . import compiled as cache\n"
              "def entity_hash(p):\n"
              " with cache.batch(p) as request:\n"
              "  return invoke(request.validate_one, kind, cache.Input('body', p))\n"
              "def invoke(read, kind, input): return read(kind, input)")
    assert _decision_violations({"store.entities": source})


def test_unpacking_cannot_hide_a_source_method_reference():
    source = ("from . import compiled as cache\n"
              "def entity_hash(p):\n"
              " with cache.batch(p) as request:\n"
              "  read, unused = request.validate_one, 0\n"
              "  return read(kind, cache.Input('body', p))")
    assert _decision_violations({"store.entities": source})


def test_helper_returning_batch_cannot_hide_a_named_source_primitive():
    source = ("from . import compiled as cache\n"
              "def entity_hash(p): return request(p).validate_one(kind, cache.Input('body', p))\n"
              "def request(p): return cache.Batch(p)")
    assert _decision_violations({"store.entities": source})


def test_sources_query_cannot_suppress_public_artifact_key_requirement():
    source = ("def lookup(paths):\n"
              " conn.execute('SELECT content_hash FROM sources WHERE path IN (?)', ('Realm.md',))\n"
              " return conn.execute('SELECT payload FROM artifacts WHERE kind=? AND key_digest=?', ('search','a')).fetchall()")
    assert _violations(source, "store.compiled")


def test_conditional_import_is_resolved_before_dependent_module_alias():
    source = ("if True:\n from . import compiled as cache\n"
              "read = cache.Batch(root).validate_one\n"
              "def entity_hash(p): return read(kind, cache.Input('body', p))")
    assert _decision_violations({"store.entities": source})


@pytest.mark.parametrize("definition", [
    "def ctor(*args): return HOT",
    "class ctor:\n def __new__(cls, *args): return HOT",
])
def test_global_definition_cannot_preserve_constructor_origin(definition):
    source = ("from . import compiled as cache\nctor = cache.Kind\n" + definition + "\n"
              "def entity_hash(p): return cache.Batch(p).validate_one(ctor('decision', 1, (), (), False), cache.Input('body', p))")
    assert _decision_violations({"store.entities": source})


@pytest.mark.parametrize("rebinding", [
    "for ctor in factories:\n  return request.validate_one(ctor('decision', 1, (), (), False), cache.Input('body', p))",
    "if (ctor := make_kind):\n  return request.validate_one(ctor('decision', 1, (), (), False), cache.Input('body', p))",
    "ctor, unused = make_kind, None\n return request.validate_one(ctor('decision', 1, (), (), False), cache.Input('body', p))",
    "alternate = factories[0]\n ctor = alternate\n return request.validate_one(ctor('decision', 1, (), (), False), cache.Input('body', p))",
])
def test_other_constructor_bindings_cannot_preserve_false_policy_proof(rebinding):
    source = ("from . import compiled as cache\n"
              "def entity_hash(p):\n ctor = cache.Kind\n request = cache.Batch(p)\n " + rebinding)
    assert _decision_violations({"store.entities": source})


def test_cyclic_aliases_have_bounded_targets():
    source = "p = p.parent\np = p.resolve()\n" + "\n".join(f"unused{i} = 0" for i in range(6))
    aliases = _possible_aliases(ast.parse(source), "store.entities", {"p": {"argument.p"}})
    assert len(aliases['p']) < 20


@pytest.mark.parametrize("order", [False, True])
def test_cyclic_helper_aliases_preserve_reachable_source_reader(order):
    source = ("from . import helper\n"
              "def entity_hash(p):\n later = helper.read\n earlier = later\n later = earlier\n return earlier(p)")
    # Both traversal orders expose the context-dependent memoization defect;
    # the guard must not change its verdict with Python's randomized hash seed.
    tree = ast.parse(source)
    function = tree.body[1]
    initial = {'helper': {'store.helper'}}
    graph = _binding_graph(function, "store.entities", initial)
    graph.resolve_all(["earlier", "later"] if order else ["later", "earlier"])
    assert 'store.helper.read' in graph.resolved['earlier']
    sources = {"store.entities": source,
               "store.helper": "from . import compiled as cache\ndef read(p): return cache.Batch(p).validate_one(HOT, cache.Input('body', p))"}
    assert _decision_violations(sources)



def test_long_alias_chain_keeps_reachable_source_target():
    source = ("from . import compiled as cache\nrequest = cache.Batch(root)\n"
              + "\n".join(f"a{i} = a{i + 1}" for i in range(65))
              + "\na65 = request.validate_one\ndef entity_hash(p): return a0(HOT, cache.Input('body', p))")
    graph = _binding_graph(ast.parse(source), "store.entities", {'cache': {'store.compiled'}})
    aliases = graph.resolve_all(['a0', *sorted(graph.origins.keys() | graph.expressions.keys() - {'a0'})])
    assert 'store.compiled.Batch.validate_one' in aliases['a0']
    assert _decision_violations({"store.entities": source})


@pytest.mark.parametrize("binding", ['ctor', 'K'])
def test_helper_global_write_revokes_caller_policy_proof(binding):
    declaration = "ctor = cache.Kind" if binding == 'ctor' else "K = cache.Kind('decision', 1, (), (), False)"
    value = "make_kind" if binding == 'ctor' else "HOT"
    argument = "ctor('decision', 1, (), (), False)" if binding == 'ctor' else 'K'
    source = ("from . import compiled as cache\n" + declaration
              + f"\ndef swap():\n global {binding}\n {binding} = {value}\n"
              + "def entity_hash(p):\n swap()\n"
              + f" return cache.Batch(p).validate_one({argument}, cache.Input('body', p))")
    assert _decision_violations({"store.entities": source})


@pytest.mark.parametrize("binding", ['cache.Kind', 'Kind.__new__'])
def test_constructor_attribute_write_revokes_false_policy_proof(binding):
    constructor = 'cache.Kind' if binding == 'cache.Kind' else 'Kind'
    source = ("from . import compiled as cache\nfrom .compiled import Kind\n"
               f"def swap(): {binding} = make_kind\n"
               "def entity_hash(p):\n swap()\n"
               f" return cache.Batch(p).validate_one({constructor}('decision', 1, (), (), False), cache.Input('body', p))")
    assert _decision_violations({"store.entities": source})



def test_alias_budget_exhaustion_cannot_inherit_false_policy_allowance():
    assignments = '\n'.join(f'read = helper.read{i}' for i in range(130))
    source = ("from . import compiled as cache\nfrom . import helper\n" + assignments
              + "\ndef entity_hash(p): return read(cache.Kind('live', 1, (), (), False), p)")
    helpers = ('def actual_read(kind, p): return request.validate_one(HOT, p)\n'
               + '\n'.join(f'def read{i}(kind,p): return actual_read(kind,p)' for i in range(130)))
    assert _decision_violations({'store.entities': source, 'store.helper': helpers})



def test_alias_budget_marker_survives_attribute_projection():
    source = ('from . import compiled as cache\n'
              + '\n'.join(f'from . import helper{i}\ndelegate = helper{i}' for i in range(130))
              + "\nread = delegate.read\ndef entity_hash(p): return read(cache.Kind('live',1,(),(),False), p)")
    sources = {'store.entities': source, **{
        f'store.helper{i}': 'def read(kind,p): return request.validate_one(HOT,p)' for i in range(130)}}
    assert _decision_violations(sources)



@pytest.mark.parametrize('separate_module', [False, True])
def test_helper_local_namespace_alias_revokes_caller_constructor(separate_module):
    setter = "def swap():\n alias = cache\n alias.Kind = make_kind\n"
    source = ("from . import compiled as cache\n"
              + ("from . import helper\n" if separate_module else setter)
              + "def entity_hash(p):\n " + ("helper.swap()" if separate_module else "swap()")
              + "\n return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),False), cache.Input('body',p))")
    sources = {'store.entities': source}
    if separate_module:
        sources['store.helper'] = 'from . import compiled as cache\n' + setter
    assert _decision_violations(sources)



@pytest.mark.parametrize('module_alias', [False, True])
def test_unrelated_local_mutation_cannot_revoke_compiled_constructor(module_alias):
    source = ("from . import compiled as cache\nfrom types import SimpleNamespace\n"
              + ('alias = cache\n' if module_alias else '')
              + "def first():\n alias = cache\n return alias\n"
              + "def second():\n alias = SimpleNamespace()\n alias.Kind = str\n"
              + "def entity_hash(p):\n first()\n second()\n"
              + " return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),False),cache.Input('body',p))")
    assert not _decision_violations({'store.entities': source})



@pytest.mark.parametrize('target', ['aliases.cache.Kind', 'aliases.Factory.__new__', "aliases.cache.__dict__['Kind']"])
def test_reexported_constructor_namespace_revokes_caller_proof(target):
    sources = {
        'store.entities': "from . import compiled as cache\nfrom . import helper\ndef entity_hash(p):\n helper.swap()\n return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),False),cache.Input('body',p))",
        'store.aliases': 'from . import compiled as cache\nfrom .compiled import Kind as Factory',
        'store.helper': f'from . import aliases\ndef swap(): {target} = make_kind',
    }
    assert _decision_violations(sources)



@pytest.mark.parametrize('setter', [
    'def swap(namespace=cache): namespace.Kind = make_kind',
    'def namespace(): return cache\ndef swap(): namespace().Kind = make_kind',
    'from types import SimpleNamespace\ndef swap():\n proxy = SimpleNamespace(cache=cache)\n proxy.cache.Kind = make_kind',
])
def test_opaque_constructor_receiver_cannot_preserve_caller_proof(setter):
    sources = {
        'store.entities': "from . import compiled as cache\nfrom . import helper\ndef entity_hash(p):\n helper.swap()\n return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),False),cache.Input('body',p))",
        'store.helper': 'from . import compiled as cache\n' + setter,
    }
    assert _decision_violations(sources)



@pytest.mark.parametrize('invoke', ['aliases.read(p)', 'read(p)'])
def test_reexported_helper_keeps_decision_traversal(invoke):
    sources = {
        'store.entities': 'from . import aliases\nfrom .aliases import read\ndef entity_hash(p): return ' + invoke,
        'store.aliases': 'from .helper import read',
        'store.helper': "from . import compiled as cache\ndef read(p): return cache.Batch(p).validate_one(HOT,cache.Input('body',p))",
    }
    assert _decision_violations(sources)



def test_class_body_fallback_preserves_compiled_namespace_mutation():
    source = ("from . import compiled as cache\nfrom types import SimpleNamespace\nalias = cache\n"
              "class Setter:\n alias.Kind = make_kind\n alias = SimpleNamespace()\n"
              "def entity_hash(p): return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),False),cache.Input('body',p))")
    assert _decision_violations({'store.entities': source})


def test_class_global_write_revokes_caller_constructor():
    source = ("from . import compiled as cache\nctor = cache.Kind\n"
              "def swap():\n class Setter:\n  global ctor\n  ctor = make_kind\n"
              "def entity_hash(p):\n swap()\n return cache.Batch(p).validate_one(ctor('live',1,(),(),False),cache.Input('body',p))")
    assert _decision_violations({'store.entities': source})


@pytest.mark.parametrize('expression', ['[alias for alias in range(1)]', '{alias for alias in range(1)}',
                                       '{alias: alias for alias in range(1)}', '(alias for alias in range(1))'])
def test_comprehension_local_cannot_pollute_independent_namespace(expression):
    source = ("from . import compiled as cache\nfrom types import SimpleNamespace\n"
              "def second():\n alias = SimpleNamespace()\n " + expression + "\n alias.Kind = str\n"
              "def entity_hash(p):\n second()\n return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),False),cache.Input('body',p))")
    assert not _decision_violations({'store.entities': source})


def test_comprehension_constructor_target_cannot_inherit_outer_false_proof():
    source = ("from . import compiled as cache\nctor = cache.Kind\n"
              "def entity_hash(p): return [cache.Batch(p).validate_one(ctor('live',1,(),(),False),cache.Input('body',p)) for ctor in factories]")
    assert _decision_violations({'store.entities': source})



def test_export_alias_work_budget_fails_closed():
    exports = {'store.aliases': {f'a{i}': {f'store.aliases.a{i + 1}', f'store.aliases.a{i + 2}'}
                                 for i in range(30)}}
    assert 'unresolved_source.namespace' in _export_targets('store.aliases.a0', exports, set())



def test_comprehension_walrus_revokes_enclosing_constructor_proof():
    source = ("from . import compiled as cache\nctor = cache.Kind\n"
              "def entity_hash(p):\n [(ctor := make_kind) for ignored in range(1)]\n"
              " return cache.Batch(p).validate_one(ctor('live',1,(),(),False),cache.Input('body',p))")
    assert _decision_violations({'store.entities': source})


def test_lambda_walrus_cannot_pollute_enclosing_independent_namespace():
    source = ("from . import compiled as cache\nfrom types import SimpleNamespace\n"
              "def second():\n alias = SimpleNamespace()\n [(lambda: (alias := cache)) for ignored in range(1)]\n alias.Kind = str\n"
              "def entity_hash(p):\n second()\n return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),False),cache.Input('body',p))")
    assert not _decision_violations({'store.entities': source})



@pytest.mark.parametrize('binding', ['ctor', 'K'])
def test_nested_nonlocal_write_revokes_enclosing_policy(binding):
    declaration = 'ctor = cache.Kind' if binding == 'ctor' else "K = cache.Kind('live',1,(),(),False)"
    value = 'make_kind' if binding == 'ctor' else 'HOT'
    argument = "ctor('live',1,(),(),False)" if binding == 'ctor' else 'K'
    source = ("from . import compiled as cache\ndef entity_hash(p):\n " + declaration
              + f"\n def swap():\n  nonlocal {binding}\n  {binding} = {value}\n swap()\n"
              + f" return cache.Batch(p).validate_one({argument},cache.Input('body',p))")
    assert _decision_violations({'store.entities': source})


def test_nested_nonlocal_write_targets_nearest_binding():
    source = ("from . import compiled as cache\nfrom types import SimpleNamespace\n"
              "def entity_hash(p):\n ctor = cache.Kind\n"
              " def second():\n  ctor = SimpleNamespace()\n"
              "  def inner():\n   nonlocal ctor\n   ctor = SimpleNamespace()\n  inner()\n second()\n"
              " return cache.Batch(p).validate_one(ctor('live',1,(),(),False),cache.Input('body',p))")
    assert not _decision_violations({'store.entities': source})



def test_nested_nonlocal_write_targets_nearer_parameter():
    source = ("from . import compiled as cache\nfrom types import SimpleNamespace\n"
              "def entity_hash(p):\n ctor = cache.Kind\n"
              " def second(ctor):\n  def inner():\n   nonlocal ctor\n   ctor = SimpleNamespace()\n  inner()\n second(None)\n"
              " return cache.Batch(p).validate_one(ctor('live',1,(),(),False),cache.Input('body',p))")
    assert not _decision_violations({'store.entities': source})



def test_nested_parameter_cannot_revoke_enclosing_named_false_policy():
    source = ("from . import compiled as cache\nfrom types import SimpleNamespace\n"
              "def entity_hash(p):\n K = cache.Kind('live',1,(),(),False)\n"
              " def second(K):\n  def inner():\n   nonlocal K\n   K = SimpleNamespace()\n  inner()\n second(None)\n"
              " return cache.Batch(p).validate_one(K,cache.Input('body',p))")
    assert not _decision_violations({'store.entities': source})



def test_nested_definition_cannot_revoke_enclosing_named_false_policy():
    source = ("from . import compiled as cache\nfrom types import SimpleNamespace\n"
              "def entity_hash(p):\n K = cache.Kind('live',1,(),(),False)\n"
              " def second():\n  def K(): pass\n  def inner():\n   nonlocal K\n   K = SimpleNamespace()\n  inner()\n second()\n"
              " return cache.Batch(p).validate_one(K,cache.Input('body',p))")
    assert not _decision_violations({'store.entities': source})



@pytest.mark.parametrize('definition', ['unused = lambda arg=(ctor := make_kind): arg',
                                        'def unused(arg=(ctor := make_kind)): pass'])
def test_definition_defaults_rebind_enclosing_constructor(definition):
    source = ("from . import compiled as cache\ndef entity_hash(p):\n ctor = cache.Kind\n " + definition
              + "\n return cache.Batch(p).validate_one(ctor('live',1,(),(),False),cache.Input('body',p))")
    assert _decision_violations({'store.entities': source})


def test_module_definition_default_rebinds_global_constructor():
    source = ("from . import compiled as cache\nctor = cache.Kind\n"
              "def unused(arg=(ctor := make_kind)): pass\n"
              "def entity_hash(p): return cache.Batch(p).validate_one(ctor('live',1,(),(),False),cache.Input('body',p))")
    assert _decision_violations({'store.entities': source})



@pytest.mark.parametrize('definition', ['def later(read=read(p)): pass',
                                        'items = [read for read in read(p)]'])
def test_enclosing_scope_entry_keeps_imported_source_helper(definition):
    sources = {'store.entities': 'from .helper import read\ndef entity_hash(p):\n ' + definition + '\n return None',
               'store.helper': 'from . import compiled as cache\ndef read(p): return cache.Batch(p).validate_one(HOT,cache.Input("body",p))'}
    assert _decision_violations(sources)


def test_class_policy_does_not_become_a_method_closure_binding():
    source = ("from . import compiled as cache\ndef entity_hash(p, HOT):\n K = HOT\n"
              " class Reader:\n  K = cache.Kind('live',1,(),(),False)\n"
              "  def read(self): return cache.Batch(p).validate_one(K,cache.Input('body',p))\n return Reader().read()")
    assert _decision_violations({'store.entities': source})


def test_nonlocal_helper_reference_keeps_source_traversal():
    sources = {'store.entities': 'from . import helper\ndef entity_hash(p):\n read = str\n def bind():\n  nonlocal read\n  read = helper.read\n bind()\n return read(p)',
               'store.helper': 'from . import compiled as cache\ndef read(p): return cache.Batch(p).validate_one(HOT,cache.Input("body",p))'}
    assert _decision_violations(sources)



@pytest.mark.parametrize('sql', [
    'SELECT rank FROM search_index',
    'SELECT rank FROM search_index WHERE kind=? AND key_digest IN (?)',
    'SELECT payload FROM artifacts WHERE kind IN (?)',
    'SELECT payload FROM artifacts WHERE kind=? AND NOT key_digest IN (?)',
    'SELECT payload FROM artifacts WHERE kind=? AND key_digest IN (SELECT key_digest FROM artifacts WHERE kind=?)',
    "SELECT payload FROM artifacts WHERE note='kind=? AND key_digest IN (?)'",
    'SELECT payload FROM artifacts /* WHERE kind=? AND key_digest IN (?) */',
    'SELECT payload FROM artifacts WHERE kind=? AND key_digest IN (' + ','.join('?' for _ in range(129)) + ')',
])
def test_unknown_or_outside_live_set_ranking_selection_rejects(sql):
    assert _violations(f'conn.execute({sql!r})', 'store.compiled')


def test_fixed_128_live_key_selection_is_permitted():
    sql = 'SELECT payload FROM artifacts WHERE kind=? AND key_digest IN (' + ','.join('?' for _ in range(128)) + ')'
    assert not _violations(f'conn.execute({sql!r})', 'store.compiled')


@pytest.mark.parametrize('name', ['rank', 'rank_keys', 'lookup_many'])
@pytest.mark.parametrize('parameters', ['', 'keys=None', 'kind'])
def test_public_bulk_or_ranking_reader_requires_supplied_keys(name, parameters):
    assert _violations(f'def {name}({parameters}): pass', 'store.compiled')


def test_collection_api_requires_a_live_collection_argument():
    assert _violations('def collection(kind): pass', 'store.compiled')
    assert not _violations('def collection(kind, collection): pass', 'store.compiled')


@pytest.mark.parametrize('flag, blocked', [(True, True), (False, False)])
def test_decision_collection_requires_explicit_false_policy(flag, blocked):
    source = ('from . import compiled as cache\ndef entity_hash(p):\n'
              f" return cache.Batch(p).collection(cache.Kind('live',1,(),(),{flag}),cache.Collection('records',p))")
    assert bool(_decision_violations({'store.entities': source})) == blocked



@pytest.mark.parametrize('flag, blocked', [(True, True), (False, False)])
def test_named_collection_policy_is_checked_at_primitive(flag, blocked):
    source = ('from . import compiled as cache\n'
              f"K = cache.Kind('live',1,(),(),{flag})\n"
              "def entity_hash(p): return cache.Batch(p).collection(K,cache.Collection('records',p))")
    assert bool(_decision_violations({'store.entities': source})) == blocked



@pytest.mark.parametrize('flag, blocked', [(True, True), (False, False)])
def test_entrypoint_default_helper_enters_decision_traversal(flag, blocked):
    sources = {'store.entities': 'from . import helper\ndef entity_hash(p, read=helper.read): return read(p)',
               'store.helper': 'from . import compiled as cache\n'
                 f"def read(p): return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),{flag}),cache.Input('body',p))"}
    assert bool(_decision_violations(sources)) == blocked


def test_entrypoint_default_source_read_is_checked_in_enclosing_scope():
    source = ("from . import compiled as cache\n"
              "def entity_hash(p, saved=cache.Batch(ROOT).collection(HOT,cache.Collection('records',ROOT))): return saved.content_hash")
    assert _decision_violations({'store.entities': source})



@pytest.mark.parametrize('source', ['def materialized(): pass', 'def materialized(kind): pass',
                                   'def read_history(kind): return conn.execute("SELECT kind FROM materialized WHERE path IN (?)")'])
def test_materialized_read_requires_supplied_paths(source):
    assert _violations(source, 'store.compiled')


def test_materialized_selection_is_supplied_path_bounded():
    assert not _violations('conn.execute("SELECT kind FROM materialized WHERE path IN (?)")', 'store.compiled')
    assert _violations('conn.execute("SELECT kind FROM materialized")', 'store.compiled')


@pytest.mark.parametrize('invoke', ['cache.Batch(p).derive(K, [])', 'read(K, [])', 'forward(cache.Batch(p).derive)'])
def test_derive_is_guarded_at_decision_sites(invoke):
    source = ("from . import compiled as cache\nK = cache.Kind('live',1,(),(),True)\n"
              "read = cache.Batch(ROOT).derive\ndef entity_hash(p): return " + invoke)
    assert _decision_violations({'store.entities': source})


def test_proven_false_derive_remains_allowed():
    source = ("from . import compiled as cache\nK = cache.Kind('live',1,(),(),False)\n"
              "def entity_hash(p): return cache.Batch(p).derive(K, [])")
    assert not _decision_violations({'store.entities': source})



@pytest.mark.parametrize('flag, blocked', [(True, True), (False, False)])
def test_default_capture_retains_earlier_redefined_helper(flag, blocked):
    source = ("from . import compiled as cache\n"
              f"def read(p): return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),{flag}),cache.Input('body',p))\n"
              "def entity_hash(p, read=read): return read(p)\n"
              "def read(p): return None")
    assert bool(_decision_violations({'store.entities': source})) == blocked



@pytest.mark.parametrize('invoke', ['cache.Batch(p).materialized([p])', 'read([p])',
                                   'forward(cache.Batch(p).materialized)'])
def test_decision_hash_cannot_use_historical_materialized_bytes(invoke):
    source = ('from . import compiled as cache\nread = cache.Batch(ROOT).materialized\n'
              'def entity_hash(p): return ' + invoke)
    assert _decision_violations({'store.entities': source})


@pytest.mark.parametrize('flag, blocked', [(True, True), (False, False)])
@pytest.mark.parametrize('wrapper', ['if True:', 'try:', 'for _ in (1,):'])
def test_captured_module_control_flow_helper_is_checked(flag, blocked, wrapper):
    source = ("from . import compiled as cache\n" + wrapper + "\n"
              f"    def read(p): return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),{flag}),cache.Input('body',p))\n"
              + ('except OSError: pass\n' if wrapper == 'try:' else '')
              + "def entity_hash(p, read=read): return read(p)\n"
              + "def read(p): return None")
    assert bool(_decision_violations({'store.entities': source})) == blocked


@pytest.mark.parametrize('table', ['artifacts', 'sources', 'materialized'])
def test_private_lru_selection_requires_exact_bound_and_reason(table):
    sql = ("SELECT rowid,CASE WHEN typeof(last_used)='integer' THEN last_used ELSE -1 END AS used "
           f'FROM {table} ORDER BY used,rowid LIMIT 128')
    safe = "def _lru_victims(conn):\n # compiled-maintenance-sql: bounded private LRU metadata, never membership\n return conn.execute(" + repr(sql) + ")"
    assert not _violations(safe, 'store.compiled')
    assert _violations(safe.replace('LIMIT 128', 'LIMIT 129'), 'store.compiled')
    assert _violations(safe.replace(' # compiled-maintenance-sql: bounded private LRU metadata, never membership\n', ''), 'store.compiled')
    assert _violations(safe.replace('_lru_victims', 'inventory'), 'store.compiled')


@pytest.mark.parametrize('flag, blocked', [(True, True), (False, False)])
def test_imported_class_method_wrapper_is_checked(flag, blocked):
    sources = {'store.entities': 'from .reader import Reader\ndef entity_hash(p): return Reader.digest(p)',
               'store.reader': 'from . import compiled as cache\nclass Reader:\n @staticmethod\n'
               f" def digest(p): return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),{flag}),cache.Input('body',p))"}
    assert bool(_decision_violations(sources)) == blocked


@pytest.mark.parametrize('helper', ['_owned_tables', '_purge_connection', '_purge_table_page', '_purge_cursor'])
def test_private_schema_purge_sql_requires_verified_shape(helper):
    if helper == '_owned_tables':
        statement = 'conn.execute("SELECT rowid,name FROM sqlite_master WHERE type=\'table\' AND (? IS NULL OR rowid>?) ORDER BY rowid LIMIT 1")'
    elif helper == '_purge_cursor':
        statement = 'conn.execute("SELECT CASE WHEN typeof(value)=\'text\' AND length(CAST(value AS BLOB))<=256 THEN value END FROM cache_meta WHERE name=?")'
    else:
        statement = "conn.execute(f'DROP TABLE {_quote_table(name)}')"
    source = f'def {helper}(conn):\n # compiled-maintenance-sql: bounded verified owned schema names\n {statement}'
    assert not _violations(source, 'store.compiled')
    assert _violations(source.replace(helper, 'inventory'), 'store.compiled')
    assert _violations(source.replace(' # compiled-maintenance-sql: bounded verified owned schema names\n', ''), 'store.compiled')
    if helper in {'_purge_connection', '_purge_table_page'}:
        assert _violations(source.replace('_quote_table(name)', 'name'), 'store.compiled')
    elif helper == '_owned_tables':
        assert _violations(source.replace('LIMIT 1', 'LIMIT 2'), 'store.compiled')
        assert _violations(source.replace('(? IS NULL OR rowid>?)', '1=1'), 'store.compiled')
    else:
        assert _violations(source.replace('<=256', '<=257'), 'store.compiled')


@pytest.mark.parametrize('flag, blocked', [(True, True), (False, False)])
@pytest.mark.parametrize('alias', ['read', 'helper.read'])
def test_imported_class_namespace_callable_alias_is_checked(flag, blocked, alias):
    sources = {'store.entities': 'from .reader import Reader\ndef entity_hash(p): return Reader.digest(p)',
               'store.reader': 'from . import helper\nfrom .helper import read\nclass Reader:\n digest = ' + alias,
               'store.helper': 'from . import compiled as cache\n'
               f"def read(p): return cache.Batch(p).validate_one(cache.Kind('live',1,(),(),{flag}),cache.Input('body',p))"}
    assert bool(_decision_violations(sources)) == blocked


def test_owned_trigger_purge_sql_is_bounded_and_quoted():
    source = ("def _purge_trigger_page(conn):\n"
              " # compiled-maintenance-sql: bounded verified owned trigger names\n"
              " conn.execute(\"SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY rowid LIMIT 129\")\n"
              " conn.execute(f'DROP TRIGGER {_quote_table(name)}')")
    assert not _violations(source, 'store.compiled')
    for old, new in [('_purge_trigger_page', 'inventory'), ('LIMIT 129', 'LIMIT 130'),
                     ("type='trigger'", "type='table'"), ('_quote_table(name)', 'name')]:
        assert _violations(source.replace(old, new), 'store.compiled')


@pytest.mark.parametrize('flag, blocked', [(True, True), (False, False)])
@pytest.mark.parametrize('expression', ['Reader(p).digest()', 'helper.Reader(p).digest()'])
def test_decision_wrapper_traverses_imported_initialization(flag, blocked, expression):
    sources = {
        'store.entities': ('from .reader import Reader\nfrom . import reader as helper\n'
                           f'def entity_hash(p): return {expression}'),
        'store.reader': ('from . import compiled as cache\nclass Reader:\n'
                         ' def __init__(self,p):\n'
                         f"  self.value=cache.Batch(p).validate_one(cache.Kind('live',1,(),(),{flag}),cache.Input('body',p))\n"
                         ' def digest(self): return self.value')}
    assert bool(_decision_violations(sources)) == blocked


def test_virtual_purge_select_and_drop_have_only_the_owned_bounded_shape():
    source = ("def _purge_virtual_page(conn):\n"
              " # compiled-maintenance-sql: bounded verified virtual-table names\n"
              " conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND lower(name)<>'cache_meta' AND (rootpage=0 OR rootpage IS NULL) ORDER BY rowid LIMIT 1\")\n"
              " conn.execute(f'DROP TABLE {_quote_table(name)}')")
    assert not _violations(source, 'store.compiled')
    for old, new in [('_purge_virtual_page', 'inventory'), ('LIMIT 1', 'LIMIT 2'),
                     ("type='table'", "type='trigger'"), ('_quote_table(name)', 'name'),
                     ('(rootpage=0 OR rootpage IS NULL)', '1=1')]:
        assert _violations(source.replace(old, new), 'store.compiled')


@pytest.mark.parametrize('flag, blocked', [(True, True), (False, False)])
@pytest.mark.parametrize('method', ['__init__', '__new__'])
@pytest.mark.parametrize('binding', ['inherited', 'alias'])
def test_decision_constructor_resolves_declared_bases_and_aliases(flag, blocked, method, binding):
    validation = f"cache.Batch(p).validate_one(cache.Kind('live',1,(),(),{flag}),cache.Input('body',p))"
    body = (f'  self.value={validation}\n' if method == '__init__' else
            f'  self=object.__new__(cls)\n  self.value={validation}\n  return self\n')
    argument = 'self' if method == '__init__' else 'cls'
    if binding == 'inherited':
        reader = (f'class Base:\n def {method}({argument},p):\n{body}'
                  'class Reader(Base):\n def digest(self): return self.value\n')
    else:
        reader = (f'def initialize({argument},p):\n' + body.replace('  ', ' ') +
                  f'class Reader:\n {method}=initialize\n def digest(self): return self.value\n')
    sources = {'store.entities': 'from .reader import Reader\ndef entity_hash(p): return Reader(p).digest()',
               'store.reader': 'from . import compiled as cache\n' + reader}
    assert bool(_decision_violations(sources)) == blocked


@pytest.mark.parametrize('table', ['artifacts', 'sources', 'materialized'])
@pytest.mark.parametrize('operation', ['UPDATE {table} SET last_used=last_used', 'DELETE FROM {table}'])
def test_row_producing_returning_cannot_bypass_live_selection(table, operation):
    sql = operation.format(table=table) + ' RETURNING *'
    source = 'def lookup(key):\n return conn.execute(' + repr(sql) + ').fetchall()'
    assert _violations(source, 'store.compiled')


def test_returning_in_a_literal_or_comment_is_not_a_row_reader():
    for sql in ("UPDATE artifacts SET payload='RETURNING payload' WHERE kind=? AND key_digest=?",
                'UPDATE artifacts SET last_used=last_used -- RETURNING payload\n'):
        assert not _violations('def write():\n conn.execute(' + repr(sql) + ')', 'store.compiled')


def test_imported_class_alias_mutated_by_imported_module_is_not_proven_safe():
    sources = {
        'store.entities': ('from .reader import Reader\nfrom . import setter\n'
                           'def entity_hash(p): return Reader.digest(p)'),
        'store.reader': 'from .helper import read\nclass Reader:\n digest = read',
        'store.setter': 'from .reader import Reader\nfrom .helper import unsafe\nReader.digest = unsafe',
        'store.helper': ('from . import compiled as cache\n'
                         "def read(p): return cache.Batch(p.parent).validate_one(cache.Kind('live',1,(),(),False),cache.Input('body',p))\n"
                         "def unsafe(p): return cache.Batch(p.parent).validate_one(cache.Kind('live',1,(),(),True),cache.Input('body',p))")}
    assert _decision_violations(sources)
