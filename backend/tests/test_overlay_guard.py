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
inheritable kind, in a read.** Writes are not flagged — a write is *supposed*
to land campaign-side; that is what materialization is.

Honest about its reach, like `test_atomic_guard.py`:

- It follows names only inside the function that binds them. Passing a campaign
  root into a helper that takes a bare ``root``/``path`` parameter escapes it
  (`context._char_name` is such a helper). Widening that means real type
  analysis, which is not worth it here.
- `MUTATORS` classifies by name. A reader called something unexpected is
  flagged, not missed — the bias is deliberate, matching the atomic guard: a
  false positive is a loud test failure a human clears with a marker or a
  rename; a false negative is the bug.
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

#: Name prefixes of resolver functions that *write*. A write belongs on the
#: campaign root: that is how a record materializes.
MUTATORS = ("write", "put_", "create_", "update_", "delete_", "set_", "import_",
            "promote_", "clear_", "remove_", "seed", "copy_", "download_")

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
        for node in nodes:
            if isinstance(node, ast.Assign) and _is_root_call(node.value):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        yield nodes, names


def _unresolved_reads(tree: ast.AST):
    """(node, description) for every campaign-root read of an inheritable record."""
    for nodes, names in _scopes(tree):
        def is_croot(n: ast.AST) -> bool:
            return _is_root_call(n) or (isinstance(n, ast.Name) and n.id in names)

        for node in nodes:
            if (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
                    and is_croot(node.left) and isinstance(node.right, ast.Constant)
                    and node.right.value in INHERITED_SEGMENTS):
                yield node, f'<campaign root> / "{node.right.value}"'
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                mod, fn = _last_name(node.func.value), node.func.attr
                if (mod in RESOLVER_MODULES and node.args and is_croot(node.args[0])
                        and not fn.startswith(MUTATORS)):
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


def test_writes_and_campaign_local_records_are_not_flagged():
    """A write is how a record materializes, and campaign-local records have
    nothing to inherit — flagging either would make the guard noise."""
    for src, why in [
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    entities.update_entity(croot, kind, eid, body=b)\n", "a write"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    assets.put_image(croot, aid, vid, name, data, ext)\n", "a write"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return (croot / 'climate.json').read_text()\n", "campaign-local file"),
        ("def f(cid):\n    croot = campaigns.campaign_root(cid)\n"
         "    return playstate.read_state(croot, aid)\n", "campaign-local record"),
    ]:
        assert not list(_unresolved_reads(ast.parse(src))), f"false positive: {why}"


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
