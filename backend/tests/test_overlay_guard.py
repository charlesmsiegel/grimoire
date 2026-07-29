"""Campaign reads of inheritable records go through store.overlay (#248).

A campaign materializes a record only when it diverges from its world, so an
inheritable record usually has no file under the campaign root at all. Reading
one straight off `campaigns.campaign_root(cid)` therefore misses everything the
campaign still inherits, and misses the `deleted.json` tombstones that hide a
world record the user deleted campaign-side. It fails *silently* — a missing
character is a skipped cast entry, not an exception — and per the project's own
history it is this codebase's most repeated bug class.

`store/overlay.py` exists to prevent exactly that, and its rules are clearly
written down. The problem the guard solves is that the rule is invisible at the
call site: `campaign_root(cid)` is an ordinary-looking function, used correctly
by every module that owns campaign-local state (proposals, sheets, playing,
chronicle, playstate, dossiers). A reviewer cannot tell a correct use from an
incorrect one without knowing whether the record in question is inheritable.

So the guard flags one specific combination: **a campaign root meeting an
inheritable kind.** In the resolver-call form it additionally requires a read,
because a pure write is *supposed* to land campaign-side — that is what
materialization is. In the raw-path form (``croot / "characters"``) it does not
try to tell reads from writes: a bare path expression has no verb to inspect,
and building an inheritable path by hand is worth a second look either way.

Honest about its reach, like `test_atomic_guard.py`:

- It follows names only inside the function that binds them, through
  assignment, annotation, walrus, matched tuple unpacking and aliasing (to a
  fixed point, so chains of any length hold). Passing a campaign root into a
  helper that takes a bare ``root``/``path`` parameter escapes it
  (`context._char_name` is such a helper), as does returning one. Widening that
  means real type analysis, which is not worth it here.
- Name tracking is **flow-insensitive**: a name that ever holds a campaign root
  holds one for the whole function, so ``x = croot`` followed by
  ``x = world_root(...)`` keeps flagging reads through ``x``. That direction is
  a false positive — loud, and clearable by not reusing the name.
- `PURE_WRITERS` is an exact list, not a set of prefixes. Prefixes were wrong:
  `create_`, `update_`, `remove_` and `promote_` all cover functions that
  resolve something through the root before writing it. Anything not on the
  list is flagged, so a novel writer fails the test rather than slipping past —
  matching the atomic guard's bias: a false positive is a loud failure a human
  clears with a marker; a false negative is the bug.
- Raw path building is recognized as ``/`` and ``joinpath`` with a literal
  segment. ``os.path.join``, ``Path(croot, ...)``, and a non-literal segment
  (``croot / kind``) are invisible.
- `appearances.locked_actor_root` is a sanctioned way to read an actor
  campaign-side, and the guard cannot check that the actor really is in the
  appearance record. What it buys is that the call site now *states* the claim
  a reviewer has to check, instead of the claim being invisible.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire
from grimoire.store import overlay

from . import guard_markers

PACKAGE = pathlib.Path(grimoire.__file__).parent

#: Calls that hand back a raw campaign directory, inheritance and all.
ROOT_FUNCS = ("campaign_root", "croot_of", "_campaign_root_or_404")

#: Store modules that take a `root` and resolve a record under it. They know
#: nothing about world inheritance by design -- that is the overlay's job -- so
#: handing one a campaign root is the mistake this guard looks for.
#:
#: `appearances` is here as well as in OWNERS, and both are correct: inside its
#: own module a raw campaign root is the point (it owns the lock), but its
#: root-taking actor readers -- `actor_hash`, `_actor_name` -- are reachable
#: from elsewhere, and `checks.py` was calling one with a bare `campaign_root`.
RESOLVER_MODULES = ("entities", "characters", "pcs", "greetings", "assets", "taglines",
                    "appearances")

#: Resolver functions that *only* write, named exactly. A pure write belongs on
#: the campaign root: that is how a record materializes.
#:
#: This was a list of name prefixes (`write`, `create_`, `set_`, ...) and that
#: was wrong, because the prefix does not tell you whether the function reads
#: first. `greetings.create_greeting` resolves `char_name(root, ...)` before
#: writing (`backend/src/grimoire/store/greetings.py:117`) -- which is issue
#: #137, a bug of exactly this class that already happened, and the reason
#: `overlay.create_greeting` bakes the name itself. `entities.update_entity`,
#: `greetings.remove_from_plotmap` and `assets.promote_image` read too. A
#: prefix list would have re-hidden all of them.
#:
#: An exact list stays honest and stays short: dropping the exemption entirely
#: flags only four sites in the whole package, two of which are these.
PURE_WRITERS = frozenset({
    "assets.put_image",    # writes bytes, then deletes only stale same-name siblings
    "assets.write_focus",  # writes focus.json, reads nothing
})

#: Modules allowed to combine the two, because each owns the invariant that
#: makes a campaign-side read correct. The reason is the point: a new entry
#: here needs one.
OWNERS = {
    "store/overlay.py": "is the resolver -- raw campaign reads are its whole job",
    "store/campaigns.py": "ensure_campaign_slim rewrites the raw pre-overlay tree",
    "store/appearances.py": "owns the version lock, which materializes the actor",
    "store/sync.py": "three-way merge needs the campaign's own copy, unresolved",
    "store/migrations.py": "one-time migrations run on the raw store layout",
}

MARKER = "overlay-ok:"

#: Path segments that resolve through to the world, from the overlay itself.
INHERITED_SEGMENTS = frozenset(overlay.INHERITED_KINDS + overlay.INHERITED_FILES)


def _last_name(node: ast.AST) -> str | None:
    """The trailing name of a dotted expression: `store.campaigns.campaign_root`
    -> "campaign_root", so package-qualified calls count the same as bare ones."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _last_name(node.func)
    return None


def _is_root_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _last_name(node.func) in ROOT_FUNCS


def _own_nodes(body: list):
    """Statements belonging to this scope, not descending into nested defs -- a
    closure has its own bindings, and inheriting the enclosing function's would
    misattribute them. Every def is reached separately, so nothing is missed."""
    stack = list(body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue   # its statements belong to its own scope, reached separately
        stack.extend(ast.iter_child_nodes(node))


def _bound_here(node: ast.AST, known: set[str]) -> set[str]:
    """Names this statement binds to a campaign root.

    Covers the forms a real call site uses: plain assignment, annotated
    assignment (`croot: Path = campaign_root(cid)`), the walrus, tuple
    unpacking where every value is a root, and a plain alias of a name already
    known to hold one. Anything cleverer than that escapes -- see the module
    docstring; this is a tripwire, not a type system."""
    def is_rootish(value: ast.AST) -> bool:
        return _is_root_call(value) or (isinstance(value, ast.Name) and value.id in known)

    def targets_of(target: ast.AST, value: ast.AST) -> set[str]:
        if isinstance(target, ast.Name) and is_rootish(value):
            return {target.id}
        # `a, b = campaign_root(x), campaign_root(y)` -- element-wise
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) \
                and len(target.elts) == len(value.elts):
            out: set[str] = set()
            for t, v in zip(target.elts, value.elts):
                out |= targets_of(t, v)
            return out
        return set()

    if isinstance(node, ast.Assign):
        out = set()
        for t in node.targets:
            out |= targets_of(t, node.value)
        return out
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return targets_of(node.target, node.value)
    if isinstance(node, ast.NamedExpr):
        return targets_of(node.target, node.value)
    return set()


def _scopes(tree: ast.AST):
    """(nodes, campaign-root-valued names) for the module and for each function.

    Every scope is yielded, including ones that bind no name: a campaign root
    is often used inline (`campaign_root(cid) / "climate.json"`), which is the
    exact shape of the read that motivated this guard.

    A name is campaign-root-valued if the scope binds it from a ROOT_FUNCS
    call, or if it is a parameter named `croot` -- the convention this codebase
    already uses for "a campaign root passed in from somewhere else"."""
    for scope in [tree] + [n for n in ast.walk(tree)
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        names = set()
        if not isinstance(scope, ast.Module):
            args = scope.args
            names = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs
                     if a.arg == "croot"}
        nodes = list(_own_nodes(scope.body))
        # To a fixed point, not a fixed number of passes: `_own_nodes` yields in
        # no useful order, so an alias chain (`a = croot; b = a; c = b`) needs as
        # many passes as it has hops. Two passes left the third hop untracked.
        while True:
            grown = set()
            for node in nodes:
                grown |= _bound_here(node, names)
            if grown <= names:
                break
            names |= grown
        yield nodes, names


def _resolver_imports(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """(module alias -> resolver module, resolver functions imported directly).

    `from . import characters as chars` and `from .characters import read_card`
    both reach the same resolver, and matching on the attribute's trailing name
    alone saw neither. The codebase does not currently use either form, which is
    exactly why the guard has to: the first one written should fail, not pass.
    """
    aliases: dict[str, str] = {}
    direct: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            tail = (node.module or "").rsplit(".", 1)[-1]
            for a in node.names:
                if a.name in RESOLVER_MODULES:          # from . import characters [as chars]
                    aliases[a.asname or a.name] = a.name
                elif tail in RESOLVER_MODULES:          # from .characters import read_card
                    direct.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                mod = a.name.rsplit(".", 1)[-1]
                if mod in RESOLVER_MODULES:
                    aliases[a.asname or mod] = mod
    return aliases, direct


def _unresolved_reads(tree: ast.AST):
    """(node, description) for every campaign-root read of an inheritable record."""
    aliases, direct = _resolver_imports(tree)

    def resolver_of(func: ast.AST) -> tuple[str, str] | None:
        """(module, function) if this call reaches a resolver, else None."""
        if isinstance(func, ast.Attribute):
            recv = _last_name(func.value)
            mod = aliases.get(recv, recv if recv in RESOLVER_MODULES else None)
            return (mod, func.attr) if mod else None
        if isinstance(func, ast.Name) and func.id in direct:
            return ("<imported>", func.id)
        return None

    for nodes, names in _scopes(tree):
        def is_croot(n: ast.AST) -> bool:
            return _is_root_call(n) or (isinstance(n, ast.Name) and n.id in names)

        for node in nodes:
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                    and is_croot(node.left) and isinstance(node.right, ast.Constant)
                    and node.right.value in INHERITED_SEGMENTS):
                yield node, f'<campaign root> / "{node.right.value}"'
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "joinpath" and is_croot(node.func.value)
                    and any(isinstance(a, ast.Constant) and a.value in INHERITED_SEGMENTS
                            for a in node.args)):
                # the same join, spelled the other way
                seg = next(a.value for a in node.args
                           if isinstance(a, ast.Constant) and a.value in INHERITED_SEGMENTS)
                yield node, f'<campaign root>.joinpath("{seg}", ...)'
            elif isinstance(node, ast.Call) and (hit := resolver_of(node.func)):
                mod, fn = hit
                # `root=` as a keyword is the same call; these resolvers all name
                # their first parameter `root`.
                root_arg = node.args[0] if node.args else next(
                    (k.value for k in node.keywords if k.arg == "root"), None)
                if (root_arg is not None and is_croot(root_arg)
                        and f"{mod}.{fn}" not in PURE_WRITERS):
                    yield node, f"{mod}.{fn}(<campaign root>, ...)"


def _scan():
    """(relative path, node, description, marker reason) over the whole package."""
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel in OWNERS:
            continue
        src = path.read_text(encoding="utf-8")
        for node, what in _unresolved_reads(ast.parse(src)):
            yield rel, node, what, guard_markers.marker_reason(MARKER, src, node)


def test_every_campaign_read_of_an_inheritable_record_goes_through_the_overlay():
    offenders = [f"{rel}:{node.lineno}: {what}"
                 for rel, node, what, reason in _scan() if reason is None]
    assert not offenders, (
        "campaign-root read(s) of a record the campaign inherits from its world "
        "— route them through store.overlay (or store.appearances."
        "locked_actor_root, if the actor is in the appearance record), or "
        "annotate the line with `# overlay-ok: <why this one is safe>`:\n  "
        + "\n  ".join(offenders))


def test_the_marker_is_not_a_rubber_stamp():
    """Exceptions must stay few and must say why. A growing pile of them means
    the rule is being routed around rather than applied."""
    marked = [(f"{rel}:{node.lineno}", reason)
              for rel, node, _what, reason in _scan() if reason is not None]

    unexplained = [loc for loc, reason in marked if len(reason) < 15]
    assert not unexplained, f"`overlay-ok` with no real reason: {unexplained}"
    assert len(marked) <= 4, (
        f"{len(marked)} overlay-ok exemptions; each one is a record that can "
        f"silently miss world inheritance, so they need review rather than a "
        f"raised limit: {marked}")


def test_a_marker_inside_a_string_literal_does_not_exempt():
    """The marker scan used to match raw line text, so a string that merely
    quoted the marker silenced the guard for that line — a way to disable an
    architecture test by accident, or quietly on purpose."""
    src = ('raise ValueError("pass overlay-ok: to skip")\n')
    node = ast.parse(src).body[0]
    assert guard_markers.marker_reason(MARKER, src, node) is None

    # A line inside a triple-quoted string tokenizes as a comment when read on
    # its own, so per-line tokenizing let a docstring hand out exemptions.
    src = ('x = read(croot, """\n'
           '# overlay-ok: a fake reason living inside a string\n'
           '""")\n')
    node = ast.parse(src).body[0]
    assert guard_markers.marker_reason(MARKER, src, node) is None

    src = 'x = read(croot)  # overlay-ok: a real reason living in a real comment\n'
    node = ast.parse(src).body[0]
    assert guard_markers.marker_reason(MARKER, src, node) == \
        "a real reason living in a real comment"


def test_every_owner_module_exists_and_says_why():
    """An allowlist entry for a module that moved would silently stop applying."""
    for rel, reason in OWNERS.items():
        assert (PACKAGE / rel).exists(), f"OWNERS names a module that is gone: {rel}"
        assert len(reason) >= 20, f"OWNERS entry for {rel} needs a real reason"


def test_the_inheritable_set_comes_from_the_overlay():
    """The guard must not carry its own copy of the rule: a kind added to the
    overlay has to start being enforced without editing this file."""
    assert "locations" in INHERITED_SEGMENTS and "characters" in INHERITED_SEGMENTS
    assert "plotmap.json" in INHERITED_SEGMENTS
    # campaign-local records are not the overlay's business and must not be flagged
    for local in ("scenes", "sheets", "proposals", "climate.json", "appearances.json"):
        assert local not in INHERITED_SEGMENTS


def test_the_guard_actually_detects_an_unresolved_read():
    """A guard that cannot fail is worse than none — it reads as coverage."""
    src = ("def f(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    return characters.read_card(croot, aid, vid)\n")
    assert [w for _n, w in _unresolved_reads(ast.parse(src))] == \
        ["characters.read_card(<campaign root>, ...)"]

    src = ("def f(cid):\n"
           "    croot = store.campaigns.campaign_root(cid)\n"
           "    return (croot / 'locations' / f'{eid}.md').read_text()\n")
    assert [w for _n, w in _unresolved_reads(ast.parse(src))] == \
        ['<campaign root> / "locations"']

    # the shape of the bug that motivated #248's sibling, #237
    src = ("def f(cid):\n"
           "    return (store.campaigns.campaign_root(cid) / 'greetings').exists()\n")
    assert list(_unresolved_reads(ast.parse(src)))


def test_a_resolver_reached_through_another_module_is_still_caught():
    """`checks.py` read an actor by calling `appearances._actor_name` with a raw
    `campaign_root` — correct (it is gated on a locked version) but invisible,
    and missed until the blast-radius pass surfaced it."""
    src = ("def f(cid):\n"
           "    return appearances._actor_name(campaigns.campaign_root(cid), kind, eid, vid)\n")
    assert [w for _n, w in _unresolved_reads(ast.parse(src))] == \
        ["appearances._actor_name(<campaign root>, ...)"]


def test_the_overlay_and_the_sanctioned_accessor_are_not_flagged():
    for src in [
        "def f(cid):\n    return overlay.read_entity(cid, 'lore', eid)\n",
        "def f(cid):\n    aroot = appearances.locked_actor_root(cid)\n"
        "    return characters.read_card(aroot, aid, vid)\n",
        "def f(cid):\n    root = overlay.char_root(cid, aid)\n"
        "    return characters.read_character(root, aid)\n",
    ]:
        assert not list(_unresolved_reads(ast.parse(src))), f"false positive: {src!r}"


def test_pure_writes_and_campaign_local_records_are_not_flagged():
    """A pure write is how a record materializes, and campaign-local records
    have nothing to inherit — flagging either would make the guard noise."""
    for src, why in [
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    assets.put_image(croot, aid, vid, name, data, ext)\n", "a pure write"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    assets.write_focus(croot, aid, vid, focus)\n", "a pure write"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return (croot / 'climate.json').read_text()\n", "campaign-local file"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return playstate.read_state(croot, aid)\n", "campaign-local record"),
    ]:
        assert not list(_unresolved_reads(ast.parse(src))), f"false positive: {why}"


def test_read_modify_write_resolvers_are_not_exempt():
    """The exemption was a list of name prefixes, and a prefix does not tell you
    whether the function reads first. Every name below writes *and* resolves
    something through the root it is handed — `create_greeting` most sharply, in
    that `overlay.create_greeting` exists to work around exactly that read
    (#137). Each of these used to be exempt."""
    for mod, fn in [("assets", "promote_image"), ("entities", "update_entity"),
                    ("greetings", "create_greeting"), ("greetings", "remove_from_plotmap"),
                    ("characters", "clear_chub_source")]:
        src = (f"def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
               f"    {mod}.{fn}(croot, aid, vid)\n")
        assert list(_unresolved_reads(ast.parse(src))), f"{mod}.{fn} was exempted as a write"


def test_a_resolver_reached_through_an_import_alias_is_still_caught():
    """Matching the attribute's trailing name saw neither aliased modules nor
    directly-imported functions. Nothing in the package spells it either way
    today, which is the point: the first one written should fail, not pass."""
    for src, why in [
        ("from . import characters as chars\n"
         "def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return chars.read_card(croot, aid, vid)\n", "aliased module"),
        ("from .characters import read_card\n"
         "def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return read_card(croot, aid, vid)\n", "directly imported function"),
    ]:
        assert list(_unresolved_reads(ast.parse(src))), f"missed: {why}"


def test_an_alias_chain_is_followed_to_its_end():
    """Two fixed passes tracked two hops; `_own_nodes` yields in no useful
    order, so the number of passes needed is the length of the chain."""
    src = ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
           "    a = croot\n    b = a\n    c = b\n"
           "    return characters.read_card(c, aid, vid)\n")
    assert list(_unresolved_reads(ast.parse(src))), "a four-hop alias chain escaped"


def test_the_other_ways_to_spell_a_campaign_root():
    """Only a plain `x = campaign_root(cid)` was tracked; these all reach the
    same place and were invisible."""
    for src, why in [
        ("def f(cid):\n    croot: Path = campaigns.campaign_root(cid)\n"
         "    return characters.read_card(croot, aid, vid)\n", "annotated assignment"),
        ("def f(cid):\n    if (croot := campaigns.campaign_root(cid)):\n"
         "        return characters.read_card(croot, aid, vid)\n", "walrus"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n    alias = croot\n"
         "    return characters.read_card(alias, aid, vid)\n", "plain alias"),
        ("def f(cid):\n    a, b = campaigns.campaign_root(cid), campaigns.campaign_root(o)\n"
         "    return characters.read_card(b, aid, vid)\n", "tuple unpacking"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return characters.read_card(root=croot, cid=aid, vid=vid)\n", "root= keyword"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return croot.joinpath('characters', aid, 'character.md').read_text()\n",
         "joinpath instead of /"),
    ]:
        assert list(_unresolved_reads(ast.parse(src))), f"missed: {why}"


def test_a_world_root_in_the_same_module_is_not_mistaken_for_a_campaign_one():
    """routes.py shares generic handlers between worlds and campaigns, both
    binding a local called `root`. Tracking names module-wide (rather than per
    function) made every world read look like a campaign read."""
    src = ("def campaign(cid):\n"
           "    root = _campaign_root_or_404(cid)\n"
           "    return root\n"
           "def world(wid):\n"
           "    root = _world_root_or_404(wid)\n"
           "    return characters.read_card(root, aid, vid)\n")
    assert not list(_unresolved_reads(ast.parse(src)))


def test_a_nested_function_does_not_inherit_the_outer_binding():
    """`taken` closures inside overlay.create_* take an id, not a root; letting
    a nested def see the outer `croot` would attribute reads to the wrong scope."""
    src = ("def outer(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    def inner(croot_unused):\n"
           "        return characters.read_card(other_root, aid, vid)\n"
           "    return inner\n")
    assert not list(_unresolved_reads(ast.parse(src)))
