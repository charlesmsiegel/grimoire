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

- It follows names through assignment, annotation, walrus, matched tuple
  unpacking and aliasing (to a fixed point, so chains of any length hold), and
  into nested functions that close over them (a parameter of the same name
  shadows, as Python does). Passing a campaign root into a *separate* helper
  that takes a bare ``root``/``path`` parameter escapes it
  (`context._char_name` is such a helper), as does returning one. Widening that
  means real type analysis, which is not worth it here.
- Resolver calls are matched through import aliases, including a directly
  imported function (which keeps its canonical name, so a renamed import of a
  sanctioned writer is still sanctioned), and the root may be positional or
  passed under any of `ROOT_KEYWORDS`.
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
#:
#: `image_subjects` reads greeting assets and character refs under `<root>/
#: greetings` (`backend/src/grimoire/store/image_subjects.py:21`), so on a thin
#: campaign it omits inherited world data exactly like the others.
#:
#: `lorebook.commit` is the same shape one level up: it reads existing entities
#: through `_existing_signatures` to skip duplicates
#: (`backend/src/grimoire/store/lorebook.py:74`), so on a thin campaign it would
#: miss inherited entries and create a campaign-side record shadowing them.
#: Every call site today passes a world root; the entry is preventive.
RESOLVER_MODULES = ("entities", "characters", "pcs", "greetings", "assets", "taglines",
                    "appearances", "image_subjects", "lorebook")

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

#: Code allowed to combine the two, because it owns the invariant that makes a
#: campaign-side read correct -- keyed by file, then by the *function* that owns
#: it. The reason is the point: a new entry here needs one.
#:
#: Scoped to functions rather than whole files on purpose. A file-wide exemption
#: left the store's largest modules permanently unguarded: `campaigns.py` was
#: exempt because of `ensure_campaign_slim`, so any unrelated helper added
#: anywhere else in it inherited the pass. Only `overlay.py` is genuinely
#: file-wide, and it says why.
OWNERS: dict[str, dict[str, str]] = {
    "store/overlay.py": {
        "*": "is the resolver -- raw campaign reads are its whole job",
    },
    "store/campaigns/lifecycle.py": {
        "ensure_campaign_slim": "rewrites the raw pre-overlay tree, by definition",
    },
    "store/appearances/versions.py": {
        "_set_default": "runs inside _lock, after the copy that materializes the actor",
    },
    "store/sync.py": {
        "incoming": "three-way merge needs the campaign's own copy, unresolved",
        "_advance": "advances a base against the copy the campaign actually holds",
        "_actor_incoming": "compares the campaign's actor bytes with the world's",
        "_plotmap_incoming": "same, for the plot map",
    },
    "store/migrations.py": {
        "_bake_campaign": "a one-time migration, run on the raw store layout",
        "_repair_character_baselines": "repairs bases against the campaign's own files",
        "_repair_greeting_baseline": "same, for a greeting",
    },
}

MARKER = "overlay-ok:"

#: Path segments that resolve through to the world, from the overlay itself.
INHERITED_SEGMENTS = frozenset(overlay.INHERITED_KINDS + overlay.INHERITED_FILES)


def _inheritable_literal(value) -> str | None:
    """The inheritable segment a path literal starts with, if any.

    `pathlib` treats `croot / "characters/alice/character.md"` exactly like the
    segments joined one at a time, so comparing the whole literal missed it --
    an ordinary path-formatting choice would have made a raw campaign read
    invisible. Backslashes count too; a Windows-style literal joins the same."""
    if not isinstance(value, str):
        return None
    head = value.replace("\\", "/").split("/", 1)[0]
    return head if head in INHERITED_SEGMENTS else None


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


def _root_aliases(tree: ast.AST) -> set[str]:
    """Local names bound to a campaign-root factory by an import.

    `from .campaigns import campaign_root as campaign_dir` makes `campaign_dir`
    a root factory that `_last_name` cannot see, so the alias has to be
    recorded the same way resolver imports are."""
    out = set(ROOT_FUNCS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name in ROOT_FUNCS:
                    out.add(a.asname or a.name)
    return out


def _is_root_call(node: ast.AST, aliases: frozenset[str] = frozenset(ROOT_FUNCS)) -> bool:
    return isinstance(node, ast.Call) and _last_name(node.func) in aliases


#: Anything that introduces its own parameter bindings. A lambda is one, which
#: is easy to forget because it is an expression: converting a nested `def` to
#: a lambda used to turn a correctly-shadowed parameter into a false positive.
SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _own_nodes(body: list):
    """Statements belonging to this scope, not descending into nested scopes --
    each is walked separately so it can be given its own name set (which starts
    from its enclosing scope's; see `_scopes`)."""
    stack = list(body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, SCOPE_NODES):
            # The body belongs to its own scope, reached separately -- but the
            # decorators and the argument defaults are evaluated *here*, when
            # the `def` executes, so they are this scope's code. Skipping the
            # whole node dropped `def f(card=characters.read_card(croot, ...))`.
            stack.extend(getattr(node, "decorator_list", []))
            a = node.args
            stack.extend(d for d in a.defaults + a.kw_defaults if d is not None)
            continue
        stack.extend(ast.iter_child_nodes(node))


def _bound_here(node: ast.AST, known: set[str], aliases: frozenset[str]) -> set[str]:
    """Names this statement binds to a campaign root.

    Covers the forms a real call site uses: plain assignment, annotated
    assignment (`croot: Path = campaign_root(cid)`), the walrus, tuple
    unpacking where every value is a root, and a plain alias of a name already
    known to hold one. Anything cleverer than that escapes -- see the module
    docstring; this is a tripwire, not a type system."""
    def is_rootish(value: ast.AST) -> bool:
        return _is_root_call(value, aliases) or (isinstance(value, ast.Name) and value.id in known)

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


def _scopes(tree: ast.AST, aliases: frozenset[str] = frozenset(ROOT_FUNCS)):
    """(nodes, campaign-root-valued names) for the module and for each function.

    Every scope is yielded, including ones that bind no name: a campaign root
    is often used inline (`campaign_root(cid) / "climate.json"`), which is the
    exact shape of the read that motivated this guard.

    A name is campaign-root-valued if the scope binds it from a ROOT_FUNCS
    call, if it is a parameter named `croot` -- the convention this codebase
    already uses for "a campaign root passed in from somewhere else" -- or if
    an *enclosing* scope bound it and this one does not shadow it. A closure
    over `croot` is an ordinary refactor, and treating a nested `def` as a
    clean slate let it walk straight past the guard.
    """
    def walk(scope, inherited: set[str]):
        names = set(inherited)
        if not isinstance(scope, ast.Module):
            args = scope.args
            params = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
            names -= params                       # a parameter shadows the closure
            names |= {a for a in params if a == "croot"}
        # a lambda's "body" is a single expression, not a list of statements
        body = scope.body if isinstance(scope.body, list) else [scope.body]
        nodes = list(_own_nodes(body))
        # To a fixed point, not a fixed number of passes: `_own_nodes` yields in
        # no useful order, so an alias chain (`a = croot; b = a; c = b`) needs as
        # many passes as it has hops. Two passes left the third hop untracked.
        while True:
            grown = set()
            for node in nodes:
                grown |= _bound_here(node, names, aliases)
            if grown <= names:
                break
            names |= grown
        yield nodes, names
        for child in nodes:
            if isinstance(child, SCOPE_NODES):
                yield from walk(child, names)

    yield from walk(tree, set())


def _resolver_imports(tree: ast.AST) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """(module alias -> resolver module, local name -> canonical (module, func)).

    `from . import characters as chars` and `from .characters import read_card`
    both reach the same resolver, and matching on the attribute's trailing name
    alone saw neither. The codebase does not currently use either form, which is
    exactly why the guard has to: the first one written should fail, not pass.

    Direct imports keep their *canonical* pair, not the local name, so
    `from .assets import put_image as save` still matches `assets.put_image` in
    PURE_WRITERS -- otherwise renaming an import turns a sanctioned write into a
    permanent false positive.

    A resolver package split into a subpackage (`appearances/cast.py`, ...)
    adds a third form: `from .appearances import cast as appearances_cast`
    binds a *submodule*, not a function, so `appearances_cast._actor_name(...)`
    reaches the resolver the same way `appearances._actor_name(...)` always
    did. Treating `a.name` as a submodule when a file of that name actually
    exists under the resolver package keeps this an alias, not a bogus
    `direct` entry for a function literally named "cast".
    """
    aliases: dict[str, str] = {}
    direct: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            tail = (node.module or "").rsplit(".", 1)[-1]
            for a in node.names:
                if a.name in RESOLVER_MODULES:          # from . import characters [as chars]
                    aliases[a.asname or a.name] = a.name
                elif tail in RESOLVER_MODULES and (PACKAGE / "store" / tail / f"{a.name}.py").exists():
                    # from .appearances import cast as appearances_cast
                    aliases[a.asname or a.name] = tail
                elif tail in RESOLVER_MODULES:          # from .characters import read_card
                    direct[a.asname or a.name] = (tail, a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                mod = a.name.rsplit(".", 1)[-1]
                if mod in RESOLVER_MODULES:
                    aliases[a.asname or mod] = mod

    # `read = characters.read_card` is the same call one rename later, and the
    # detector saw bare names only when an import had bound them. Bind the
    # canonical pair so `read(croot, ...)` still resolves -- and so a renamed
    # pure writer stays a pure writer.
    #
    # To a fixed point, like campaign-root names: `reader = read` is a second
    # ordinary rename, and stopping after one hop left any longer chain
    # invisible.
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    while True:
        grew = False
        for node in assigns:
            if isinstance(node.value, ast.Attribute):
                recv = _last_name(node.value.value)
                mod = aliases.get(recv, recv if recv in RESOLVER_MODULES else None)
                canonical = (mod, node.value.attr) if mod else None
            elif isinstance(node.value, ast.Name):
                canonical = direct.get(node.value.id)
            else:
                canonical = None
            if canonical is None:
                continue
            for t in node.targets:
                if isinstance(t, ast.Name) and direct.get(t.id) != canonical:
                    direct[t.id] = canonical
                    grew = True
        if not grew:
            break
    return aliases, direct


#: Parameter names the resolvers give their root argument. `appearances
#: ._actor_name` calls it `aroot`, so looking only for `root` let the keyword
#: form of the very call that motivated RESOLVER_MODULES's `appearances` entry
#: slip past.
ROOT_KEYWORDS = ("root", "aroot", "croot")


def _unresolved_reads(tree: ast.AST):
    """(node, description) for every campaign-root read of an inheritable record."""
    aliases, direct = _resolver_imports(tree)
    root_aliases = frozenset(_root_aliases(tree))

    def resolver_of(func: ast.AST) -> tuple[str, str] | None:
        """Canonical (module, function) if this call reaches a resolver."""
        if isinstance(func, ast.Attribute):
            recv = _last_name(func.value)
            mod = aliases.get(recv, recv if recv in RESOLVER_MODULES else None)
            return (mod, func.attr) if mod else None
        if isinstance(func, ast.Name) and func.id in direct:
            return direct[func.id]
        return None

    for nodes, names in _scopes(tree, root_aliases):
        def is_croot(n: ast.AST) -> bool:
            return _is_root_call(n, root_aliases) or (isinstance(n, ast.Name) and n.id in names)

        for node in nodes:
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                    and is_croot(node.left) and isinstance(node.right, ast.Constant)
                    and _inheritable_literal(node.right.value)):
                yield node, f'<campaign root> / "{node.right.value}"'
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "joinpath" and is_croot(node.func.value)
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and _inheritable_literal(node.args[0].value)):
                # The same join, spelled the other way -- and only the FIRST
                # component decides which directory under the campaign root is
                # entered. Checking every argument reported
                # `joinpath("scenes", sid, "characters")`, which is campaign-local
                # under scenes/, as an inheritable read.
                yield node, f'<campaign root>.joinpath("{node.args[0].value}", ...)'
            elif isinstance(node, ast.Call) and (hit := resolver_of(node.func)):
                mod, fn = hit
                # the root as a keyword is the same call
                root_arg = node.args[0] if node.args else next(
                    (k.value for k in node.keywords if k.arg in ROOT_KEYWORDS), None)
                if (root_arg is not None and is_croot(root_arg)
                        and f"{mod}.{fn}" not in PURE_WRITERS):
                    yield node, f"{mod}.{fn}(<campaign root>, ...)"


def _owning_def(tree: ast.AST, node: ast.AST) -> str:
    """Name of the innermost `def` containing `node`, or "<module>"."""
    best = None
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.lineno <= node.lineno <= getattr(fn, "end_lineno", fn.lineno):
            if best is None or fn.lineno > best.lineno:
                best = fn
    return best.name if best else "<module>"


def _scan():
    """(relative path, node, description, marker reason) over the whole package."""
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        exempt = OWNERS.get(rel, {})
        if "*" in exempt:
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = list(_unresolved_reads(tree))
        for node, what in found:
            if _owning_def(tree, node) in exempt:
                continue
            others = [n for n, _w in found if n is not node]
            yield rel, node, what, guard_markers.marker_reason(MARKER, src, node, others)


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


def test_every_owner_entry_exists_and_says_why():
    """An entry naming a module or function that moved would silently stop
    applying — and a renamed function would take its exemption with it."""
    for rel, funcs in OWNERS.items():
        path = PACKAGE / rel
        assert path.exists(), f"OWNERS names a module that is gone: {rel}"
        defined = {n.name for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name, reason in funcs.items():
            assert name == "*" or name in defined, \
                f"OWNERS names {rel}::{name}, which no longer exists"
            assert len(reason) >= 20, f"OWNERS entry for {rel}::{name} needs a real reason"


def test_owner_exemptions_are_scoped_to_functions_not_whole_files():
    """A file-wide pass left the store's biggest modules unguarded: `campaigns.py`
    was exempt for `ensure_campaign_slim`, so anything else added to it inherited
    that. Only the resolver itself gets a whole file."""
    whole_file = [rel for rel, funcs in OWNERS.items() if "*" in funcs]
    assert whole_file == ["store/overlay.py"], \
        f"only the overlay may be exempt file-wide; also found: {whole_file}"

    # and the scoping must actually bite: a read in a non-exempt function of an
    # owner file is still an offender
    src = ("def ensure_campaign_slim(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    return entities.entity_hash(croot, kind, eid)\n"
           "def some_new_helper(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    return characters.read_card(croot, aid, vid)\n")
    tree = ast.parse(src)
    owners = OWNERS["store/campaigns/lifecycle.py"]
    inside = [_owning_def(tree, n) for n, _w in _unresolved_reads(tree)]
    assert "ensure_campaign_slim" in inside and "some_new_helper" in inside
    assert "some_new_helper" not in owners, \
        "the new helper must not be exempt just because it shares a file"


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


def test_a_closure_over_a_campaign_root_is_still_caught():
    """A nested `def` was scanned with a fresh name set, so closing over `croot`
    — an ordinary refactor — walked straight past the guard."""
    src = ("def outer(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    def read():\n"
           "        return characters.read_card(croot, aid, vid)\n"
           "    return read\n")
    assert list(_unresolved_reads(ast.parse(src))), "a closure over croot escaped"


def test_a_nested_parameter_shadows_the_enclosing_root():
    """Inheriting enclosing bindings must not classify a *different* value that
    merely reuses the name — the `taken` closures inside `overlay.create_*` take
    an id, not a root."""
    src = ("def outer(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    def taken(croot):\n"
           "        return characters.read_card(other, aid, vid)\n"
           "    return taken\n")
    assert not list(_unresolved_reads(ast.parse(src)))


def test_an_aliased_root_factory_import_is_still_a_root():
    """`_last_name` sees only the local alias, so an imported-and-renamed
    factory stopped being recognized as a campaign root at all."""
    src = ("from .campaigns import campaign_root as campaign_dir\n"
           "def f(cid):\n    croot = campaign_dir(cid)\n"
           "    return characters.read_card(croot, aid, vid)\n")
    assert list(_unresolved_reads(ast.parse(src))), "an aliased root factory escaped"


def test_a_directly_imported_pure_writer_keeps_its_canonical_name():
    """A direct import used to lose the function's identity, so a sanctioned
    write became a permanent false positive — including under a rename."""
    for src, why in [
        ("from .assets import put_image\n"
         "def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    put_image(croot, aid, vid, name, data, ext)\n", "direct import"),
        ("from .assets import put_image as save\n"
         "def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    save(croot, aid, vid, name, data, ext)\n", "renamed import"),
    ]:
        assert not list(_unresolved_reads(ast.parse(src))), f"false positive: {why}"


def test_the_resolvers_own_root_keyword_is_recognized():
    """`appearances._actor_name` names its root `aroot`, so looking only for
    `root=` let the keyword form of the call that motivated the `appearances`
    entry slip past."""
    src = ("def f(cid):\n"
           "    return appearances._actor_name(aroot=campaigns.campaign_root(cid), kind=k, actor_id=a, vid=v)\n")
    assert list(_unresolved_reads(ast.parse(src))), "the aroot= keyword form escaped"


def test_a_marker_on_a_nested_call_does_not_exempt_its_parent():
    """An inline marker sits inside every enclosing call's line span, so a
    marker written for an inner read also silenced the outer one."""
    src = ("def f(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    return characters.read_card(\n"
           "        croot,\n"
           "        assets.image_path(croot, a, v),  # overlay-ok: inner one is fine, honestly\n"
           "        vid)\n")
    found = list(_unresolved_reads(ast.parse(src)))
    assert len(found) == 2, f"expected both calls flagged, got {[w for _n, w in found]}"
    reasons = {}
    for node, what in found:
        others = [n for n, _w in found if n is not node]
        reasons[what.split("(")[0]] = guard_markers.marker_reason(MARKER, src, node, others)
    assert reasons["assets.image_path"] is not None, "the inner call lost its own marker"
    assert reasons["characters.read_card"] is None, \
        "the outer call inherited the inner call's marker"


def test_a_preceding_marker_block_does_not_exempt_nested_calls():
    """A comment block above a statement attaches to the statement, so it belongs
    to the outermost flagged node there. Without that, adding a nested call under
    an already-marked one silently inherited the exemption."""
    src = ("def f(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    # overlay-ok: this reason was written for the outer read only\n"
           "    return characters.read_card(croot, assets.image_path(croot, a, v))\n")
    found = list(_unresolved_reads(ast.parse(src)))
    assert len(found) == 2, f"expected both flagged, got {[w for _n, w in found]}"
    reasons = {}
    for node, what in found:
        others = [n for n, _w in found if n is not node]
        reasons[what.split("(")[0]] = guard_markers.marker_reason(MARKER, src, node, others)
    assert reasons["characters.read_card"] is not None, "the outer call lost its own marker"
    assert reasons["assets.image_path"] is None, \
        "the nested call inherited the block written for its parent"


def test_a_same_line_nested_marker_does_not_exempt_its_parent():
    """Two calls nested on one physical line have identical *line* spans, so
    ownership by span width was false for both and the marker exempted the outer
    call too. Containment is by line and column."""
    src = ("def f(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    return characters.read_card(croot, assets.image_path(croot, a, v))  "
           "# overlay-ok: meant for the inner call only, honestly\n")
    found = list(_unresolved_reads(ast.parse(src)))
    assert len(found) == 2, f"expected both flagged, got {[w for _n, w in found]}"
    reasons = {}
    for node, what in found:
        others = [n for n, _w in found if n is not node]
        reasons[what.split("(")[0]] = guard_markers.marker_reason(MARKER, src, node, others)
    assert reasons["assets.image_path"] is not None, "the inner call lost its own marker"
    assert reasons["characters.read_card"] is None, \
        "the outer call on the same line inherited the inner call's marker"


def test_a_marker_must_be_the_comment_not_a_mention_of_it():
    """`partition` matched the marker anywhere in the comment, so a warning
    *about* the marker turned the guard off."""
    src = "x = read(croot)  # do not add overlay-ok: this is still unsafe\n"
    node = ast.parse(src).body[0]
    assert guard_markers.marker_reason(MARKER, src, node) is None

    src = "x = read(croot)  # overlay-ok: this one really is campaign-local\n"
    node = ast.parse(src).body[0]
    assert guard_markers.marker_reason(MARKER, src, node) == \
        "this one really is campaign-local"


def test_a_marker_shared_by_two_siblings_exempts_neither():
    """Neither sibling contains the other, so both used to accept the same
    comment — one reason silencing two calls. Ambiguous placement wins nothing."""
    src = ("def f(cid):\n"
           "    croot = campaigns.campaign_root(cid)\n"
           "    return (characters.read_card(croot, a, v), assets.image_path(croot, a, v))"
           "  # overlay-ok: which of these two did I mean, honestly\n")
    found = list(_unresolved_reads(ast.parse(src)))
    assert len(found) == 2, f"expected both flagged, got {[w for _n, w in found]}"
    for node, what in found:
        others = [n for n, _w in found if n is not node]
        assert guard_markers.marker_reason(MARKER, src, node, others) is None, \
            f"{what} claimed a marker it shares with a sibling"


def test_a_lambda_parameter_shadows_the_enclosing_root():
    """A lambda binds parameters like a `def`, but is an expression — traversing
    it in the enclosing scope meant its parameters never shadowed, so an
    ordinary def→lambda conversion became a false positive."""
    src = ("def f(cid):\n"
           "    root = campaigns.campaign_root(cid)\n"
           "    return lambda root: characters.read_card(root, aid, vid)\n")
    assert not list(_unresolved_reads(ast.parse(src))), \
        "the lambda parameter did not shadow the enclosing campaign root"

    # a lambda that really does close over the campaign root is still caught
    src = ("def f(cid):\n"
           "    root = campaigns.campaign_root(cid)\n"
           "    return lambda aid: characters.read_card(root, aid, vid)\n")
    assert list(_unresolved_reads(ast.parse(src))), "a closing lambda escaped"

    # and a parameter *named* croot stays a campaign root wherever it appears --
    # that is the naming convention, not the closure, and it outranks shadowing
    src = "f = lambda croot: characters.read_card(croot, aid, vid)\n"
    assert list(_unresolved_reads(ast.parse(src))), \
        "the `croot` parameter convention stopped applying inside a lambda"


def test_a_resolver_alias_chain_is_followed_to_its_end():
    """`reader = read` is a second ordinary rename; one hop was not enough."""
    src = ("read = characters.read_card\nreader = read\n"
           "def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
           "    return reader(croot, aid, vid)\n")
    assert list(_unresolved_reads(ast.parse(src))), "a two-hop resolver alias escaped"


def test_a_later_joinpath_component_is_not_an_inheritable_read():
    """Only the first component decides which directory under the campaign root
    is entered. Checking every argument flagged campaign-local scene paths."""
    src = ("def f(cid, sid):\n    croot = campaigns.campaign_root(cid)\n"
           "    return croot.joinpath('scenes', sid, 'characters').exists()\n")
    assert not list(_unresolved_reads(ast.parse(src))), \
        "a campaign-local path was flagged because a later component was named 'characters'"


def test_a_multi_segment_path_literal_is_still_caught():
    """`pathlib` joins `"characters/alice"` exactly as it joins the segments one
    at a time, so an ordinary path-formatting choice hid the read."""
    for src, why in [
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return (croot / 'characters/alice/character.md').read_text()\n", "one literal"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return croot.joinpath('characters/alice').exists()\n", "joinpath literal"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return (croot / 'characters\\\\alice').exists()\n", "backslash literal"),
    ]:
        assert list(_unresolved_reads(ast.parse(src))), f"missed: {why}"

    # a campaign-local dir that merely starts with a similar word must not fire
    src = ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
           "    return (croot / 'charactersheets/x').exists()\n")
    assert not list(_unresolved_reads(ast.parse(src))), "false positive on a prefix match"


def test_a_resolver_bound_to_a_local_alias_is_still_caught():
    """`read = characters.read_card` is the same call one rename later; bare
    names were recognized only when an import had bound them."""
    src = ("read = characters.read_card\n"
           "def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
           "    return read(croot, aid, vid)\n")
    assert list(_unresolved_reads(ast.parse(src))), "an aliased resolver callable escaped"

    # and the same rename of a sanctioned writer stays sanctioned
    src = ("save = assets.put_image\n"
           "def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
           "    save(croot, aid, vid, name, data, ext)\n")
    assert not list(_unresolved_reads(ast.parse(src))), "an aliased pure writer was flagged"


def test_a_nested_def_header_is_read_in_the_enclosing_scope():
    """A default value and a decorator run when the `def` executes, so they are
    the *enclosing* scope's code. Skipping the whole node dropped them."""
    for src, why in [
        ("def outer(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    def read(card=characters.read_card(croot, aid, vid)):\n        return card\n"
         "    return read\n", "argument default"),
        ("def outer(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    @wraps(characters.read_card(croot, aid, vid))\n"
         "    def read():\n        return 1\n"
         "    return read\n", "decorator"),
    ]:
        assert list(_unresolved_reads(ast.parse(src))), f"missed: {why}"


def test_lorebook_commit_is_a_watched_resolver():
    """It reads existing entities off the root to skip duplicates, so a campaign
    root would miss inherited entries and create a shadowing record."""
    src = ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
           "    return lorebook.commit(croot, entries)\n")
    assert list(_unresolved_reads(ast.parse(src))), "lorebook.commit was not watched"


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
