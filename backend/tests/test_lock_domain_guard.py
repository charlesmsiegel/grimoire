"""Guard: lock-domain membership is declared in code, not in a paragraph.

``store/locks.py`` opens by saying "**This list is the domain**". That was
literally true and is the bug: the domain was a prose list a human had to
remember to update, so a campaign-scoped mutator could be written, reviewed and
shipped entirely outside the exclusion without anything failing. It happened —
``rolls`` kept a private lock registry and was outside the shared domain until
#255, and the docstring itself admits four more (``campaigns.rename_campaign``
/ ``set_campaign_response`` / ``touch`` / ``delete_campaign``). Nothing detected
either omission, because nothing could: prose does not fail a test run.

This guard moves the domain into ``locks.DOMAIN_MODULES`` (its public mutators
all serialize), ``locks.OUTSIDE_DOMAIN`` (deliberately not, with the reason) and
``locks.UNREVIEWED`` (mutates campaign state, never assessed — a frozen
backlog), then holds those declarations and the code to each other:

- a module in ``DOMAIN_MODULES`` must have *every* public campaign-scoped
  mutator serialize, so a new ``scenes`` mutator that forgets ``@_serialized``
  fails — the transcript-loss case of #254;
- a module that mutates campaign state and appears in none of the three fails
  until somebody classifies it, and ``UNREVIEWED`` may not grow. This is the
  #255 shape, and the only check here that would have caught it;
- a module declared outside must still actually have an unserialized mutator, so
  the moment one is fixed the declaration has to move rather than quietly
  becoming a lie;
- a declared module that stops mutating campaign state has to be dropped, so the
  lists cannot accumulate phantoms.

What it deliberately does not do is *fix* anything. The four mutators
``locks.py`` used to name in prose are still unserialized; they are now recorded
in ``OUTSIDE_DOMAIN`` with the reason, where a test watches them, instead of in
a paragraph where nothing did.

Honest about its reach — the house standard set by ``test_atomic_guard.py``:

- **"Campaign-scoped" is approximated by a ``cid`` parameter.** That is the
  codebase's own convention, not a proof. A mutator that derives the campaign id
  some other way (from a path, a scene record, a request object) is not seen.
- **Serialization is recognized from a CLOSED WHITELIST of forms, and
  everything else fails loud.** This is the one design decision worth
  understanding before trusting the guard, and it was learned the hard way:
  seven review rounds produced roughly eight findings of a single shape — a
  syntactic test standing in for the semantic fact "this lock protects this
  campaign". `hold_all(x)` where `x` merely mentions `cid`; an alias whose
  parameters bind the caller's `cid` onto a different name; a local variable
  shadowing `campaign_lock`. Each was true, and each was a new way to spell
  "looks locked".

  So the polarity is inverted. Rather than accepting an acquisition unless a
  problem is spotted, only these are recognized:

  1. ``with locks.campaign_lock(cid)`` — receiver ``locks``, which nothing in
     the module rebinds, and argument exactly the name ``cid``;
  2. ``with locks.hold_all([... cid ...])`` — a literal container that visibly
     contains ``cid`` (or any argument at all, in a function that takes no
     ``cid`` — that covers the function's own body only, and never vouches for
     a caller: see ``_binds_the_campaign``);
  3. ``with <alias>(cid, ...)`` — a module-local helper whose FIRST parameter is
     ``cid`` and which returns or yields one of the above, so positional binding
     makes the two the same campaign;
  4. ``@<decorator>`` applied DIRECTLY to the function, whose returned wrapper
     invokes the decorated function under one of the above on every path it
     executes, passing ``cid`` first — and whose target takes ``cid`` first and
     runs its body when called, rather than deferring it (an ``async def`` or a
     generator only *constructs* something inside the lock).

  Anything else — a computed iterable, a keyword-bound alias, a lock reached
  through an object this cannot resolve — reads as *unserialized*. That will
  produce false alarms on legitimate code the whitelist has not learned. That is
  the intended trade: the marker is cheap and visible, and a false alarm costs a
  comment while a false negative costs a transcript.

  Four things the whitelist deliberately does *not* trust. A name that only
  module scope may bind — a lock alias or decorator resolved from a nested
  ``def`` in some unrelated factory is not the name module scope actually binds.
  A name reached through an attribute — ``@hooks.safe`` is not this module's
  ``safe``. A decorator that is not the innermost one, because composition order
  decides whether the write happens inside the lock: ``@safe`` over ``@defer``
  locks around a call that returns a callback and writes nothing. And any name a
  binding elsewhere in the module can shadow — the spelling ``locks``
  (``_rebinds_locks``), or an alias or decorator a parameter rebinds
  (``_shadowed_names``).

  That last one is the second rule this file had to learn to state once. **A
  name is not a binding.** It was fixed four separate times before it was
  written down — ``campaign_lock=nullcontext`` as a parameter, then ``locks``
  as a parameter, then a validated alias as a parameter — and on the mutation
  side the same rule appears as ``import shutil as fs`` and
  ``publish = atomic.write_text``. Every trusted spelling is now resolved to
  what actually binds it, and a name this cannot resolve fails loud.

  Running through all four forms is one rule that took five rounds to state in
  one place: **an acquisition only counts when this campaign's id reaches the
  parameter the lock is keyed on.** It has to be checked at every boundary the
  id crosses — into an alias, into a delegated helper, into a decorated target —
  and each boundary was a separate finding before it was a single rule.

  The rule's limit is worth stating beside it, because assuming otherwise was
  itself a finding: when a callee has NO parameter the id can reach, there is no
  binding to verify and therefore no coverage to infer. Not even
  ``locks.hold_all(...)``, whose argument may be ``[]`` or another campaign's
  id. Such a callee never vouches for its caller.
- **The mutation surface is inverted too, inside the filesystem namespace.**
  The other half of the same lesson, learned one round later. "What counts as
  publishing" was an enumeration, and six rounds each added exactly one more
  entry to it — ``touch``, ``mkdir``, a raw ``open``, imported writers,
  ``os.write``/``os.fdopen``, ``os.open``. A rule that grows by one every time
  somebody looks is not a rule that can be finished by looking. So a call on
  ``os`` or ``shutil`` is a publication unless its name is a known reader, and
  ``shutil.copytree`` — used twice in this package and matched by no
  enumeration — is caught by that inversion rather than by a seventh entry.

  Which namespace a receiver names is resolved through the module's import
  bindings, not by its spelling: ``import shutil as fs`` defeated the first
  version, and since ``copytree`` matched no enumeration either, the module
  dropped out of the survey altogether rather than reading as unlocked.

  It stops at the namespace boundary, and that boundary is real: ``p.foo()``
  cannot be told from ``some_dict.foo()`` without type inference, so a
  path-valued receiver keeps the enumerated names and keeps their blind spots
  (``Path.replace`` is the documented one).
- **Analysis is per-module.** Mutation propagates through a module's own
  helpers, never across an import, so a function whose only mutation happens
  inside a *different* module's unserialized mutator is not itself flagged. The
  callee is flagged in its own file instead, which is where the fix goes; what
  this misses is the caller that spans two such calls non-atomically.
- **Every WRITE must run under the lock; the READ need not.** The write half is
  checked: a function that locks around part of its body and publishes outside
  that block fails, on every branch, including through a comprehension. That was
  not true until round fifteen -- entering a lock anywhere made the whole
  function read as serialized -- and it is the one limit here that got narrower
  rather than being restated.

  The read half is still open, and it is the bug ``scenes._serialized`` was
  written to fix ("The lock has to span the READ as well as the write"). A
  read-modify-write whose read sits outside the ``with`` passes, because
  nothing here can tell which values a write depends on. Likewise two
  individually-locked calls made non-atomically.
- **It does not decide which list a module belongs in.** Classification is a
  human judgment about whether that state can lose an update; the guard only
  insists the judgment be written down, stay true, and be revisited when the
  code moves underneath it.
"""

from __future__ import annotations

import ast
import functools
import pathlib
import typing

import grimoire
from grimoire.store import locks

from . import guard_markers

PACKAGE = pathlib.Path(grimoire.__file__).parent
# Compared by path, not by `name == "locks.py"`: a second module with that name
# anywhere under the package would otherwise be skipped along with it.
LOCKS_PY = pathlib.Path(locks.__file__)

# The trailing colon is load-bearing -- see test_pydantic_guard.MARKER.
MARKER = "lock-domain-ok:"

# Publication primitives, for receivers this cannot identify as a namespace.
# The fallback, not the primary rule -- see `_FS_NAMESPACES` below, which
# inverts the question everywhere the receiver IS identifiable.
_ATOMIC_WRITERS = ("write_text", "write_bytes")
# Removal and rename publish a change without writing bytes, so a guard that
# watched only `atomic.*` would miss `delete_campaign`'s `rmtree` entirely.
#
# Split by whether the NAME alone is evidence, because two of these are also
# ordinary methods on builtins and being receiver-blind about them was wrong:
# `doc.replace(...)` in `store/export.py` is `str.replace`, and
# `rec["scenes"].remove(sid)` in `store/appearances.py` is `list.remove`.
# Neither touches a filesystem, and counting them classified `store.export` as a
# campaign mutator on the strength of a string operation.
_FS_ONLY = ("rmtree", "unlink", "rmdir", "rename")   # no builtin type has these
_FS_AMBIGUOUS = ("remove", "replace")                # also str/list/set methods
_FS_MODULES = ("os", "shutil")                       # ...so require the receiver

# --- the filesystem namespace, inverted ---------------------------------------
#
# Everything above is an ENUMERATION of things that publish, and every review
# round that touched it found another entry: `touch`, then `mkdir`, then raw
# `open`, then imported writers, then `os.write`/`os.fdopen`, then `os.open`.
# Enumeration cannot be finished by inspection -- `os` alone also offers
# `truncate`, `symlink`, `link`, `makedirs`, `mknod`, `renames`, `removedirs`,
# `chmod`, `utime`, and `shutil` offers `copy`, `copy2`, `copyfile`, `move`,
# `copytree`. `shutil.copytree` is used twice in this package today and was
# invisible to the list above.
#
# So inside a namespace that is unambiguously the filesystem, the polarity is
# inverted to match how locks are recognized: a call on `os` or `shutil` is a
# publication UNLESS its name is a known reader. A primitive nobody thought of
# now fails loud, and the cost of a wrong guess is a whitelist entry.
#
# It is bounded to receivers that NAME A MODULE on purpose. The same inversion
# on a bare variable is not available: `p.foo()` cannot be told from
# `some_dict.foo()` without type inference, so a path-valued receiver keeps the
# enumerated names above and keeps their blind spots with it (`Path.replace` is
# the documented one).
_FS_NAMESPACES = ("os", "shutil")
_FS_READERS = frozenset({
    # os: inspection and process state, not publication
    "stat", "lstat", "fstat", "statvfs", "access", "listdir", "scandir", "walk",
    "readlink", "getcwd", "getcwdb", "fspath", "fsdecode", "fsencode", "read",
    "pread", "lseek", "close", "closerange", "dup", "dup2", "pipe", "isatty",
    "get_terminal_size", "cpu_count", "getpid", "getppid", "getuid", "geteuid",
    "getgid", "getegid", "getlogin", "urandom", "strerror", "get_blocking",
    "getenv", "environb", "device_encoding", "listxattr", "getxattr",
    "path", "sep", "linesep", "pathsep", "curdir", "pardir", "extsep", "altsep",
    "devnull", "name", "environ",
    # shutil: measurement only
    "disk_usage", "which", "get_archive_formats", "get_unpack_formats",
    "ignore_patterns",
})
# `store.atomic` exists to publish; its whole public surface is a write. Named
# separately because its readers are its own, not the stdlib's.
_ATOMIC_READERS = frozenset({"replace_is_atomic"})
# `touch` is ambiguous the other way round: `Path.touch` has no fixed receiver
# to match, so it is treated as a file creation UNLESS the receiver names a
# module in this package that defines its own `touch`. Only one does.
_TOUCH_FUNCTIONS = ("campaigns",)
# The two entry points into the domain. `hold_all` counts because the
# multi-campaign holders reach every campaign lock through it.
_LOCK_CALLS = ("campaign_lock", "hold_all")


def _called_name(func: ast.expr) -> str | None:
    """The trailing name of a call target: `f`, `m.f` and `pkg.m.f` all give `f`.

    Receiver-agnostic on purpose. `locks.campaign_lock`, `store.locks.
    campaign_lock` and a bare `campaign_lock` are the same acquisition, and
    resolving the receiver statically buys nothing here -- no other name in this
    package is `campaign_lock`.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _receiver_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        value = func.value
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            return value.attr
    return None


def _bindings(tree: ast.AST):
    """(target name, value node) for every assignment in `tree`.

    `x = v`, `x = y = v` and `x: T = v` all bind a name, and only the first two
    were looked at. An annotated re-export -- `put: Callable[..., None] = _put`
    -- is a different AST node, so the public name was never collected at all
    and `_analyze` skipped the private writer as private: a domain module could
    expose an unlocked public mutator and pass.

    Walrus is deliberately absent: it binds inside an expression, never at the
    module scope these callers care about.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                yield target.id, value


def _functions(tree: ast.AST) -> dict[str, list[ast.AST]]:
    """Every function in the module, grouped by name, nested ones included.

    EVERY definition, not the first one. Keeping only the first discarded the
    implementation that actually runs: an `@overload` stub ahead of a writing
    body left the guard analyzing the stub, so the module read as non-mutating;
    a conditional definition whose first branch locks and whose second does not
    read as serialized. Both are silent false negatives.

    The predicates below resolve a group conservatively in the direction that
    fails loud -- a name MUTATES if any definition does, and SERIALIZES only if
    every definition does -- so an ambiguous name can never be quieter than its
    worst branch.
    """
    out: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, []).append(node)

    # `put = _put` re-exports a function under another name, and that name is
    # what callers use. Without this the analysis saw only `_put`, skipped it as
    # a private helper, and never knew `put` existed -- so a domain module could
    # expose an unlocked public mutator and pass. Iterated for `a = b; c = a`.
    for _ in range(len(out) + 1):
        grew = False
        for name, value in _bindings(tree):
            if not isinstance(value, ast.Name):
                continue
            source = out.get(value.id)
            if not source:
                continue
            have = out.setdefault(name, [])
            for d in source:
                if d not in have:
                    have.append(d)
                    grew = True
        if not grew:
            break
    return out


def _any(defs, predicate) -> bool:
    return any(predicate(d) for d in defs)


def _all(defs, predicate) -> bool:
    return bool(defs) and all(predicate(d) for d in defs)


def _calls(fn: ast.AST):
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            yield node


def _takes_cid(fn: ast.AST) -> bool:
    """The codebase's convention for "this operates on one campaign"."""
    args = fn.args
    return any(a.arg == "cid"
               for a in [*args.posonlyargs, *args.args, *args.kwonlyargs])


def _fs_namespaces(tree: ast.AST) -> dict[str, str]:
    """Local name -> the filesystem namespace it refers to.

    The inverted namespace check keyed on the receiver's spelling, so
    `import shutil as fs; fs.copytree(...)` escaped it entirely -- and because
    `copytree` is in no enumeration either, the module vanished from the survey
    rather than merely reading as unlocked. Import bindings are resolved instead
    of trusted.

    `import os.path as p` is deliberately NOT mapped: the alias binds the
    submodule, and `os.path` publishes nothing, so mapping it to `os` would
    read every `p.join(...)` as a write.
    """
    out = {name: name for name in (*_FS_NAMESPACES, "atomic")}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname and "." in alias.name:
                    continue             # binds a submodule, not the root
                root = alias.name.split(".")[0]
                if root in _FS_NAMESPACES:
                    out[alias.asname or root] = root
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in (*_FS_NAMESPACES, "atomic"):
                    out[alias.asname or alias.name] = alias.name
    return out


def _imported_writers(tree: ast.AST) -> set[str]:
    """Local names bound by `from <atomic|os|shutil> import <primitive>`.

    Without this, `from .atomic import write_text` followed by a bare
    `write_text(p, x)` is invisible: the call's target is an `ast.Name`, not an
    attribute, so there is no receiver to key on. The comment here used to claim
    `test_atomic_guard` caught that form as a fallback. It does not — it matches
    `write_text`/`write_bytes` only as attributes — so nothing did.

    Resolved from the import rather than matching the bare names everywhere,
    which would flag any local helper that happened to be called `remove`.
    """
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        source = node.module.rsplit(".", 1)[-1]
        # Inverted the same way the attribute path is: importing a name OUT of
        # a filesystem namespace does not make it safer, so `from os import
        # truncate` binds a writer even though nothing enumerated `truncate`.
        if source == "atomic":
            readers = _ATOMIC_READERS
        elif source in _FS_NAMESPACES:
            readers = _FS_READERS
        else:
            continue
        out |= {(a.asname or a.name) for a in node.names if a.name not in readers}
    return out


class _Surface(typing.NamedTuple):
    """What counts as publication in one module: the names it imported out of a
    filesystem namespace, and what each local receiver name refers to.

    Carried together because both halves answer the same question and drifted
    apart when only one was threaded -- the namespace inversion keyed on the
    literal spellings `os`/`shutil` while `_imported_writers` already resolved
    bindings, so `import shutil as fs` defeated one and not the other."""
    imported: frozenset = frozenset()
    namespaces: dict = {}
    trusted_locks: bool = True

    @classmethod
    def of(cls, tree: ast.AST) -> "_Surface":
        namespaces = _fs_namespaces(tree)
        writers = _imported_writers(tree) | _assigned_writers(tree, namespaces)
        return cls(frozenset(writers), namespaces, not _rebinds_locks(tree))


def _assigned_writers(tree: ast.AST, namespaces: dict) -> set[str]:
    """Local names bound to a publication primitive by ASSIGNMENT.

    `_imported_writers` covered `from .atomic import write_text` and the bare
    `write_text(p, x)` that follows it. It did not cover

        publish = atomic.write_text
        def put(cid):
            publish(p, data)            # seen by nothing

    which is the same binding question asked with a different keyword, and had
    the same consequence the `import shutil as fs` alias did: the call falls
    through the bare-name branch, the module never reads as mutating, and it
    leaves the survey entirely rather than reading as unlocked.

    Iterated so `a = atomic.write_text; b = a` resolves, the way `_functions`
    already resolves re-export chains.
    """
    out: set[str] = set()
    bound = list(_bindings(tree))
    for _ in range(len(bound) + 1):
        grew = False
        for name, value in bound:
            if isinstance(value, ast.Attribute):
                writer = _names_a_writer(value.attr, _receiver_name(value), namespaces)
            elif isinstance(value, ast.Name):
                writer = value.id in out
            else:
                continue
            if writer and name not in out:
                out.add(name)
                grew = True
        if not grew:
            break
    return out


def _rebinds_locks(tree: ast.AST) -> bool:
    """Whether anything in this module binds the name `locks` other than an
    import of the real module.

    `_is_lock_context` requires the receiver to be spelled `locks`, and that was
    described as "requiring the receiver costs nothing" — but a spelling is not
    a binding:

        def put(cid, locks=fake):            # the parameter shadows the module
            with locks.campaign_lock(cid):   # `fake.campaign_lock` locks nothing
                atomic.write_text(p, x)

    This is the SAME defect the receiver check was introduced to fix
    (`def put(cid, campaign_lock=nullcontext)`), moved one level up — patched at
    the instance rather than at the class, which is the pattern this guard's
    review keeps finding in it. `_shadowed_names` is the general form; this is
    the one name that is a receiver rather than a callee, so it is answered
    separately and by the same rule.

    Answered for the whole module rather than per function, deliberately. A
    binding in one function does not shadow another, so this is coarser than
    Python's scoping; it is also the version that cannot be defeated by putting
    the rebinding somewhere the per-function check does not look. No module in
    this package binds `locks` to anything, so the strictness is free — and when
    it is not free, the whole module fails loud, which is the trade made
    everywhere else here.
    """
    # `store.locks.campaign_lock` is the other spelling the docstring supports,
    # and `_receiver_name` reads its receiver as `locks` -- discarding the outer
    # `store`, which can itself be a parameter. So every root a `.locks.` chain
    # hangs off is watched alongside the name itself.
    watched = {"locks"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "locks" \
                and isinstance(node.value, ast.Name):
            watched.add(node.value.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) \
                and node.id in watched:
            return True                  # assignment, `for`, `with ... as`, walrus
        if isinstance(node, ast.arg) and node.arg in watched:
            return True                  # a parameter, of any function here
        if isinstance(node, ast.alias) and (node.asname or node.name) in watched \
                and node.name.rsplit(".", 1)[-1] not in watched:
            return True                  # `import json as locks`
    return False


def _shadowed_names(tree: ast.AST) -> set[str]:
    """Names bound by a PARAMETER anywhere in this module.

    The general form of the `locks` rule, and the same defect a third time: a
    module-level `helper` validated as a lock alias, or `_serialized` validated
    as a locking decorator, is not the `helper` or `_serialized` that a
    parameter of the same name refers to —

        def put(cid, helper=nullcontext):
            with helper(cid):            # nullcontext holds nothing
                atomic.write_text(p, x)

    `_module_level` already refuses a name that module scope rebinds to
    something unresolvable. A parameter rebinds it too, and was not looked at,
    so the whitelist kept vouching for the module-level definition while the
    call reached the parameter.

    Whole-module and parameters-only, for the reason `_rebinds_locks` gives:
    per-function scoping is what the previous two versions of this rule tried,
    and the coarse answer is the one that cannot be sidestepped by moving the
    binding. Assignments are left to `_module_level`, which resolves the
    legitimate `put = _put` re-export rather than poisoning it.
    """
    return {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)}


def _writes_directly(fn: ast.AST, surface: _Surface = _Surface()) -> bool:
    """Whether `fn` publishes a change to disk itself.

    `Path.replace` — a real atomic rename — is deliberately NOT matched: it is
    indistinguishable from `str.replace` without type inference. That is a known
    blind spot, accepted so that every string substitution is not read as a
    campaign mutation; `os.replace`, which is how `store.atomic` actually
    publishes, is matched.
    """
    return any(_is_write_call(node, surface) for node in _calls(fn))


def _is_write_call(node: ast.Call, surface: _Surface = _Surface()) -> bool:
    """Whether this ONE call publishes.

    Split out so that "does this function write?" and "does THIS write run under
    the lock?" are the same question asked twice, rather than two rules. The
    second question had no answer at all until round fifteen: a mutator could
    enter the lock around `pass` and write after it.
    """
    name = _called_name(node.func)
    if isinstance(node.func, ast.Name):
        # `from .atomic import write_text` — no receiver to key on.
        if name in surface.imported:
            return True
        return name == "open" and _is_write_mode(node)   # the builtin, not a method
    if not isinstance(node.func, ast.Attribute):
        return False
    receiver = _receiver_name(node.func)
    # The inverted namespace, checked BEFORE the enumerated names so the
    # enumeration is a fallback for receivers this cannot identify rather than
    # the primary rule. `os.path.join` is unaffected: `_receiver_name` reads its
    # receiver as `path`, which names no namespace here, so it falls through to
    # the enumeration and matches nothing -- right, since `os.path` publishes
    # nothing. Resolved through the module's import bindings, not matched
    # against the literal spellings.
    if _names_a_writer(name, receiver, surface.namespaces):
        return True
    return name == "open" and _is_write_mode(node)       # the raw form `atomic` wraps


def _names_a_writer(name: str | None, receiver: str | None, namespaces: dict) -> bool:
    """Whether `receiver.name` publishes, judged from the two names alone.

    Split out of `_writes_directly` so that a CALL and a BINDING of the same
    attribute are answered by one rule. `publish = atomic.write_text` followed
    by `publish(p, data)` was seen by neither: the call reaches the bare-name
    branch, which only knew names from `from ... import ...`. So the module left
    the survey entirely rather than reading as unlocked -- the same failure the
    `import shutil as fs` alias produced, one binding form along.

    Everything here is argument-independent on purpose; `open(p, "w")` is the
    one primitive that needs its arguments, and it stays at the call site.
    """
    namespace = namespaces.get(receiver)
    # The inverted namespace, checked BEFORE the enumerated names so the
    # enumeration is a fallback for receivers this cannot identify rather than
    # the primary rule. `os.path.join` is unaffected: `_receiver_name` reads its
    # receiver as `path`, which names no namespace here, so it falls through to
    # the enumeration and matches nothing -- right, since `os.path` publishes
    # nothing. Resolved through the module's import bindings, not matched
    # against the literal spellings.
    if namespace in _FS_NAMESPACES and name not in _FS_READERS:
        return True
    if namespace == "atomic" and name not in _ATOMIC_READERS:
        return True
    if name in _ATOMIC_WRITERS:
        # ANY receiver, not just `atomic`. The claim that `test_atomic_guard`
        # reduces publication to the helper surface was wrong twice over: it
        # matches these names only as attributes, and it lets a raw write
        # through entirely when a human clears it with `# atomic-ok:`. Two such
        # exemptions exist today. A write that guard forgives is still a write
        # this one has to see.
        return True
    if name in ("write", "fdopen") and receiver == "os":
        # `os.write(fd, b)` / `os.fdopen(fd, "wb")` bypass `open()` entirely.
        return True
    if name == "mkdir":
        # A directory is a namespace entry others can observe, and neither guard
        # looked for one -- a mutator that publishes state purely by creating an
        # initialization or completion directory was invisible.
        return True
    if name in _FS_ONLY:
        return True
    if name in _FS_AMBIGUOUS and receiver in _FS_MODULES:
        return True
    if name == "touch" and receiver not in _TOUCH_FUNCTIONS:
        # `Path.touch()` publishes a pathname others can observe (a marker or
        # completion file) while writing no bytes, so neither this guard's
        # writers nor `test_atomic_guard` would see it.
        #
        # Keyed on the receiver, NOT on arity. Arity looked like a clean
        # discriminator against this package's own `campaigns.touch(cid)` and
        # was simply wrong: `Path.touch(self, mode=0o666, exist_ok=True)` takes
        # both positionally, so `marker.touch(0o600)` is a real file creation
        # that a zero-positional-args test rejects.
        return True
    return False


def _is_write_mode(node: ast.Call) -> bool:
    """`open(p, "w")` / `p.open("w")` in any writable mode -- mirrors
    `test_atomic_guard._is_write_mode`, tested for the characters that make a
    mode writable rather than enumerating literals."""
    for arg in [*node.args[:2], *(k.value for k in node.keywords if k.arg == "mode")]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                and 0 < len(arg.value) <= 3 and set(arg.value) <= set("rwaxbt+") \
                and any(c in arg.value for c in "wax+"):
            return True
    return False


def _own_body(fn: ast.AST):
    """Nodes belonging to `fn` itself, NOT to functions nested inside it.

    `ast.walk` descends into nested `def`s, and taking that at face value was a
    hole: a lock inside a *callback* made the enclosing function read as
    serialized while its own writes ran under nothing —

        def outer(cid):
            def _cb():
                with locks.campaign_lock(cid): ...
            atomic.write_text(p, x)     # serialized by nothing
            register(_cb)

    which is precisely the shape this package uses (`proposals` persists a reply
    through a callback). The decorator case is the one place a nested lock really
    does cover the outer name, and it is handled separately in `_serializing`.
    """
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue                     # its body is its own, not `fn`'s
        stack.extend(ast.iter_child_nodes(node))


def _guards_the_campaign(call: ast.Call) -> bool:
    """The lock being entered is keyed on `cid` — this codebase's name for the
    campaign id, and the same convention `_takes_cid` uses to decide a function
    is in scope at all.

    Checking only the callee name let a mutator enter the WRONG lock and pass:
    `with locks.campaign_lock(sid): atomic.write_text(...)` serializes two
    concurrent calls for one campaign under two different locks, which loses
    updates exactly as if there were no lock.

    Matched on the name `cid` rather than on membership in this function's
    parameters, because a closure that captures `cid` from an enclosing scope
    locks the right campaign while having no parameter of its own.
    """
    return bool(call.args) and isinstance(call.args[0], ast.Name) \
        and call.args[0].id == "cid"


def _locks_its_first_param(fn: ast.AST) -> bool:
    """The lock this helper hands back is keyed on its OWN first parameter.

    What makes positional binding safe to reason about: if the helper locks its
    first parameter and the caller passes `cid` first, the two are the same
    campaign. Any other arrangement is a helper this cannot follow, and it fails
    loud rather than being assumed benign.
    """
    params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
    return bool(params) and params[0] == "cid"


def _produces_lock(fn: ast.AST) -> bool:
    """`fn` hands back a campaign lock, keyed on `cid`, for someone else to enter.

    `audit`-shaped aliasing: a one-line helper that *returns* `campaign_lock(cid)`
    never enters it, so it is not itself serialized — but `with _alias(cid):` in
    a caller is a real acquisition. Kept separate from `_enters_lock` so
    that distinction survives.

    It has to be a `return`/`yield` of the lock, not a mention of the name.
    Accepting any helper that names `campaign_lock` would let
    `def _helper(cid): log(campaign_lock); return nullcontext()` stand in for an
    acquisition, and would accept a helper that locks some *other* id — the same
    two holes the direct path already closes.

    And the acquisition has to still be HELD when the caller's body runs.
    Deferring to `_enters_lock` accepted a helper that takes the lock,
    releases it, and hands back something else —

        def helper(cid):
            with locks.campaign_lock(cid):
                pass
            return nullcontext()        # caller's write runs unlocked

    so the `@contextmanager` case is recognized by its `yield` sitting INSIDE
    the `with`, which is what keeps the lock open across the caller's block.
    """
    if any(_is_yield(n) for n in _own_body(fn)):
        return _yields_inside_lock(fn)   # a @contextmanager, judged by its yields

    # Otherwise it must HAND BACK the lock -- and on every path, through
    # `_is_lock_context` so `hold_all([sid])` is validated the same way it is
    # everywhere else. A bare `campaign_lock(cid)` statement is not a return; it
    # constructs a lock and discards it, which locks nothing.
    returns = [n.value for n in _own_body(fn)
               if isinstance(n, ast.Return) and n.value is not None]
    return bool(returns) and all(_is_lock_context(v, fn) for v in returns)


def _holds_this_campaign(call: ast.Call, fn: ast.AST) -> bool:
    """`hold_all(...)` demonstrably covers this function's campaign.

    Unconditional acceptance was wrong for a `cid`-scoped writer:
    `with locks.hold_all([sid])` holds *a* set of campaign locks, not
    necessarily this one, so concurrent writes to `cid` stayed unprotected while
    the guard passed. A function that takes no `cid` is a multi-campaign holder
    (`module_edit._campaign_locks`, the world-module rebind route) and is not
    campaign-scoped, so there is nothing to tie; one that does take `cid` has to
    mention it in the argument. An expression this cannot read fails loud.
    """
    if not _takes_cid(fn):
        return True                      # a multi-campaign holder; nothing to tie
    if not call.args:
        return False
    held = call.args[0]
    # Membership in an arbitrary iterable is not decidable here, and "mentions
    # `cid` somewhere" is not membership: `hold_all([sid] if cid else [other])`
    # names it in the condition and holds it on neither branch. So only a
    # literal container is recognized; anything else fails loud and needs a
    # marker rather than being guessed at.
    if isinstance(held, (ast.List, ast.Tuple, ast.Set)):
        return any(isinstance(e, ast.Name) and e.id == "cid" for e in held.elts)
    return False


def _is_lock_context(expr: ast.expr, fn: ast.AST) -> bool:
    """`expr`, used as a `with` context, holds THIS function's campaign lock.

    The single place that answers the question. It was previously answered in
    two — once on the direct path and once in the decorator check — and the copy
    drifted twice: the decorator path shipped without the `cid` argument check
    the direct path had just gained, and then without the `hold_all` coverage
    check it gained after that. Both were the same bug arriving twice because
    the rule lived in two places.
    """
    if not isinstance(expr, ast.Call):
        return False
    # The call must reach the real `store.locks`. Trusting the trailing name let
    # `def put(cid, campaign_lock=nullcontext): with campaign_lock(cid): ...`
    # pass, and any object with a same-named method would do the same. Every
    # acquisition in this package is written `locks.campaign_lock` or
    # `store.locks.campaign_lock`, so requiring the receiver costs nothing.
    if _receiver_name(expr.func) != "locks":
        return False
    name = _called_name(expr.func)
    if name == "hold_all":
        return _holds_this_campaign(expr, fn)
    return name == "campaign_lock" and _guards_the_campaign(expr)


def _enters_lock(fn: ast.AST, aliases) -> bool:
    """`fn` enters `campaign_lock`/`hold_all` directly, or a local alias of one,
    keyed on this campaign.

    `aliases` is deliberately NOT "every name that serializes". A function can
    hold the lock internally and hand back something else —

        def helper(cid):
            with locks.campaign_lock(cid):
                pass
            return nullcontext()

    which serializes `helper`'s own body and nothing of its caller's. Only a
    name that keeps the acquisition live across the caller's block qualifies,
    which is what `_produces_lock` establishes.
    """
    for call in _with_context_calls(fn):
        if _is_lock_context(call, fn):
            return True
        # An alias is recognized only when the caller's `cid` lands on the
        # parameter the alias actually locks. Checking the two ends separately
        # -- the helper locks *a* parameter named `cid`, the caller passes `cid`
        # first -- accepted `def helper(sid, cid)` called as `helper(cid, sid)`,
        # where binding sends the caller's cid to `sid` and the helper locks the
        # other campaign entirely. `aliases` therefore holds only helpers whose
        # FIRST parameter is the one they lock, and the call must pass `cid`
        # first positionally.
        if _called_name(call.func) in aliases and _guards_the_campaign(call):
            return True
    return False


def _yields_inside_lock(fn: ast.AST) -> bool:
    """A `@contextmanager` whose EVERY `yield` sits inside a campaign lock, so
    the lock is held across the caller's block on every path it can take.

    One yield under the lock is not enough: a helper that yields unlocked on one
    branch hands its caller an unprotected block, and the caller cannot tell.
    """
    return _every_one_locked(fn, fn, _is_yield)


def _under_lock(node: ast.AST, fn: ast.AST, locked: bool, want, aliases=()):
    """Yield, for every node matching `want` that this node executes DIRECTLY,
    whether it runs while a campaign lock is held.

    The one traversal every "is this protected?" question goes through, so the
    two rules that kept being got wrong live in exactly one place.

    It does not descend into nested `def`s or lambdas: code there runs when
    someone calls it, not here, so `later = lambda: fn(cid)` written inside a
    locked block is not a locked invocation. And it reports EVERY match rather
    than stopping at the first, because `locked` differs per branch -- an early
    `return fn(cid)` under a feature flag has to be visible beside the locked
    fallback rather than hidden by it.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue                       # deferred, not executed here
        if isinstance(child, (ast.With, ast.AsyncWith)):
            inner = locked or any(_is_lock_context(i.context_expr, fn)
                                  or _is_alias_context(i.context_expr, aliases)
                                  for i in child.items)
            for item in child.items:       # the context expressions themselves
                yield from _under_lock(item, fn, locked, want, aliases)
            for stmt in child.body:
                yield from _under_lock(stmt, fn, inner, want, aliases)
            continue
        if want(child):
            yield locked
        yield from _under_lock(child, fn, locked, want, aliases)


def _is_alias_context(expr: ast.expr, aliases) -> bool:
    """`with <alias>(cid, ...)` — a module-local helper that hands back this
    campaign's lock. The alias arm of `_enters_lock`, extracted so the traversal
    and the direct check agree on what an acquisition is."""
    return (isinstance(expr, ast.Call) and _called_name(expr.func) in aliases
            and _guards_the_campaign(expr))


def _every_one_locked(node: ast.AST, fn: ast.AST, want, aliases=()) -> bool:
    """`node` does the thing at least once, and EVERY time it does, under a lock.

    "At least one occurrence is locked" was the wrong polarity in four separate
    predicates -- a `@contextmanager` yielding unlocked on one branch, a
    decorator returning either a locked or an unlocked wrapper, a wrapper
    invoking its target on a flagged fast path, an alias returning a lock on one
    branch and something else on another. All four are this function now.
    """
    seen = list(_under_lock(node, fn, False, want, aliases))
    return bool(seen) and all(seen)


def _is_call_to(names: set[str]):
    """Calls to the decorated function, under any local name it is bound to.

    Matched as a bare `ast.Name`, not by trailing attribute: `_called_name`
    would read `hooks.fn(cid)` as an invocation of a target named `fn`, so a
    wrapper could satisfy the lock with an unrelated call of the same spelling.

    Taking a SET rather than the one parameter name is the other half of that.
    Matching only the parameter meant an aliased target matched nothing, and
    "nothing" is the wrong answer in the one shape that matters:

        def wrapper(cid, *a):
            with locks.campaign_lock(cid):
                fn(cid, *a)          # counted, and locked
            target = fn
            target(cid, *a)          # not counted, and not locked

    `_every_one_locked` asks "at least one, and all of them locked", so the
    covered call satisfied it while the uncovered one was invisible. Rebinding
    is followed instead; a target smuggled through a data structure still is
    not, and reads as no invocation at all, which fails closed.
    """
    return lambda n: (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and n.func.id in names)


def _target_aliases(wrapper: ast.AST, target: str) -> set[str]:
    """`target` and every local name assigned from it inside `wrapper`."""
    names = {target}
    bound = list(_bindings(wrapper))
    for _ in range(len(bound) + 1):
        grew = False
        for name, value in bound:
            if isinstance(value, ast.Name) and value.id in names and name not in names:
                names.add(name)
                grew = True
        if not grew:
            break
    return names


def _decorator_name(decorator: ast.expr) -> str | None:
    """The MODULE-LOCAL name a decorator applies, or None for anything else.

    `_called_name` was used here and reads a trailing attribute, so `@hooks.safe`
    resolved to `safe` — and a module that happened to define its own locking
    `safe` decorator vouched for every unrelated `@hooks.safe` in the file. The
    decorated function then read as serialized while nothing locked it.

    An attribute decorator reaches into another module, which this per-module
    analysis cannot follow, so it is not recognized and fails loud instead. A
    decorator *factory* (`@retry(3)`) is let through to `_locks_anywhere`, which
    rejects it on its own terms: its first parameter is the option, not the
    function, so no invocation of the target is ever found.
    """
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    return decorator.id if isinstance(decorator, ast.Name) else None


def _module_level(tree: ast.AST) -> set[str]:
    """Names bound at MODULE scope to a function defined at module scope.

    `_functions` is deliberately flat — every definition of a name, nested ones
    included — because for "does this mutate?" a flat view is the conservative
    one. For "does this lock?" it is the opposite, and it was a false negative:

        def _make_probe():
            def helper(cid):                     # nested, unrelated
                with locks.campaign_lock(cid):
                    yield
            return contextmanager(helper)

        helper = contextlib.nullcontext          # what module scope actually binds

        def put(cid):
            with helper(cid):                    # nullcontext -- locks nothing
                atomic.write_text(p, x)

    The nested `helper` made the name read as a lock alias for the whole module.
    So the names that may vouch for a lock — aliases and decorators — are drawn
    from module scope only, and a module-level assignment to anything this
    cannot resolve back to a module-level function POISONS the name rather than
    being ignored. A lock alias that is genuinely local to one function is no
    longer recognized; that fails loud, which is the trade this guard makes
    everywhere else.
    """
    defined: set[str] = set()
    assigned: list[tuple[str, ast.AST]] = []
    imported: set[str] = set()

    def scan(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)          # its body is a new scope; stop
            elif isinstance(node, ast.ClassDef):
                continue                        # also a new scope
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                assigned.extend(_bindings(node))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # An import binds a name too, and this scan handled only `=`.
                # `def safe(fn): ...` followed by `from hooks import safe` leaves
                # the local definition in the tree for the whitelist to validate
                # while `@safe` decorates with the imported object. Per-module
                # analysis cannot follow the import, so the name is poisoned.
                imported.update((a.asname or a.name).split(".")[0] for a in node.names)
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith,
                                   ast.For, ast.While)):
                for attr in ("body", "orelse", "finalbody", "handlers"):
                    inner = getattr(node, attr, [])
                    scan([h for h in inner] if attr != "handlers"
                         else [s for h in inner for s in h.body])

    scan(tree.body)
    out = set(defined)
    poisoned = set(imported)
    for _ in range(len(assigned) + 1):
        grew = False
        for name, value in assigned:
            if isinstance(value, ast.Name) and value.id in out:
                if name not in out:
                    out.add(name)
                    grew = True
            else:
                poisoned.add(name)
        if not grew:
            break
    # ...and a name any parameter in this module shadows is not a name module
    # scope can vouch for either -- see `_shadowed_names`.
    return out - poisoned - _shadowed_names(tree)


def _binds_the_campaign(call: ast.Call, defs) -> bool:
    """The callee's own `cid` parameter receives THIS function's `cid`.

    Delegation was accepted on the callee's name alone, which checked that the
    helper locks *a* campaign and never that it locks *this* one:

        def _write(cid, sid):            # locks its own `cid`
            with locks.campaign_lock(cid): ...

        def put(cid, sid):
            _write(sid, cid)             # binds `sid` onto the locked parameter

    Two concurrent `put`s for one campaign then serialize under two different
    locks, which loses updates exactly as if there were no lock — the same
    argument-binding hole `_enters_lock` closed for aliases, on the other path.

    A callee with no `cid` parameter used to be waved through as a multi-campaign
    holder, which was true of the shape that motivated it and false in general:

        cid = "some-global"
        def helper():
            with locks.campaign_lock(cid):    # locks a module global
                atomic.write_text(p, x)

        def put(cid):
            helper()                          # a DIFFERENT campaign's lock

    `helper` is serializing, so `_serializing` accepted `put` and `_mutators`
    also stopped propagating at it — `put` was reported by neither, the same
    double silence the argument-swap case produced.

    The first fix for that kept a bypass for `locks.hold_all(...)`, on the
    reasoning that an all-campaign holder covers whatever the caller's campaign
    is. That was the receiver-spelling mistake again, one argument along:
    `hold_all([])` and `hold_all([sid])` are also `hold_all`, and neither holds
    the caller's campaign, so the bypass restored exactly the double silence it
    had just closed. There is no syntactic form that proves an argument
    enumerates every campaign, so the bypass is gone rather than narrowed — a
    callee with no `cid` parameter can never establish coverage of the caller's,
    and says so by failing loud.

    (A function with no `cid` is still recognized as serializing its OWN body
    via `_holds_this_campaign`; that is a different question, and `_analyze`
    ignores such functions anyway because they are not campaign-scoped.)

    Anything this cannot index — a starred argument, a position the call does
    not reach — fails loud too.
    """
    starred = any(isinstance(a, ast.Starred) for a in call.args)
    for fn in defs:
        params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
        if "cid" not in params:
            return False
        passed = [k.value for k in call.keywords if k.arg == "cid"]
        if passed:
            if not all(isinstance(v, ast.Name) and v.id == "cid" for v in passed):
                return False
            continue
        index = params.index("cid")
        if starred or index >= len(call.args):
            return False
        arg = call.args[index]
        if not (isinstance(arg, ast.Name) and arg.id == "cid"):
            return False
    return True


def _is_yield(node: ast.AST) -> bool:
    return isinstance(node, (ast.Yield, ast.YieldFrom))


def _wraps_target_under_lock(fn: ast.AST) -> bool:
    """`fn` is a decorator whose returned wrapper calls the decorated function,
    and does so under this campaign's lock on EVERY path it executes.

    Descending into nested defs is unavoidable here -- a decorator's acquisition
    lives in the closure it returns -- which is exactly why this needs to be
    precise about three separate things, each of which was a hole in turn:

    - it must be the wrapper the decorator RETURNS, not any closure it happens
      to define (a `safe` closure that locks, never used, beside an `unsafe` one
      that is);
    - the invocation must be *executed* inside the `with`, not merely written
      there (a lambda defined in the block and called after it);
    - and EVERY executed invocation must be covered, not one of them, so a
      wrapper that returns `fn(cid)` early under a flag and locks only on the
      fallback does not read as protective.

    The decorated function is identified by the decorator's own first parameter,
    which is what `@_serialized`'s `fn` is.

    A fourth thing, and the same hole as everywhere else: the invocation has to
    BIND this campaign. The wrapper's lock expression was validated while the
    arguments handed to the target were not, so

        def _serialized(fn):
            def locked(sid, cid, *a):
                with locks.campaign_lock(cid):
                    return fn(sid, cid, *a)   # the target's `cid` gets `sid`
            return locked

    read as protective while two decorated calls for one campaign ran under two
    different locks. The target must receive `cid` first positionally (or as the
    keyword `cid`), which is the same positional-binding rule aliases and
    delegation already use, and which the real `_serialized` satisfies —
    `return fn(cid, *args, **kwargs)`.
    """
    params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
    if not params:
        return False
    target = params[0]

    # EVERY name the decorator can return, and every definition of each. A
    # decorator that returns a locked wrapper on one branch and an unlocked one
    # on another was accepted as soon as the locked one was inspected; if the
    # flag selects the other, the decorated writer runs unlocked.
    returns = [node.value for node in _own_body(fn)
               if isinstance(node, ast.Return) and node.value is not None]
    if not returns or not all(isinstance(v, ast.Name) for v in returns):
        return False                     # `return fn` unchanged, or a call/expr
    returned = {v.id for v in returns}

    wrappers: dict[str, list[ast.AST]] = {}
    for nested in ast.walk(fn):
        if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and nested is not fn and nested.name in returned:
            wrappers.setdefault(nested.name, []).append(nested)

    if set(wrappers) != returned:
        return False                     # a returned name this cannot resolve
    for w in [w for defs in wrappers.values() for w in defs]:
        is_target = _is_call_to(_target_aliases(w, target))
        # Checked over every syntactic invocation, not only the ones
        # `_every_one_locked` walks: a mis-bound call this cannot reach is not
        # a call this may assume is bound correctly.
        if not all(_guards_the_campaign(c) or _passes_cid_by_keyword(c)
                   for c in ast.walk(w) if is_target(c)):
            return False
        if not _every_one_locked(w, w, is_target):
            return False
    return True


def _passes_cid_by_keyword(call: ast.Call) -> bool:
    """`f(cid=cid)` — the other spelling `_guards_the_campaign` does not read."""
    return any(k.arg == "cid" and isinstance(k.value, ast.Name)
               and k.value.id == "cid" for k in call.keywords)


def _locks_anywhere(fn: ast.AST) -> bool:
    """Whether applying `fn` as a decorator serializes what it decorates.

    Only the returned wrapper counts. A lock in the decorator's OWN body is held
    while the decoration runs -- once, at import -- and is long released by the
    time the decorated function is ever called, so a lock entered in the decorator's own body was
    never evidence of anything here.
    """
    return _wraps_target_under_lock(fn)


def _with_context_names(fn: ast.AST) -> set[str]:
    """Names called as context managers anywhere in `fn`: the `f` of `with f(x)`.

    Entering a locking context manager is not the same as delegating to a locked
    function, and the difference decides whether the caller's OWN writes are
    covered. `with _lock(cid): atomic.write_text(...)` serializes that write;
    `_locked_helper(cid); atomic.write_text(...)` does not serialize anything.
    """
    return {name for name in map(lambda c: _called_name(c.func),
                                 _with_context_calls(fn)) if name is not None}


def _with_context_calls(fn: ast.AST):
    """The `f(...)` of every `with f(...)` in `fn`'s own body.

    Own body only: a `with` inside a nested def covers that def, not this one.
    """
    for node in _own_body(fn):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call):
                    yield item.context_expr


def _own_calls(fn: ast.AST):
    """Calls `fn` makes itself. Delegation is only delegation when the caller
    actually makes the call; one buried in a nested def is that def's."""
    for node in _own_body(fn):
        if isinstance(node, ast.Call):
            yield node


def _reaches_a_write(funcs: dict[str, ast.AST], surface=_Surface()) -> set[str]:
    """Names that reach a write through module-local calls, ignoring locks.

    Deliberately lock-blind, unlike `_mutators`: it answers "is there a write
    down this path at all", which is what the delegation rule needs before
    `_serializing` has decided anything. Computing it the other way round would
    be circular.
    """
    out = {n for n, ds in funcs.items() if _any(ds, lambda d: _writes_directly(d, surface))}
    for _ in range(len(funcs) + 1):
        grown = set(out)
        for name, defs in funcs.items():
            if name not in grown and _any(defs, lambda d: any(
                    _called_name(c.func) in grown for c in _own_calls(d))):
                grown.add(name)
        if grown == out:
            break
        out = grown
    return out


def _innermost_decorator_locks(fn: ast.AST, decorating: set[str]) -> bool:
    """A recognized locking decorator is applied DIRECTLY to `fn`.

    "Any decorator in the chain locks" ignored composition order, and order is
    what decides whether the write happens inside the lock:

        @safe            # locks, and calls what it wraps
        @defer           # returns a callback instead of running the body
        def put(cid): atomic.write_text(p, x)

    `defer` is applied first, so `safe` locks around `defer(put)` — which hands
    back a callback and writes nothing. The lock is released, then the callback
    runs the write. `put` read as serialized while nothing serialized it.

    The innermost decorator is the one applied to the function itself, so if it
    locks, the write is inside the lock. Anything further out then wraps an
    already-serialized callable and cannot un-serialize it — it may defer or
    skip the whole locked unit, but never split the lock from the write. Any
    other arrangement is a chain this cannot resolve and fails loud.

    No function in this package has more than one decorator today, so this costs
    nothing now and is here for the chain that gets written later.

    Two things about the DECORATED function also have to hold, and neither was
    checked -- the wrapper was validated in isolation, as if what it wraps could
    not matter:

    - **Its body must run inside the call.** A synchronous wrapper doing
      `with lock: return fn(cid, *a)` around an `async def` or a generator only
      *constructs* the coroutine or generator; the writes happen later, when
      somebody awaits or iterates it, with the lock long released. A wrapper
      that awaits or `yield from`s under the lock would be fine, and is not
      recognized -- it fails loud and needs a marker. Nothing in this package
      applies a locking decorator to a deferred body today.
    - **Its `cid` must be its first parameter.** The wrapper locks its own first
      argument and passes arguments through positionally, so
      `@_serialized def put(sid, cid)` locks the scene id while the campaign id
      lands on the target's second slot. This is the same positional-binding
      rule `_locks_its_first_param` applies to aliases, at the fifth boundary an
      id crosses; every `@_serialized` mutator in `scenes` already satisfies it.
    """
    if not (bool(fn.decorator_list) and _decorator_name(fn.decorator_list[-1]) in decorating):
        return False
    if isinstance(fn, ast.AsyncFunctionDef) or any(_is_yield(n) for n in _own_body(fn)):
        return False                     # a deferred body; the wrapper's lock is
                                         # released before the writes happen
    params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
    return not _takes_cid(fn) or params[:1] == ["cid"]


def _serializing(funcs: dict[str, ast.AST], surface=_Surface(),
                 module_level: set[str] | None = None) -> set[str]:
    """Names whose bodies establish the campaign lock.

    Three forms, all present in the package today: a direct
    ``with locks.campaign_lock(cid)``; a module-local alias like
    ``audit._lock``; and a decorator that wraps the body in one, which is how
    every ``scenes`` mutator does it (``@_serialized``).

    Delegation counts too -- ``scenes.create_scene`` does its work in the
    ``@_serialized`` ``_create_scene`` -- but only when EVERY write the wrapper
    reaches is covered, and only when the caller's ``cid`` lands on the
    parameter the callee locks. Three ways that fails: the wrapper publishes
    something itself, it calls a second unlocked helper alongside the locked
    one, or it passes a different id into the locked position.

    ``module_level`` is the set of names allowed to vouch for a lock -- see
    `_module_level`. ``None`` means "every name", which is only for callers
    that have no tree to draw it from; every real caller passes it.
    """
    if not surface.trusted_locks:
        # Something here binds the name `locks`, so no `locks.campaign_lock(...)`
        # in this module is known to reach the real one. Nothing in it may claim
        # to serialize -- see `_rebinds_locks`.
        return set()
    scope = set(funcs) if module_level is None else module_level
    # A decorator's acquisition is nested inside the wrapper it returns, so it is
    # the one case that must look through nested defs -- see `_locks_anywhere`.
    decorating = {n for n, ds in funcs.items()
                  if n in scope and _all(ds, _locks_anywhere)}
    # Local helpers that hand back a campaign lock. Entering one of these IS an
    # acquisition, even though the helper itself never enters anything.
    aliases = {n for n, ds in funcs.items()
               if n in scope and _all(ds, _produces_lock)
               and _all(ds, _locks_its_first_param)}
    writing = _reaches_a_write(funcs, surface)
    # No seed: the fixed point below decides every name by one rule. The seed
    # used to answer "does this body lock?" with a bare `_enters_lock`, which was
    # a second, weaker rule for the same question -- and weaker in the way that
    # mattered, since it asked only whether a lock is entered somewhere.
    out: set[str] = set()
    for _ in range(len(funcs) + 1):
        grown = set(out)
        for name, defs in funcs.items():
            if name in grown:
                continue

            def covered(fn, grown=grown, surface=surface):
                if _innermost_decorator_locks(fn, decorating):
                    return True                       # @_serialized and friends
                if _writes_directly(fn, surface):
                    # Checked BEFORE `_enters_lock`, which was the bug: entering
                    # a lock anywhere made the whole function read as serialized,
                    # so `with locks.campaign_lock(cid): pass` followed by
                    # `atomic.write_text(...)` passed while writing unlocked.
                    # Where the writes run is the question, and the traversal
                    # that answers it for yields and decorator targets simply
                    # was not asked here.
                    return _every_one_locked(
                        fn, fn, lambda n: isinstance(n, ast.Call)
                        and _is_write_call(n, surface), aliases)
                if _enters_lock(fn, aliases):
                    return True                       # with _lock(cid): ...
                # Pure delegation: every local callee that reaches a write must
                # itself be covered, on this campaign, and at least one must exist.
                delegated = [c for c in _own_calls(fn)
                             if _called_name(c.func) in writing]
                return bool(delegated) and all(
                    _called_name(c.func) in grown
                    and _binds_the_campaign(c, funcs[_called_name(c.func)])
                    for c in delegated)

            if _all(defs, covered):
                grown.add(name)
        if grown == out:
            break
        out = grown
    return out


def _mutators(funcs: dict[str, ast.AST], serializing: set[str],
              surface=_Surface()) -> set[str]:
    """Names that publish a change to campaign state.

    Propagation stops at a serializing callee: it is an atomic unit, so calling
    it does not make the caller a mutation site in its own right. Without that,
    every thin wrapper over a locked helper reads as an unlocked mutator and the
    guard drowns in false positives.

    It stops only for a callee that locks THIS campaign, though. Stopping on
    membership alone was the more dangerous half of the argument-binding hole:
    `_write(sid, cid)` against a helper that locks its own first parameter is
    not covered by `_serializing` any more, but the caller also stopped being a
    mutator, so it was reported by neither -- quieter than if the helper had
    taken no lock at all.
    """
    def atomic_unit(call: ast.Call) -> bool:
        name = _called_name(call.func)
        return name in serializing and _binds_the_campaign(call, funcs.get(name, []))

    out = {n for n, ds in funcs.items() if _any(ds, lambda d: _writes_directly(d, surface))}
    for _ in range(len(funcs) + 1):
        grown = set(out)
        for name, defs in funcs.items():
            if name in grown:
                continue
            if _any(defs, lambda d: any(
                    _called_name(c.func) in grown and not atomic_unit(c)
                    for c in _calls(d))):
                grown.add(name)
        if grown == out:
            break
        out = grown
    return out


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(PACKAGE).with_suffix("")
    return ".".join(rel.parts)


def _exemption(src: str, fn: ast.AST, others=()) -> str | None:
    """The `# lock-domain-ok: <reason>` attached to THIS function, if any.

    Two things `guard_markers.marker_reason(MARKER, src, fn)` alone gets wrong
    for a *function* node, as opposed to the call nodes the other guards pass it:

    - ``ast.FunctionDef.lineno`` is the ``def`` line, not the first decorator's,
      so the comment block above a decorated function does not attach and the
      marker is silently ignored. Every ``scenes`` mutator is decorated, which is
      the one domain module where that matters most. Retry from the decorator.
    - a function's span covers its whole body, so a marker written inside a
      NESTED function exempts the enclosing one too. Passing the module's other
      functions as `others` hands the marker to the innermost that contains it,
      which is what that parameter is for.
    """
    reason = guard_markers.marker_reason(MARKER, src, fn, others)
    if reason is not None:
        return reason
    for decorator in getattr(fn, "decorator_list", []):
        reason = guard_markers.marker_reason(MARKER, src, decorator, others)
        if reason is not None:
            return reason
    return None


def _analyze(path: pathlib.Path):
    """(unserialized mutator names, every campaign mutator name) for one file."""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = _functions(tree)
    surface = _Surface.of(tree)
    serializing = _serializing(funcs, surface, _module_level(tree))
    mutators = _mutators(funcs, serializing, surface)

    every = [d for ds in funcs.values() for d in ds]
    campaign_mutators, unserialized = set(), set()
    for name in mutators:
        defs = funcs[name]
        if not _any(defs, _takes_cid):
            continue
        if name.startswith("_"):
            # A private helper is not its own domain member: it runs under
            # whatever its callers hold, which is how `rolls._write`,
            # `proposals._write` and `sheets._set_field_locked` are written.
            # This is not a hole, because propagation already charged the
            # helper's mutation to every unlocked *public* caller in the module
            # -- so forgetting the lock still fails, it just fails at the
            # caller. What it does miss is a private helper reached from
            # another module through an unlocked path.
            continue
        campaign_mutators.add(name)
        if name in serializing:
            continue
        # EVERY definition must carry its own exemption; one marker cannot
        # clear a name whose other branch is the unlocked one.
        if _all(defs, lambda d: _exemption(src, d, [o for o in every if o is not d])
                is not None):
            continue
        unserialized.add(name)
    return unserialized, campaign_mutators


@functools.cache
def _survey() -> dict[str, tuple[set[str], set[str]]]:
    """Every campaign-mutating module -> (unserialized public mutators, all of
    them). Cached: six tests below ask for it, the package is ~150 files, and
    the per-module fixed points are quadratic in the module's function count.
    Nothing rewrites the tree mid-run."""
    out = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        unserialized, mutators = _analyze(path)
        if mutators:
            out[_module_name(path)] = (unserialized, mutators)
    return out


def _declared() -> set[str]:
    return set(locks.DOMAIN_MODULES) | set(locks.OUTSIDE_DOMAIN) | set(locks.UNREVIEWED)


def test_every_campaign_mutating_module_is_classified():
    """The #255 check: a module can no longer mutate campaign state without
    somebody deciding, in code, whether it belongs in the exclusion.

    Before #255 ``rolls`` ran a private lock registry and was outside the shared
    domain. Nothing failed, because the domain was a sentence. This is the test
    that fails."""
    unclassified = sorted(set(_survey()) - _declared())
    assert not unclassified, (
        "module(s) mutating campaign state that the lock domain does not "
        "classify — add each to `locks.DOMAIN_MODULES` (its public mutators "
        "take `campaign_lock`) or to `locks.OUTSIDE_DOMAIN` with the reason "
        "they deliberately do not. `locks.UNREVIEWED` is a frozen backlog and "
        "is not open for new entries:\n  " + "\n  ".join(unclassified))


# The backlog exactly as it stood when this guard landed. Frozen HERE, not
# derived from `locks.UNREVIEWED`, because that is the whole point: the
# declaration is the thing under test and cannot also be the baseline.
_UNREVIEWED_AT_LANDING = frozenset({
    "store.appearances", "store.assets", "store.campaign_climate",
    "store.changes", "store.characters", "store.chronicle", "store.commits",
    "store.dossiers", "store.modules", "store.overlay", "store.playing",
    "store.playstate", "store.plot", "store.relationships", "store.sync",
    "store.taglines", "store.weather.overrides",
})


def test_the_unreviewed_backlog_only_shrinks():
    """A third bucket is a temptation: it takes an entry with no reason. It is
    allowed to exist only because it cannot grow — every module in it predates
    this guard, and a new mutator has to be classified for real.

    Membership, not cardinality. A count-only bound let one legacy entry be
    swapped for a brand-new module in the same change: the length stays put, the
    classification and phantom checks are satisfied, and an unreviewed mutator
    joins the backlog without anyone reviewing it. Names are checked because
    names are what the guarantee is about."""
    added = sorted(set(locks.UNREVIEWED) - _UNREVIEWED_AT_LANDING)
    assert not added, (
        "`locks.UNREVIEWED` is a frozen backlog of modules that predate this "
        "guard; it may only shrink. Classify these into DOMAIN_MODULES or "
        "OUTSIDE_DOMAIN instead:\n  " + "\n  ".join(added))


def test_no_module_is_declared_twice():
    counts = [len(locks.DOMAIN_MODULES), len(locks.OUTSIDE_DOMAIN), len(locks.UNREVIEWED)]
    assert sum(counts) == len(_declared()), (
        "a module appears in more than one of DOMAIN_MODULES / OUTSIDE_DOMAIN "
        "/ UNREVIEWED; the three are meant to partition the mutating modules")


def test_the_declaration_has_no_phantom_modules():
    """A declared module that no longer mutates campaign state (renamed,
    deleted, refactored) is stale documentation with a test guarding it, which
    is worse than none."""
    surveyed = set(_survey())
    phantom = sorted(_declared() - surveyed)
    assert not phantom, (
        "declared in the lock domain but no longer a campaign-mutating module "
        "— drop the entry:\n  " + "\n  ".join(phantom))


def test_every_mutator_in_a_domain_module_serializes():
    """The #254 check: a new ``scenes`` mutator that forgets ``@_serialized`` is
    a silent lost update on a transcript, the one artifact here that cannot be
    regenerated."""
    survey = _survey()
    offenders = []
    for module in sorted(locks.DOMAIN_MODULES):
        unserialized, _ = survey.get(module, (set(), set()))
        offenders += [f"{module}.{name}" for name in sorted(unserialized)]
    assert not offenders, (
        "campaign-scoped mutator(s) in a lock-domain module that do not take "
        "`locks.campaign_lock(cid)` — take it, or annotate the function with "
        f"`# {MARKER} <why this one is safe>`:\n  " + "\n  ".join(offenders))


def test_modules_declared_outside_are_really_outside():
    """A declaration that has quietly become true again is worse than none: it
    reads as a known hole long after the hole was filled, and the next reader
    trusts it. If a module's mutators all serialize now, it belongs in
    ``DOMAIN_MODULES``."""
    survey = _survey()
    stale = []
    for module in sorted(set(locks.OUTSIDE_DOMAIN) | set(locks.UNREVIEWED)):
        unserialized, _ = survey.get(module, (set(), set()))
        if not unserialized:
            stale.append(f"{module}: every public mutator serializes now — promote it")
    assert not stale, (
        "lock-domain entries that no longer describe the code:\n  "
        + "\n  ".join(stale))


def test_every_outside_entry_states_a_reason():
    """The same rule the atomic guard applies to its marker: an exemption with
    no reason is a rubber stamp, and a growing pile of them means the domain is
    being routed around rather than applied."""
    thin = sorted(m for m, why in locks.OUTSIDE_DOMAIN.items() if len(why) < 30)
    assert not thin, f"`OUTSIDE_DOMAIN` entries with no real reason: {thin}"


def test_the_marker_is_not_a_rubber_stamp():
    marked = []
    for path in sorted(PACKAGE.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        funcs = _functions(ast.parse(src))
        every = [d for ds in funcs.values() for d in ds]
        for name, defs in funcs.items():
            for fn in defs:
                reason = _exemption(src, fn, [o for o in every if o is not fn])
                if reason is not None:
                    marked.append((f"{_module_name(path)}.{name}", reason))
    unexplained = [loc for loc, reason in marked if len(reason) < 15]
    assert not unexplained, f"`{MARKER}` with no real reason: {unexplained}"
    assert len(marked) <= 2, (
        f"{len(marked)} {MARKER} exemptions; each is a hole in the exclusion, "
        f"so they need review rather than a raised limit: {marked}")


# --- the guard can fail ------------------------------------------------------
# "A guard that cannot fail is worse than none -- it reads as coverage."
# (test_atomic_guard.py)

def _probe(src: str):
    tree = ast.parse(src)
    funcs = _functions(tree)
    surface = _Surface.of(tree)
    serializing = _serializing(funcs, surface, _module_level(tree))
    return funcs, serializing, _mutators(funcs, serializing, surface)


def test_the_guard_detects_an_unlocked_mutator():
    src = ("def touch(cid):\n"
           "    meta = read(p)\n"
           "    atomic.write_text(p, meta)\n")
    funcs, serializing, mutators = _probe(src)
    assert mutators == {"touch"}
    assert "touch" not in serializing
    assert _takes_cid(funcs["touch"][0])


def test_the_guard_accepts_a_directly_locked_mutator():
    src = ("def append(cid):\n"
           "    with locks.campaign_lock(cid):\n"
           "        atomic.write_text(p, x)\n")
    _funcs, serializing, mutators = _probe(src)
    assert mutators == {"append"} and "append" in serializing


def test_a_decorator_that_locks_covers_what_it_wraps():
    """`scenes` serializes its whole mutator surface this way, so a guard that
    missed decorators would flag every one of them."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _funcs, serializing, _mutators_ = _probe(src)
    assert "append_message" in serializing


def test_a_module_local_lock_alias_counts():
    """`audit` takes the campaign lock through its own `_lock` helper."""
    src = ("import contextlib\n"
           "@contextlib.contextmanager\n"
           "def _lock(cid):\n"
           "    with locks.campaign_lock(cid):\n"
           "        yield\n"
           "def clear_baselines(cid):\n"
           "    with _lock(cid):\n"
           "        atomic.write_text(p, '{}')\n")
    _funcs, serializing, _m = _probe(src)
    assert "clear_baselines" in serializing


def test_a_context_manager_that_locks_covers_the_bodys_own_writes():
    """The distinction `_with_context_names` exists for. `with _lock(cid):` puts
    the caller's own write under the lock; merely *calling* a locked helper
    beside an unlocked write does not (the test below)."""
    src = ("def _lock(cid):\n"
           "    return locks.campaign_lock(cid)\n"
           "def clear(cid):\n"
           "    with _lock(cid):\n"
           "        atomic.write_text(p, x)\n")
    _funcs, serializing, _m = _probe(src)
    assert "clear" in serializing


def test_a_lock_that_is_never_entered_does_not_count():
    """`_ProcessScopedLock` acquires in `__enter__`. A bare call constructs the
    lock object and locks nothing, so reading any mention of the name as an
    acquisition let a completely unlocked mutator pass."""
    src = ("def append(cid):\n"
           "    locks.campaign_lock(cid)\n"
           "    atomic.write_text(p, x)\n")
    _funcs, serializing, mutators = _probe(src)
    assert mutators == {"append"}
    assert "append" not in serializing, \
        "a lock that was constructed but never entered read as serialization"


def test_entering_the_lock_for_a_different_id_does_not_count():
    """Two concurrent calls for one `cid` serialized under two different locks
    lose an update exactly as if there were no lock at all."""
    wrong = ("def set_datetime(cid, sid):\n"
             "    with locks.campaign_lock(sid):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(wrong)
    assert "set_datetime" not in serializing, "the wrong campaign's lock passed"

    right = ("def set_datetime(cid, sid):\n"
             "    with locks.campaign_lock(cid):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(right)
    assert "set_datetime" in serializing


def test_hold_all_qualifies_without_a_single_cid():
    """The multi-campaign form takes an iterable, so there is no one argument to
    tie to the caller — it must not be caught by the cid check."""
    src = ("def rebind(all_cids):\n"
           "    with locks.hold_all(all_cids):\n"
           "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "rebind" in serializing


def test_creating_an_empty_file_counts_as_a_mutation():
    """`Path.touch()` publishes a pathname others can observe while writing no
    bytes, so neither this guard's writers nor the atomic guard would see it."""
    _f, _s, mutators = _probe("def mark(cid):\n    (root / 'done').touch()\n")
    assert mutators, "Path.touch() was not seen as publishing state"


def test_this_repos_own_touch_function_is_not_a_filesystem_call():
    """`campaigns.touch(cid)` is a module function, not `Path.touch`. Matching
    the bare name would have made every caller of it a direct writer."""
    _f, _s, mutators = _probe("def bump(cid):\n    campaigns.touch(cid)\n")
    assert not mutators, "campaigns.touch(cid) was read as a filesystem call"


def test_hold_all_membership_must_be_visible_not_merely_mentioned():
    """"Mentions `cid` somewhere in the expression" is not membership:
    `hold_all([sid] if cid else [other])` names it in the condition and holds it
    on neither branch. Only a literal container is recognized."""
    for src, why in [
        ("def put(cid, sid):\n    with locks.hold_all([sid] if cid else [x]):\n"
         "        atomic.write_text(p, y)\n", "conditional, holds neither"),
        ("def put(cid):\n    with locks.hold_all(ids_for(cid)):\n"
         "        atomic.write_text(p, y)\n", "computed iterable"),
    ]:
        _f, serializing, _m = _probe(src)
        assert "put" not in serializing, f"accepted without proving membership: {why}"

    ok = ("def put(cid, sid):\n    with locks.hold_all([cid, sid]):\n"
          "        atomic.write_text(p, y)\n")
    _f, serializing, _m = _probe(ok)
    assert "put" in serializing, "a literal container holding cid regressed"


def test_an_alias_must_lock_the_parameter_the_caller_passes_cid_into():
    """Checking the two ends separately accepted `def helper(sid, cid)` called
    as `helper(cid, sid)`: the helper locks a parameter spelled `cid`, the
    caller passes one spelled `cid`, and binding sends them to opposite
    campaigns."""
    crossed = ("def helper(sid, cid):\n"
               "    return locks.campaign_lock(cid)\n"
               "def put(cid, sid):\n"
               "    with helper(cid, sid):\n"
               "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(crossed)
    assert "put" not in serializing, "argument binding crossed the campaigns"

    straight = ("def helper(cid):\n"
                "    return locks.campaign_lock(cid)\n"
                "def put(cid):\n"
                "    with helper(cid):\n"
                "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(straight)
    assert "put" in serializing, "the ordinary alias shape regressed"


def test_a_shadowed_campaign_lock_is_not_the_store_lock():
    """The name is not the thing. A local binding, or any object with a
    same-named method, spelled its way past a check that trusted the trailing
    name."""
    for src in ("def put(cid, campaign_lock=nullcontext):\n"
                "    with campaign_lock(cid):\n        atomic.write_text(p, x)\n",
                "def put(cid):\n    with self.campaign_lock(cid):\n"
                "        atomic.write_text(p, x)\n"):
        _f, serializing, _m = _probe(src)
        assert "put" not in serializing, f"a shadowed name passed as the lock: {src!r}"

    real = ("def put(cid):\n    with locks.campaign_lock(cid):\n"
            "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(real)
    assert "put" in serializing


def test_directory_creation_is_a_mutation():
    """A directory is a namespace entry others can observe, and neither guard
    looked for one."""
    _f, _s, mutators = _probe("def init(cid):\n    (root / 'done').mkdir()\n")
    assert mutators, "Path.mkdir() was invisible"


def test_a_raw_write_the_atomic_guard_forgives_is_still_a_mutation():
    """`# atomic-ok:` lets a raw write past that guard -- two exist today -- and
    matching only an `atomic` receiver meant this one never saw them. A write
    the other guard forgives is still a write."""
    for src in ("def put(cid):\n    p.write_text('x', encoding='utf-8')\n",
                "def put(cid):\n    p.write_bytes(b'x')\n",
                "def put(cid):\n    open(p, 'w').write('x')\n",
                "def put(cid):\n    p.open('wb').write(b'x')\n"):
        _f, _s, mutators = _probe(src)
        assert mutators, f"a raw write was invisible: {src!r}"

    for src in ("def read_it(cid):\n    return p.open('r').read()\n",
                "def read_it(cid):\n    return open(p).read()\n"):
        _f, _s, mutators = _probe(src)
        assert not mutators, f"a read was mistaken for a write: {src!r}"


def test_a_public_alias_of_a_private_mutator_is_analyzed():
    """Private helpers are skipped as domain members because they run under
    their callers' locks — but `put = _put` makes the private one callable under
    a public name, and nothing was looking at that name."""
    src = ("def _put(cid):\n"
           "    atomic.write_text(p, x)\n"
           "put = _put\n")
    funcs, serializing, mutators = _probe(src)
    assert "put" in funcs, "the alias was never collected"
    assert "put" in mutators and "put" not in serializing

    chained = ("def _put(cid):\n"
               "    atomic.write_text(p, x)\n"
               "mid = _put\n"
               "put = mid\n")
    funcs, _s, mutators = _probe(chained)
    assert "put" in mutators, "an alias chain was not followed"


def test_os_level_writes_are_mutations():
    """`os.write` / `os.fdopen` bypass `open()` entirely. `test_atomic_guard`
    matches both; this did not, so an os-level write — especially one that guard
    has already forgiven with a marker — was invisible."""
    for src in ("def put(cid):\n    os.write(fd, b'x')\n",
                "def put(cid):\n    os.fdopen(fd, 'wb')\n"):
        _f, _s, mutators = _probe(src)
        assert mutators, f"an os-level write was invisible: {src!r}"


def test_the_filesystem_namespace_is_a_closed_whitelist_not_an_enumeration():
    """The mutation surface is inverted, like lock recognition already is.

    Six rounds each added one more publication primitive to a list, which is
    the shape of a rule that cannot be finished by inspection. Inside `os` and
    `shutil` the question is now "is this a known reader?", so a primitive
    nobody enumerated is a mutation by default. `os.open` — a create-and-
    truncate that never calls `os.write` because it hands the fd to something
    else — is the one that made the point; `shutil.copytree` is used in this
    package today and no enumeration had it either.
    """
    for src, why in [
        ("def put(cid):\n    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)\n",
         "os.open creates and truncates"),
        ("def put(cid):\n    os.truncate(p, 0)\n", "os.truncate"),
        ("def put(cid):\n    os.symlink(a, b)\n", "os.symlink"),
        ("def put(cid):\n    os.makedirs(p)\n", "os.makedirs"),
        ("def put(cid):\n    shutil.copytree(a, b)\n", "shutil.copytree"),
        ("def put(cid):\n    shutil.move(a, b)\n", "shutil.move"),
        ("from os import truncate\ndef put(cid):\n    truncate(p, 0)\n",
         "imported out of the namespace"),
    ]:
        _f, _s, mutators = _probe(src)
        assert mutators, f"{why} was invisible ({src!r})"


def test_reading_the_filesystem_is_not_publishing_it():
    """The other half of the inversion: it has to keep a reader quiet, or every
    module that calls `os.stat` lands in the declaration. `os.path` is left to
    the enumeration on purpose — its receiver reads as `path`, which names no
    namespace here, and it publishes nothing anyway."""
    for src, why in [
        ("def read(cid):\n    return os.stat(p).st_mtime\n", "os.stat"),
        ("def read(cid):\n    return sorted(os.listdir(p))\n", "os.listdir"),
        ("def read(cid):\n    return os.path.join(root, cid)\n", "os.path.join"),
        ("def read(cid):\n    return os.path.exists(p)\n", "os.path.exists"),
        ("def read(cid):\n    return shutil.disk_usage(p).free\n", "shutil.disk_usage"),
    ]:
        _f, _s, mutators = _probe(src)
        assert not mutators, f"{why} was read as a publication ({src!r})"


def test_a_rebound_target_is_still_the_target():
    """A wrapper that invokes the decorated function twice — once under the
    lock, once through a local rebinding — is not protective.

    `_every_one_locked` asks "at least one, and every one locked". Matching only
    the parameter name meant the rebound call was not an invocation at all, so
    the locked one answered for both and the decorator read as serializing."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            fn(cid, *a)\n"
           "        target = fn\n"
           "        return target(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def put(cid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "put" not in serializing, "an unlocked call through a rebound target was invisible"

    # ...and the same decorator with only the covered call still qualifies, so
    # the rule is about the uncovered invocation, not about mentioning `target`.
    ok = src.replace("        target = fn\n        return target(cid, *a)\n", "")
    _f, serializing, _m = _probe(ok)
    assert "put" in serializing


def test_a_decorator_from_another_module_is_not_this_modules_decorator():
    """`@hooks.safe` is not the local `safe`. Resolving a decorator by its
    trailing attribute let any module that defined its own locking decorator
    vouch for every same-spelled attribute decorator in the file."""
    src = ("def safe(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@hooks.safe\n"
           "def put(cid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(src)
    assert "put" in mutators
    assert "put" not in serializing, "an imported decorator borrowed a local name's lock"

    # The local one, applied locally, still counts -- this narrows the rule to
    # attribute decorators rather than breaking decorator recognition.
    _f, serializing, _m = _probe(src.replace("@hooks.safe", "@safe"))
    assert "put" in serializing


def test_only_module_scope_can_vouch_for_a_lock():
    """A nested `def helper` in an unrelated factory is not the module-level
    `helper`, and `_functions` is flat, so the nested one vouched for the name
    everywhere. Module scope binds `helper` to something that locks nothing."""
    src = ("def _make_probe():\n"
           "    def helper(cid):\n"
           "        with locks.campaign_lock(cid):\n"
           "            yield\n"
           "    return contextmanager(helper)\n"
           "helper = contextlib.nullcontext\n"
           "def put(cid):\n"
           "    with helper(cid):\n"
           "        atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(src)
    assert "put" in mutators
    assert "put" not in serializing, "a nested definition vouched for a module-level name"

    # Drop the shadowing assignment and promote the helper to module scope and
    # it is a real alias again, so the rule is about scope, not about the shape.
    real = ("from contextlib import contextmanager\n"
            "@contextmanager\n"
            "def helper(cid):\n"
            "    with locks.campaign_lock(cid):\n"
            "        yield\n"
            "def put(cid):\n"
            "    with helper(cid):\n"
            "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(real)
    assert "put" in serializing


def test_delegation_must_bind_this_campaign_to_the_locked_parameter():
    """Delegating to a locked helper was accepted on the helper's name alone.
    `_write(sid, cid)` binds the caller's `sid` onto the parameter `_write`
    locks, so two concurrent calls for one campaign serialize under two
    different locks — the same hole `_enters_lock` closed for aliases."""
    helper = ("def _write(cid, sid):\n"
              "    with locks.campaign_lock(cid):\n"
              "        atomic.write_text(p, x)\n")
    swapped = helper + ("def put(cid, sid):\n"
                        "    _write(sid, cid)\n")
    _f, serializing, mutators = _probe(swapped)
    assert "put" in mutators
    assert "put" not in serializing, "delegation bound the wrong id to the locked parameter"

    for ok, why in [(helper + "def put(cid, sid):\n    _write(cid, sid)\n", "positional"),
                    (helper + "def put(cid, sid):\n    _write(cid=cid, sid=sid)\n", "keyword")]:
        _f, serializing, _m = _probe(ok)
        assert "put" in serializing, f"correct {why} binding was rejected"


def test_a_decorator_must_bind_this_campaign_to_its_target():
    """The wrapper's lock expression was validated; the arguments it hands the
    decorated function were not. `fn(sid, cid)` sends the caller's `sid` to the
    target's first parameter, so two decorated calls for one campaign run under
    two different locks — the delegation hole, on the decorator path."""
    src = ("def _serialized(fn):\n"
           "    def locked(sid, cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(sid, cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def put(cid, sid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(src)
    assert "put" in mutators
    assert "put" not in serializing, "the decorator bound the wrong id to its target"

    # The real shape -- `return fn(cid, *args, **kwargs)` -- still qualifies, so
    # this narrows the rule rather than rejecting decorators generally.
    ok = src.replace("return fn(sid, cid, *a)", "return fn(cid, sid, *a)")
    _f, serializing, _m = _probe(ok)
    assert "put" in serializing


def test_no_callee_without_a_bindable_cid_may_vouch_for_its_caller():
    """A callee with no `cid` parameter cannot establish coverage of the
    caller's campaign — there is nothing for the caller's `cid` to reach.

    This took two goes. The first version excluded a helper locking a module
    global but kept a bypass for `locks.hold_all(...)`, reasoning that an
    all-campaign holder covers whatever the caller's campaign is. That read the
    callee's *name* and not its argument: `hold_all([])` and `hold_all([sid])`
    are equally `hold_all` and hold neither everything nor this campaign. No
    syntactic form proves an argument enumerates every campaign, so there is no
    narrower bypass to write — it is gone.

    Each of these was reported by NEITHER check before its fix: `_serializing`
    accepted the caller and `_mutators` also stopped propagating at it, which is
    quieter than the helper taking no lock at all."""
    for src, why in [
        ("cid = 'a-global'\n"
         "def helper():\n"
         "    with locks.campaign_lock(cid):\n"
         "        atomic.write_text(p, x)\n"
         "def put(cid):\n"
         "    helper()\n", "a helper locking a module global"),
        ("def helper():\n"
         "    with locks.hold_all([]):\n"
         "        atomic.write_text(p, x)\n"
         "def put(cid):\n"
         "    helper()\n", "hold_all of nothing"),
        ("def helper():\n"
         "    with locks.hold_all([sid]):\n"
         "        atomic.write_text(p, x)\n"
         "def put(cid):\n"
         "    helper()\n", "hold_all of another campaign"),
        ("def helper():\n"
         "    with locks.hold_all(all_cids()):\n"
         "        atomic.write_text(p, x)\n"
         "def put(cid):\n"
         "    helper()\n", "hold_all of an expression this cannot read"),
    ]:
        _f, serializing, mutators = _probe(src)
        assert "put" in mutators, f"{why}: the caller was reported by neither check"
        assert "put" not in serializing, f"{why} vouched for its caller"

    # A helper that DOES take `cid` still covers a caller that binds it -- the
    # rule is about the missing parameter, not about delegation.
    ok = ("def helper(cid):\n"
          "    with locks.campaign_lock(cid):\n"
          "        atomic.write_text(p, x)\n"
          "def put(cid):\n"
          "    helper(cid)\n")
    _f, serializing, _m = _probe(ok)
    assert "put" in serializing


def test_the_locks_receiver_must_be_the_locks_module():
    """Requiring the receiver to be spelled `locks` was the fix for
    `def put(cid, campaign_lock=nullcontext)`. It moved the shadowing up one
    level rather than closing it: `def put(cid, locks=fake)` passes the same
    check, and `fake.campaign_lock` need not lock anything.

    Answered per module, not per function — coarser than Python's scoping, and
    the version that cannot be defeated by rebinding somewhere the per-function
    check does not look."""
    for src, why in [
        ("def put(cid, locks=fake):\n"
         "    with locks.campaign_lock(cid):\n"
         "        atomic.write_text(p, x)\n", "a parameter"),
        ("locks = fake\n"
         "def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        atomic.write_text(p, x)\n", "a module-level assignment"),
        ("import json as locks\n"
         "def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        atomic.write_text(p, x)\n", "an import alias"),
        ("def put(cid):\n"
         "    for locks in registries:\n"
         "        with locks.campaign_lock(cid):\n"
         "            atomic.write_text(p, x)\n", "a loop target"),
    ]:
        _f, serializing, mutators = _probe(src)
        assert "put" in mutators
        assert "put" not in serializing, f"`locks` shadowed by {why} still read as the module"

    # Unshadowed, the same body is the real acquisition.
    _f, serializing, _mm = _probe(
        "from . import locks\n"
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in serializing, "the real `locks` import was rejected"


def test_a_parameter_shadows_the_name_the_whitelist_validated():
    """The `locks` rule, generalized — and the same defect a third time.

    A module-level `helper` validated as a lock alias, or `_serialized`
    validated as a locking decorator, is not what a parameter of that name
    refers to. `_module_level` already refused a name module scope rebinds to
    something unresolvable; a parameter rebinds it too and was not looked at, so
    the whitelist kept vouching for the definition while the call reached the
    parameter."""
    alias = ("from contextlib import contextmanager\n"
             "@contextmanager\n"
             "def helper(cid):\n"
             "    with locks.campaign_lock(cid):\n"
             "        yield\n"
             "def put(cid, helper=nullcontext):\n"
             "    with helper(cid):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(alias)
    assert "put" in mutators
    assert "put" not in serializing, "a parameter borrowed the module-level alias"

    # The decorator whitelist has the same seam, and it is closed by the same
    # rule rather than by a second one.
    decorator = ("def _serialized(fn):\n"
                 "    def locked(cid, *a):\n"
                 "        with locks.campaign_lock(cid):\n"
                 "            return fn(cid, *a)\n"
                 "    return locked\n"
                 "def factory(_serialized=identity):\n"
                 "    return _serialized\n"
                 "@_serialized\n"
                 "def put(cid):\n"
                 "    atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(decorator)
    assert "put" not in serializing, "a parameter borrowed the module-level decorator"

    # Without the shadowing parameter both are recognized, so this narrows the
    # whitelist rather than disabling it.
    _f, serializing, _m = _probe(alias.replace("def put(cid, helper=nullcontext):",
                                               "def put(cid):"))
    assert "put" in serializing
    _f, serializing, _m = _probe(
        decorator.replace("def factory(_serialized=identity):\n    return _serialized\n", ""))
    assert "put" in serializing


def test_an_annotated_alias_binds_a_name_too():
    """`put = _put` was collected; `put: Callable[..., None] = _put` was not,
    because Python represents the annotated form with a different node. The
    public name was never collected at all, so `_analyze` skipped the writer as
    private and a domain module could expose an unlocked public mutator."""
    funcs, _s, mutators = _probe("def _put(cid):\n"
                                 "    atomic.write_text(p, x)\n"
                                 "put: Callable[..., None] = _put\n")
    assert "put" in funcs, "an annotated re-export was never collected"
    assert "put" in mutators


def test_an_import_rebinds_a_module_scope_name():
    """The shadowing rule reached parameters and assignments but not imports.
    A local `safe` that really locks, followed by `from hooks import safe`,
    left the whitelist validating the definition while `@safe` decorates with
    the imported object."""
    src = ("def safe(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "from hooks import safe\n"
           "@safe\n"
           "def put(cid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(src)
    assert "put" in mutators
    assert "put" not in serializing, "an import did not poison the local definition"

    _f, serializing, _m = _probe(src.replace("from hooks import safe\n", ""))
    assert "put" in serializing, "the local decorator was rejected without the import"


def test_the_store_locks_spelling_resolves_its_root():
    """`store.locks.campaign_lock` is the other spelling the docstring
    supports, and `_receiver_name` reads its receiver as `locks` — discarding
    the outer `store`, which can itself be a parameter. The `locks`-parameter
    fix did not reach it."""
    _f, serializing, mutators = _probe(
        "def put(cid, store=fake):\n"
        "    with store.locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a shadowed `store` still reached the real module"

    # Unshadowed, the two-segment spelling is a real acquisition.
    _f, serializing, _m = _probe(
        "from grimoire import store\n"
        "def put(cid):\n"
        "    with store.locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in serializing, "the `store.locks` spelling was rejected outright"


def test_the_lock_module_is_surveyed_like_any_other():
    """`_survey` skipped `store/locks.py` outright, on the reasoning that the
    lock is not a lock taker. Whether a module takes the lock has nothing to do
    with whether it publishes, and the skip removed it from discovery entirely:
    a future public `cid` writer there would be unclassified, unlocked and
    invisible.

    The marker audit skipped it too, and for the same bad reason — so a bare
    `# lock-domain-ok:` there would have been exempt from the minimum-reason
    check and invisible to the exemption cap. Both skips are gone; the audit
    below is what proves the second one.

    This one is a ratchet rather than a mutation-verified guard, and says so:
    `locks.py` publishes nothing today, so restoring the skip would not make it
    fail. Its value is entirely in the day that stops being true."""
    assert LOCKS_PY.exists()
    assert _module_name(LOCKS_PY) not in _survey(), (
        "store/locks.py is in the survey; it is analyzed like any other module "
        "now, so classify it rather than restoring the skip")
    unserialized, mutators = _analyze(LOCKS_PY)
    assert not mutators and not unserialized

    # The marker audit reaches it: no unaudited exemptions hide in the one file
    # whose whole purpose is the lock.
    src = LOCKS_PY.read_text(encoding="utf-8")
    funcs = _functions(ast.parse(src))
    every = [d for ds in funcs.values() for d in ds]
    marked = [n for n, defs in funcs.items() for fn in defs
              if _exemption(src, fn, [o for o in every if o is not fn]) is not None]
    assert not marked, f"unaudited `{MARKER}` in the lock module: {marked}"


def test_every_write_must_execute_under_the_lock():
    """Entering the lock somewhere made the WHOLE function read as serialized.

    The sharpest hole on this branch, and the one the guard's own machinery
    already had the answer to: `_under_lock` has asked "does every occurrence of
    this run while the lock is held?" since round six, for yields and for
    decorator target invocations. It was never asked about the function's own
    writes, so

        def put(cid):
            with locks.campaign_lock(cid):
                pass
            atomic.write_text(p, x)      # completely unlocked

    passed. This is NOT the documented limit -- that one is a read-modify-write
    whose *read* sits outside the block, which still passes and is stated in the
    module docstring. This was the write itself outside it.
    """
    for src, why in [
        ("def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        pass\n"
         "    atomic.write_text(p, x)\n", "after the block"),
        ("def put(cid):\n"
         "    atomic.write_text(p, x)\n"
         "    with locks.campaign_lock(cid):\n"
         "        log(cid)\n", "before the block"),
        ("def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        atomic.write_text(p, x)\n"
         "    atomic.write_text(q, y)\n", "one write locked, one not"),
        ("def put(cid):\n"
         "    if flag:\n"
         "        with locks.campaign_lock(cid):\n"
         "            atomic.write_text(p, x)\n"
         "    else:\n"
         "        atomic.write_text(q, y)\n", "an unlocked branch"),
        ("def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        pass\n"
         "    [atomic.write_text(p, v) for v in vs]\n", "inside a comprehension"),
    ]:
        _f, serializing, mutators = _probe(src)
        assert "put" in mutators
        assert "put" not in serializing, f"a write {why} read as serialized ({src!r})"

    # ...and the shapes that ARE covered still are, including through the alias
    # and `hold_all` arms, which the traversal had to learn about to answer this.
    for src, why in [
        ("def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        for v in vs:\n"
         "            if v:\n"
         "                atomic.write_text(p, v)\n", "nested blocks inside the lock"),
        ("from contextlib import contextmanager\n"
         "@contextmanager\n"
         "def _lock(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        yield\n"
         "def put(cid):\n"
         "    with _lock(cid):\n"
         "        atomic.write_text(p, x)\n", "a module-local alias"),
        ("def put(cid):\n"
         "    with locks.hold_all([cid]):\n"
         "        atomic.write_text(p, x)\n", "hold_all covering this campaign"),
    ]:
        _f, serializing, _m = _probe(src)
        assert "put" in serializing, f"{why} was rejected ({src!r})"


def test_a_decorator_does_not_cover_a_deferred_body():
    """A synchronous wrapper around an `async def` or a generator only
    constructs the coroutine; the writes run later, when somebody awaits or
    iterates it, with the lock long released. The wrapper was validated in
    isolation, as if what it wraps could not matter."""
    dec = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n")
    for body, why in [
        ("@_serialized\nasync def put(cid):\n    atomic.write_text(p, x)\n", "async def"),
        ("@_serialized\ndef put(cid):\n    atomic.write_text(p, x)\n    yield 1\n", "generator"),
    ]:
        _f, serializing, mutators = _probe(dec + body)
        assert "put" in mutators
        assert "put" not in serializing, f"a sync decorator covered a {why} body"

    # The same decorator over a plain body is still the recognized form.
    _f, serializing, _m = _probe(
        dec + "@_serialized\ndef put(cid):\n    atomic.write_text(p, x)\n")
    assert "put" in serializing


def test_a_decorated_target_must_take_cid_first():
    """The wrapper locks its own first argument and passes arguments through
    positionally, so `@_serialized def put(sid, cid)` locks the scene id while
    the campaign id lands on the target's second slot. Same positional-binding
    rule as `_locks_its_first_param`, at the fifth boundary an id crosses."""
    dec = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n")
    _f, serializing, mutators = _probe(
        dec + "@_serialized\ndef put(sid, cid):\n    atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "the wrapper locked the wrong id for this target"

    _f, serializing, _m = _probe(
        dec + "@_serialized\ndef put(cid, sid):\n    atomic.write_text(p, x)\n")
    assert "put" in serializing, "`cid` first was rejected"


def test_an_annotated_alias_of_the_decorated_target_is_followed():
    """`_target_aliases` was the one assignment walker still reading
    `ast.Assign` directly instead of going through `_bindings` — exactly the
    gap round thirteen predicted would be next, in the helper introduced to
    close that class."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            fn(cid, *a)\n"
           "        target: Callable = fn\n"
           "        return target(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def put(cid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(src)
    assert "put" in mutators
    assert "put" not in serializing, "an annotated rebinding of the target was invisible"


def test_a_writer_bound_by_assignment_is_seen():
    """`from .atomic import write_text` was resolved; `publish = atomic.write_text`
    was not. Same binding question, different keyword — and the same consequence
    as the `import shutil as fs` alias: the call falls through the bare-name
    branch and the module leaves the survey entirely rather than reading as
    unlocked."""
    for src, why in [
        ("publish = atomic.write_text\n"
         "def put(cid):\n"
         "    publish(p, data)\n", "atomic.write_text"),
        ("import shutil\n"
         "wipe = shutil.rmtree\n"
         "def put(cid):\n"
         "    wipe(root)\n", "shutil.rmtree"),
        ("import os as fs\n"
         "cut = fs.truncate\n"
         "def put(cid):\n"
         "    cut(p, 0)\n", "an aliased namespace, then assigned"),
        ("publish = atomic.write_text\n"
         "alias = publish\n"
         "def put(cid):\n"
         "    alias(p, data)\n", "a chain of assignments"),
    ]:
        _f, _s, mutators = _probe(src)
        assert mutators, f"a writer bound from {why} was invisible ({src!r})"

    # A reader bound the same way is still a reader -- the rule is the writer
    # test, applied to a binding instead of a call.
    _f, _s, mutators = _probe("import os\n"
                              "look = os.stat\n"
                              "def read(cid):\n"
                              "    return look(p).st_mtime\n")
    assert not mutators, "`os.stat` bound by assignment was read as a publication"


def test_a_filesystem_namespace_is_resolved_through_its_import():
    """`import shutil as fs` defeated the inverted namespace check, which keyed
    on the spelling. Worse than reading as unlocked: `copytree` is in no
    enumeration either, so the module dropped out of the survey entirely."""
    for src, why in [
        ("import shutil as fs\ndef put(cid):\n    fs.copytree(a, b)\n", "shutil as fs"),
        ("import os as fs\ndef put(cid):\n    fs.truncate(p, 0)\n", "os as fs"),
        ("import os.path\nimport shutil as sh\ndef put(cid):\n    sh.move(a, b)\n",
         "shutil as sh beside an os.path import"),
    ]:
        _f, _s, mutators = _probe(src)
        assert mutators, f"an aliased namespace was invisible ({why})"

    # `import os.path as p` binds the SUBMODULE, which publishes nothing --
    # mapping it to `os` would read every `p.join(...)` as a write.
    _f, _s, mutators = _probe(
        "import os.path as p\ndef read(cid):\n    return p.join(root, cid)\n")
    assert not mutators, "os.path was read as a publication through its alias"


def test_the_locking_decorator_must_be_the_one_applied_to_the_function():
    """Composition order decides whether the write is inside the lock. `@safe`
    over `@defer` locks around a call that returns a callback and writes
    nothing; the write happens later, unlocked. Accepting any locking name
    anywhere in the chain missed that entirely."""
    chain = ("def defer(fn):\n"
             "    def later(*a):\n"
             "        return lambda: fn(*a)\n"
             "    return later\n"
             "def safe(fn):\n"
             "    def locked(cid, *a):\n"
             "        with locks.campaign_lock(cid):\n"
             "            return fn(cid, *a)\n"
             "    return locked\n"
             "@safe\n"
             "@defer\n"
             "def put(cid):\n"
             "    atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(chain)
    assert "put" in mutators
    assert "put" not in serializing, "a deferring decorator inside the lock was ignored"

    # Innermost and locking: anything further out wraps an already-serialized
    # callable and cannot split the lock from the write.
    _f, serializing, _m = _probe(chain.replace("@safe\n@defer\n", "@defer\n@safe\n"))
    assert "put" in serializing, "a locking decorator applied directly was rejected"


def test_the_decorated_target_is_matched_by_name_not_by_spelling():
    """Matching the trailing attribute let a wrapper satisfy the lock with an
    unrelated `hooks.fn(cid)` while the real call — through an alias, outside
    the block — was never counted."""
    sham = ("def audited(fn):\n"
            "    def locked(cid, *a):\n"
            "        target = fn\n"
            "        with locks.campaign_lock(cid):\n"
            "            hooks.fn(cid)\n"
            "        return target(cid, *a)\n"
            "    return locked\n"
            "@audited\n"
            "def append_message(cid, text):\n"
            "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(sham)
    assert "append_message" not in serializing, \
        "an unrelated same-named call vouched for the decorator"


def test_a_contextmanager_must_yield_under_the_lock_on_every_branch():
    """One yield inside the lock is not enough: the branch that yields unlocked
    hands its caller an unprotected block, and the caller cannot tell."""
    mixed = ("@contextlib.contextmanager\n"
             "def helper(cid):\n"
             "    if FAST:\n"
             "        yield\n"
             "        return\n"
             "    with locks.campaign_lock(cid):\n"
             "        yield\n"
             "def put(cid):\n"
             "    with helper(cid):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(mixed)
    assert "put" not in serializing, "an unlocked yield branch was hidden by the locked one"


def test_a_decorator_that_locks_only_while_decorating_does_not_count():
    """A lock in the decorator's own body is held once, at import, and released
    long before the decorated function is ever called."""
    src = ("def audited(fn):\n"
           "    with locks.campaign_lock(cid):\n"
           "        register(fn)\n"
           "    return fn\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "a decoration-time lock vouched for every runtime call"


def test_an_alias_returning_hold_all_for_another_campaign_does_not_count():
    """`hold_all` inside an alias went unvalidated, so the caller's own
    `_guards_the_campaign` check passed on its argument while the helper held a
    different campaign's locks entirely."""
    src = ("def helper(cid, sid):\n"
           "    return locks.hold_all([sid])\n"
           "def put(cid, sid):\n"
           "    with helper(cid, sid):\n"
           "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "put" not in serializing, "an alias holding another campaign passed"


def test_every_wrapper_a_decorator_can_return_must_lock():
    """`returned` can hold more than one name. Accepting the decorator as soon
    as the locked wrapper was inspected let the flag select the other one."""
    src = ("def audited(fn):\n"
           "    def safe(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    def fast(cid, *a):\n"
           "        return fn(cid, *a)\n"
           "    if FAST:\n"
           "        return fast\n"
           "    return safe\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "one locked wrapper vouched for the unlocked one beside it"


def test_the_real_serialized_decorator_still_qualifies():
    """The counterweight to the four tests above: none of that strictness may
    stop recognizing the shape `scenes` actually uses."""
    src = ("def _serialized(fn):\n"
           "    @functools.wraps(fn)\n"
           "    def locked(cid, *args, **kwargs):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *args, **kwargs)\n"
           "    return locked\n"
           "@_serialized\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" in serializing, "the real @_serialized shape regressed"


def test_path_touch_with_positional_arguments_is_a_mutation():
    """`Path.touch(self, mode=0o666, exist_ok=True)` takes both positionally, so
    the arity discriminator this used to rely on was simply wrong — a real file
    creation written `marker.touch(0o600)` was rejected."""
    for src in ("def mark(cid):\n    (root / 'done').touch()\n",
                "def mark(cid):\n    (root / 'done').touch(0o600)\n",
                "def mark(cid):\n    (root / 'done').touch(0o600, True)\n",
                "def mark(cid):\n    (root / 'done').touch(exist_ok=False)\n"):
        _f, _s, mutators = _probe(src)
        assert mutators, f"missed a Path.touch publication: {src!r}"


def test_a_decorator_holding_another_campaigns_hold_all_does_not_count():
    """The `hold_all` coverage check lived only on the direct path; the
    decorator path accepted it by callee name. Same rule, two places, one of
    them stale — which is why both now go through `_is_lock_context`."""
    src = ("def audited(fn):\n"
           "    def locked(cid, sid):\n"
           "        with locks.hold_all([sid]):\n"
           "            return fn(cid, sid)\n"
           "    return locked\n"
           "@audited\n"
           "def put(cid, sid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "put" not in serializing, \
        "a decorator holding another campaign's locks passed"


def test_a_decorator_with_one_unlocked_path_does_not_count():
    """Accepting the first protected invocation hid the branch beside it: an
    early `return fn(cid)` under a flag runs the decorated writer unlocked."""
    src = ("def audited(fn):\n"
           "    def locked(cid, *a):\n"
           "        if FAST:\n"
           "            return fn(cid, *a)\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "an unlocked branch was hidden by the locked one beside it"


def test_an_alias_that_releases_before_returning_does_not_count():
    """The acquisition has to still be held while the caller's block runs. A
    helper that takes the lock, releases it, and hands back something else left
    the caller's write unprotected."""
    released = ("def helper(cid):\n"
                "    with locks.campaign_lock(cid):\n"
                "        pass\n"
                "    return nullcontext()\n"
                "def put(cid):\n"
                "    with helper(cid):\n"
                "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(released)
    assert "put" not in serializing, \
        "a helper that released the lock before returning passed as an alias"

    held = ("@contextlib.contextmanager\n"
            "def helper(cid):\n"
            "    with locks.campaign_lock(cid):\n"
            "        yield\n"
            "def put(cid):\n"
            "    with helper(cid):\n"
            "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(held)
    assert "put" in serializing, "the real @contextmanager alias regressed"


def test_a_call_deferred_out_of_the_locked_block_does_not_count():
    """The returned-wrapper check still walked into nested defs and lambdas, so
    a target invocation written inside the `with` but executed after it read as
    protected."""
    src = ("def audited(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            later = lambda: fn(cid, *a)\n"
           "        return later()\n"
           "    return locked\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "a call deferred out of the locked block vouched for the decorator"


def test_hold_all_must_cover_this_campaign():
    """`hold_all` holds *a* set of campaign locks. For a cid-scoped writer it
    has to be shown to include this one."""
    wrong = ("def put(cid, sid):\n"
             "    with locks.hold_all([sid]):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(wrong)
    assert "put" not in serializing, "hold_all over another campaign passed"

    right = ("def put(cid, sid):\n"
             "    with locks.hold_all([cid, sid]):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(right)
    assert "put" in serializing

    multi = ("def rebind(all_cids):\n"
             "    with locks.hold_all(all_cids):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(multi)
    assert "rebind" in serializing, "the multi-campaign holder shape regressed"


def test_every_definition_of_a_name_is_analyzed():
    """Keeping only the first definition discarded the one that runs: an
    `@overload` stub ahead of a writing body, or a conditional definition whose
    first branch locks and whose second does not."""
    overloaded = ("@overload\n"
                  "def write_note(cid: str) -> None: ...\n"
                  "def write_note(cid):\n"
                  "    atomic.write_text(p, x)\n")
    _f, _s, mutators = _probe(overloaded)
    assert "write_note" in mutators, "the stub hid the implementation"

    conditional = ("if FAST:\n"
                   "    def put(cid):\n"
                   "        with locks.campaign_lock(cid):\n"
                   "            atomic.write_text(p, x)\n"
                   "else:\n"
                   "    def put(cid):\n"
                   "        atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(conditional)
    assert "put" in mutators
    assert "put" not in serializing, \
        "a locked first branch vouched for an unlocked second one"


def test_a_directly_imported_writer_is_seen():
    """`from .atomic import write_text` leaves the call an `ast.Name`, so there
    is no receiver to key on and the attribute path skips it. `test_atomic_guard`
    matches those names only as attributes, so it is not a fallback either —
    nothing saw this form."""
    src = ("from .atomic import write_text\n"
           "def write_note(cid, t):\n"
           "    write_text(p, t)\n")
    _f, _s, mutators = _probe(src)
    assert "write_note" in mutators, "a directly-imported writer was invisible"

    aliased = ("from .atomic import write_text as _wt\n"
               "def write_note(cid, t):\n"
               "    _wt(p, t)\n")
    _f, _s, mutators = _probe(aliased)
    assert "write_note" in mutators, "the import alias was not followed"

    unrelated = ("def write_text(a, b):\n    return a\n"
                 "def helper(cid):\n    write_text(1, 2)\n")
    _f, _s, mutators = _probe(unrelated)
    assert not mutators, "a local function sharing the name was read as a writer"


def test_the_returned_wrapper_is_the_one_that_must_lock():
    """One level down from the decorator fix: define a `safe` closure that calls
    the target under the lock, never use it, and return an `unsafe` one that
    calls the target bare."""
    src = ("def audited(fn):\n"
           "    def safe(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    def unsafe(cid, *a):\n"
           "        return fn(cid, *a)\n"
           "    return unsafe\n"
           "@audited\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(src)
    assert "append_message" not in serializing, \
        "an unused locking closure vouched for the wrapper actually returned"


def test_a_decorator_locking_the_wrong_id_does_not_count():
    """The direct path validates the lock's argument; the decorator path was
    bypassing that check entirely."""
    src = ("def audited(fn):\n"
           "    def locked(cid, sid):\n"
           "        with locks.campaign_lock(sid):\n"
           "            return fn(cid, sid)\n"
           "    return locked\n"
           "@audited\n"
           "def set_datetime(cid, sid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "set_datetime" not in serializing, \
        "a decorator holding another campaign's lock passed"


def test_an_alias_must_return_the_lock_not_merely_call_it():
    """A bare `campaign_lock(cid)` statement constructs a lock and drops it, so
    a helper that does that and returns a `nullcontext` locks nothing."""
    src = ("def _helper(cid):\n"
           "    locks.campaign_lock(cid)\n"
           "    return nullcontext()\n"
           "def put(cid):\n"
           "    with _helper(cid):\n"
           "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(src)
    assert "put" not in serializing, \
        "a helper that constructed and discarded the lock passed as an alias"


def test_a_decorator_must_actually_wrap_its_target_under_the_lock():
    """Decorator recognition has to descend into nested defs, which is a second
    door into the callback hole: a decorator carrying an unrelated locking
    callback, that hands its target back unchanged, would launder every writer
    it decorates."""
    sham = ("def audited(fn):\n"
            "    def _report(cid):\n"
            "        with locks.campaign_lock(cid):\n"
            "            log(cid)\n"
            "    schedule(_report)\n"
            "    return fn\n"           # target returned UNWRAPPED
            "@audited\n"
            "def append_message(cid, text):\n"
            "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(sham)
    assert "append_message" not in serializing, \
        "a decorator that never wraps its target under the lock laundered a write"

    real = ("def _serialized(fn):\n"
            "    def locked(cid, *a):\n"
            "        with locks.campaign_lock(cid):\n"
            "            return fn(cid, *a)\n"
            "    return locked\n"
            "@_serialized\n"
            "def append_message(cid, text):\n"
            "    atomic.write_text(p, text)\n")
    _f, serializing, _m = _probe(real)
    assert "append_message" in serializing, "the real _serialized shape regressed"


def test_delegation_requires_every_mutating_path_to_be_covered():
    """A wrapper that calls a locked helper AND an unlocked private one was
    marked serialized because *some* callee was. `_analyze` skips private
    helpers, so the unlocked write then had nowhere left to be reported."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def _safe(cid):\n"
           "    atomic.write_text(p, x)\n"
           "def _unsafe(cid):\n"
           "    atomic.write_text(q, y)\n"
           "def publish(cid):\n"
           "    _safe(cid)\n"
           "    _unsafe(cid)\n")
    _f, serializing, mutators = _probe(src)
    assert "publish" in mutators
    assert "publish" not in serializing, \
        "an unlocked helper alongside a locked one passed as delegation"


def test_a_helper_that_only_mentions_the_lock_is_not_an_alias():
    """`_produces_lock` keyed on the callee name alone would accept a helper
    that returns a `nullcontext`, or one that locks a different id."""
    sham = ("def _helper(cid):\n"
            "    log(campaign_lock)\n"
            "    return nullcontext()\n"
            "def put(cid):\n"
            "    with _helper(cid):\n"
            "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(sham)
    assert "put" not in serializing, "a nullcontext helper passed as a lock alias"

    wrong = ("def _helper(sid):\n"
             "    return locks.campaign_lock(sid)\n"
             "def put(cid, sid):\n"
             "    with _helper(sid):\n"
             "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(wrong)
    assert "put" not in serializing, "an alias keyed on another id passed"

    real = ("def _lock(cid):\n"
            "    return locks.campaign_lock(cid)\n"
            "def put(cid):\n"
            "    with _lock(cid):\n"
            "        atomic.write_text(p, x)\n")
    _f, serializing, _m = _probe(real)
    assert "put" in serializing, "the real audit-shaped alias regressed"


def test_a_lock_inside_a_callback_does_not_cover_the_enclosing_body():
    """`ast.walk` descends into nested defs, so a lock taken in a callback made
    the function around it read as serialized while its own write ran under
    nothing. This package persists through callbacks (`proposals` commits a
    narration that way), so the shape is not hypothetical."""
    src = ("def outer(cid):\n"
           "    def _cb():\n"
           "        with locks.campaign_lock(cid):\n"
           "            pass\n"
           "    atomic.write_text(p, x)\n"
           "    register(_cb)\n")
    _funcs, serializing, mutators = _probe(src)
    assert "outer" in mutators
    assert "outer" not in serializing, \
        "a nested closure's lock covered the enclosing function's own write"
    assert "_cb" in serializing, "the closure itself still locks"


def test_pure_delegation_to_a_locked_helper_is_covered():
    """`scenes.create_scene` resolves the calendar, then hands off to the
    `@_serialized` `_create_scene`. Flagging it would be a false positive."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def _create_scene(cid, title):\n"
           "    atomic.write_text(p, title)\n"
           "def create_scene(cid, title):\n"
           "    return _create_scene(cid, hint(title))\n")
    _funcs, serializing, _m = _probe(src)
    assert "create_scene" in serializing


def test_delegation_does_not_cover_a_write_of_the_callers_own():
    """The hole that makes "delegates to a locked helper" dangerous: the wrapper
    also publishes something itself, and that write is serialized by nothing."""
    src = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@_serialized\n"
           "def _inner(cid):\n"
           "    atomic.write_text(p, x)\n"
           "def outer(cid):\n"
           "    _inner(cid)\n"
           "    atomic.write_text(other, y)\n")
    _funcs, serializing, mutators = _probe(src)
    assert "outer" in mutators, "the caller's own write was not seen"
    assert "outer" not in serializing, "an unserialized write read as covered"


def test_a_read_modify_write_split_across_helpers_is_caught():
    """`appearances`-shaped: neither helper locks, so the caller inherits both
    halves and is the mutation site."""
    src = ("def record(cid):\n"
           "    return json.loads(p.read_text())\n"
           "def _write(cid, data):\n"
           "    atomic.write_text(p, data)\n"
           "def pick_version(cid, v):\n"
           "    data = record(cid)\n"
           "    data['v'] = v\n"
           "    _write(cid, data)\n")
    _funcs, serializing, mutators = _probe(src)
    assert "pick_version" in mutators and "pick_version" not in serializing


def test_removal_counts_as_a_mutation():
    """`delete_campaign` publishes nothing through `atomic`; it calls `rmtree`.
    A guard watching only writers would not see it."""
    for src in ("def delete_campaign(cid):\n    shutil.rmtree(root)\n",
                "def drop(cid):\n    p.unlink()\n",
                "def rehome(cid):\n    p.rename(q)\n",
                "def swap(cid):\n    os.replace(tmp, p)\n",
                "def drop2(cid):\n    os.remove(p)\n"):
        _funcs, _s, mutators = _probe(src)
        assert mutators, f"missed a removal/rename mutation: {src!r}"


def test_a_string_or_list_method_is_not_a_filesystem_mutation():
    """`remove` and `replace` are also ordinary builtin methods, and matching
    them receiver-blind classified `store.export` as a campaign mutator because
    `build_html` calls `doc.replace(...)` on a string. Two modules sat in the
    declaration on the strength of that."""
    for src, why in [
        ("def build_html(cid):\n    return doc.replace('a', 'b')\n", "str.replace"),
        ("def leave(cid, sid):\n    rec['scenes'].remove(sid)\n", "list.remove"),
        ("def prune(cid):\n    seen.remove(cid)\n", "set.remove"),
    ]:
        _funcs, _s, mutators = _probe(src)
        assert not mutators, f"{why} was read as a filesystem mutation ({src!r})"


def test_a_function_without_a_cid_is_not_campaign_scoped():
    """World- and module-scoped writers share these primitives and are not in
    this domain; without the `cid` test the guard would flag the whole store."""
    src = "def write_world(wid):\n    atomic.write_text(p, x)\n"
    funcs, _s, mutators = _probe(src)
    assert mutators == {"write_world"}
    assert not _takes_cid(funcs["write_world"][0])


def test_cyclic_helpers_terminate():
    """`scenes` and `overlay` both contain call cycles; a recursive walker hangs
    on either."""
    src = ("def a(cid):\n    b(cid)\n"
           "def b(cid):\n    a(cid)\n    atomic.write_text(p, x)\n")
    _funcs, _s, mutators = _probe(src)
    assert mutators == {"a", "b"}


def test_a_marker_exempts_only_its_own_function():
    src = (f"# {MARKER} runs before the campaign is reachable by anyone else\n"
           "def seed(cid):\n"
           "    atomic.write_text(p, x)\n"
           "def other(cid):\n"
           "    atomic.write_text(q, y)\n")
    funcs = _functions(ast.parse(src))
    assert _exemption(src, funcs["seed"][0]) is not None
    assert _exemption(src, funcs["other"][0]) is None


def test_a_marker_above_a_decorator_attaches():
    """`FunctionDef.lineno` is the `def` line, so the comment block above a
    DECORATED function does not attach to it and the marker reads as absent.
    Every `scenes` mutator is decorated — the one domain module where writing a
    marker in the obvious place has to work."""
    src = (f"# {MARKER} a reason long enough to be a real one\n"
           "@_serialized\n"
           "def append_message(cid, text):\n"
           "    atomic.write_text(p, text)\n")
    funcs = _functions(ast.parse(src))
    assert _exemption(src, funcs["append_message"][0]) is not None


def test_a_marker_inside_a_nested_function_does_not_exempt_the_outer_one():
    """A function's span covers its whole body, so without `others` a marker
    written for an inner closure silently exempted the enclosing mutator."""
    src = ("def outer(cid):\n"
           "    def inner():\n"
           f"        pass  # {MARKER} this reason belongs to the closure\n"
           "    atomic.write_text(p, x)\n")
    funcs = _functions(ast.parse(src))
    others = [d for ds in funcs.values() for d in ds if d is not funcs["outer"][0]]
    assert _exemption(src, funcs["outer"][0], others) is None, \
        "the enclosing mutator inherited a nested function's marker"
    assert _exemption(src, funcs["inner"][0], [funcs["outer"][0]]) is not None
