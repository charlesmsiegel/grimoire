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
  mutator serialize, so a new ``scenes`` mutator that forgets
  ``@locking._serialized`` fails — the transcript-loss case of #254;
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

- **"Public" is approximated by the absence of a leading underscore, with
  dunders excepted.** A private helper runs under its callers' locks and is
  charged to them; a protocol method like ``__setitem__`` is a public entry
  point wearing underscores, and is analyzed.
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
     the module rebinds, and the campaign passed as the name ``cid``, first
     positionally or as the keyword ``cid``;
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
     generator only *constructs* something inside the lock). The decorator may
     be defined in this module or in a sibling module of this package reached
     through a relative import (``@locking._serialized``, which is every
     ``scenes`` mutator since that module became a package) — the receiver is
     resolved to a FILE and that file's definition is put through the identical
     test, so this is one more spelling of the same property and not a weaker
     rule. A receiver that does not resolve reads as *unserialized*, like
     anything else the whitelist cannot resolve.

  Anything else — a computed iterable, a keyword-bound alias, a lock reached
  through an object this cannot resolve — reads as *unserialized*. That will
  produce false alarms on legitimate code the whitelist has not learned. That is
  the intended trade: the marker is cheap and visible, and a false alarm costs a
  comment while a false negative costs a transcript.

  Five things the whitelist deliberately does *not* trust. A name that only
  module scope may bind — a lock alias or decorator resolved from a nested
  ``def`` in some unrelated factory is not the name module scope actually binds.
  A name reached through an attribute, on the attribute alone — ``@hooks.safe``
  is not this module's ``safe``, and it counts only when ``hooks`` resolves to a
  file of this package whose ``safe`` passes the decorator test itself
  (``_sibling_modules``); ``hooks.helper(cid)`` is never this module's lock
  alias, since only the decorator form is resolved across files. A
  decorator that is not the innermost one, because composition order
  decides whether the write happens inside the lock: ``@safe`` over ``@defer``
  locks around a call that returns a callback and writes nothing. And any name a
  binding elsewhere in the module can shadow — the spelling ``locks``, which
  must be imported on a path that names ``grimoire.store.locks`` itself and not
  merely something in the package (``_rebinds_locks``), or an alias or decorator
  rebound by a parameter, a loop target, a ``with ... as``, an import, an
  assignment from any of those, or an assignment inside the calling function
  (``_shadowed_names``, ``_module_level`` and ``_locally_bound``). A call
  reached through an attribute is never module-local — not as a lock, not as an
  alias, and not as a delegate (``_local_call_name``). And an alias carrying any
  decorator but ``@contextmanager``, since a decorator replaces what the name
  means and only that one's semantics are modelled here.

  What is still trusted on its spelling, stated rather than papered over:
  ``open``, ``os``/``shutil`` where no import binds them, and ``contextlib`` /
  ``contextmanager``. A module that rebinds those is not checked. Only ``locks``
  and the names the whitelist validates are resolved to their bindings. And ``cid`` itself, in a function
  whose body reassigns it (``_rebinds_cid``) — every rule here reads that name
  as "this function's campaign", which is a claim about the parameter.

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

  ``store.assets``' ``put_in``/``delete_in`` are recognized as publication the
  same way ``atomic.write_text``/``write_bytes`` are — a call on a resolved
  receiver, not a spelling match — but by ENUMERATION (``_ASSETS_WRITERS``),
  not by inverting the whole ``assets`` namespace: most of it reads
  (``path_in``, ``image_path``, ``list_images``, ...), so inverting it would
  misread a caller's unlocked ``assets.path_in(...)`` as a write. Deliberately
  narrower than that inversion, and deliberately not extended to
  ``put_image``/``delete_image``/``promote_image``: those are real writes too,
  but every caller of theirs today (``characters.py``, ``localize.py``,
  ``image_subjects.py``) passes a CHARACTER id into the ``cid`` parameter this
  guard reads as "campaign id" — recognizing them would misclassify those
  three modules as campaign mutators on the strength of a name collision, not
  a real one.
- **Analysis is per-module, with one exception that reads and does not
  propagate.** Mutation propagates through a module's own helpers, never across
  an import, so a function whose only mutation happens inside a *different*
  module's unserialized mutator is not itself flagged. The callee is flagged in
  its own file instead, which is where the fix goes; what this misses is the
  caller that spans two such calls non-atomically — and a package that splits a
  mutator away from the helper it writes through loses coverage that way, which
  is how ``sheets.write`` and ``appearances.leave`` left the survey when those
  modules became packages. The exception is the decorator in a sibling module
  (form 4 above): that file is parsed to answer one question about one ``def``,
  and nothing crosses the boundary in either direction. It is a hop, not a call
  graph — the sibling's own decorator imports are not followed.
- **A campaign lock held across a suspension point is not serialization.**
  ``_ProcessScopedLock`` is a ``threading.RLock`` plus a file lock, and an RLock
  is owned by a thread rather than a task, so two coroutines on one event-loop
  thread both "hold" it across an ``await`` — the second re-enters, and skips
  the file lock. An ``async def`` that awaits inside the lock therefore reads as
  unserialized here. Nothing in this package does that today; the rule exists
  for the one written later.
- **Every MUTATION must run under the lock; the READ need not.** A function
  mutates two ways -- by writing, and by calling something that writes -- and
  both are positional. A function that locks around part of its body and
  publishes outside that block fails, on every branch and through a
  comprehension; so does one whose locked write is followed by a call to a
  local helper that writes without serializing on this campaign. A helper that
  DOES serialize on this campaign is an atomic unit wherever it is called,
  which is what ``scenes.create_scene`` relies on.

  Neither half was true until rounds fifteen and seventeen -- entering a lock
  anywhere made the whole function read as serialized -- and this is the one
  limit here that got narrower rather than being restated.

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
# `store.assets.put_in`/`delete_in` publish a file under a directory a caller
# hands them, without ever touching `atomic`/`os`/`shutil` themselves --
# `store.covers` is built entirely on the two of them (Task 1's directory-level
# image primitives), so without this a campaign-scoped mutator that writes
# purely by calling them was invisible to the survey, exactly like an
# unenumerated `shutil` primitive would be.
#
# An ENUMERATION, not the `atomic`/`os`/`shutil` inversion above, and
# deliberately narrow: unlike `atomic`, most of `assets`' surface reads
# (`path_in`, `image_path`, `list_images`, `read_focus`, `image_version`, ...),
# so inverting the whole namespace would misread `covers.cover_path`'s
# unlocked `assets.path_in(...)` as a write needing a lock it doesn't need.
#
# And deliberately NOT `put_image`/`delete_image`/`promote_image`, though
# those publish too: each takes a parameter named `cid` -- this guard's own
# convention for "campaign id" (`_takes_cid`) -- but their real callers
# (`characters.py`, `localize.py`, `image_subjects.py`) pass a CHARACTER id
# into it. Recognizing them would newly survey those three modules and force
# a campaign-lock classification onto mutators that were never campaign-scoped
# to begin with -- a false positive this guard's own "campaign-scoped is
# approximated by a `cid` parameter" limit warns about.
_ASSETS_WRITERS = ("put_in", "delete_in")
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

    `ast.NamedExpr` is here because the reason it was excluded was simply wrong.
    The comment said a walrus "binds inside an expression, never at the module
    scope these callers care about" -- but `if (helper := nullcontext): pass` at
    module level rebinds `helper` for the whole module. That was a factual claim
    of mine, not a limitation of the AST, and it is the fourth time on this
    branch that the defect was a sentence rather than the code.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        elif isinstance(node, ast.NamedExpr):
            targets, value = [node.target], node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                yield target.id, value
            elif isinstance(target, (ast.Tuple, ast.List)):
                # `put, other = _put, helper`. Paired element-wise when the
                # shapes match; otherwise each name is yielded against the whole
                # right-hand side, which resolves to nothing and therefore
                # poisons rather than being ignored.
                paired = (isinstance(value, (ast.Tuple, ast.List))
                          and len(value.elts) == len(target.elts))
                for i, element in enumerate(target.elts):
                    if isinstance(element, ast.Name):
                        yield element.id, value.elts[i] if paired else value


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
    # `put = lambda cid: atomic.write_text(p, x)` defines a public mutator with
    # no `def` anywhere, so a collector keyed on FunctionDef saw nothing -- not
    # an unlocked mutator, but no function at all, and potentially no module.
    # The binding forms are `_bindings`' job; what a binding may point AT is
    # this one's, and a lambda was missing from that list.
    for name, value in _bindings(tree):
        if isinstance(value, ast.Lambda):
            out.setdefault(name, []).append(value)

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

    `assets` resolves the same way, for `_ASSETS_WRITERS` -- but it is NOT in
    the identity seed below the way `os`/`shutil`/`atomic` are: those three are
    trusted even where nothing in the module binds them, while `assets` is
    resolved only from an actual import, imported-from, or assignment alias.
    Narrower on purpose -- `assets` names a package-local module, not a
    universally-recognized one, and `_ASSETS_WRITERS` is an enumeration of two
    names rather than an inverted whole namespace, so trusting the bare word
    would buy nothing an import doesn't already give it.
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
                if alias.name in (*_FS_NAMESPACES, "atomic", "assets"):
                    out[alias.asname or alias.name] = alias.name
    # `import shutil; fs = shutil` binds the MODULE OBJECT, which the import
    # scan above cannot see. Round sixteen resolved import aliases and left
    # assignment aliases, so `fs.copytree(...)` still fell through -- and since
    # `copytree` is in no enumeration, the module left the survey rather than
    # reading as unlocked. Iterated for `a = shutil; b = a`.
    bound = list(_bindings(tree))
    for _ in range(len(bound) + 1):
        grew = False
        for name, value in bound:
            if isinstance(value, ast.Name) and value.id in out and name not in out:
                out[name] = out[value.id]
                grew = True
        if not grew:
            break
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
    def of(cls, tree: ast.AST, package: str = "store") -> "_Surface":
        namespaces = _fs_namespaces(tree)
        writers = _imported_writers(tree) | _assigned_writers(tree, namespaces)
        return cls(frozenset(writers), namespaces, not _rebinds_locks(tree, package))


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


def _local_call_name(node: ast.AST) -> str | None:
    """The MODULE-LOCAL name a call invokes, or None for anything else.

    `_called_name` reads the trailing attribute, so `hooks._write(cid)` looked
    like this module's `_write`. That mattered in three places at once, all of
    them places where resolving to a local name GRANTS safety: it made a foreign
    call count as delegation to a serialized helper, made it an atomic unit that
    stops mutation propagation, and excused it from the position check. The
    caller was then reported by neither guard -- the foreign module skips the
    private helper as private, and this one thought it was covered.

    Only used where a match buys safety. `_reaches_a_write` deliberately keeps
    the receiver-blind name, because there over-matching is the conservative
    direction.
    """
    return node.func.id if isinstance(node, ast.Call) \
        and isinstance(node.func, ast.Name) else None


def _bound_names(node: ast.AST) -> set[str]:
    """Every name this ONE node binds, whatever syntax does the binding.

    Four scans in this file ask that question -- `_rebinds_cid`,
    `_rebinds_locks`, `_locally_bound` and `_module_level` -- and each grew its
    own answer, so each knew a different subset of Python's binding forms.
    `_rebinds_cid` learned `except ... as`, `match` captures and
    `global`/`nonlocal` in round twenty; `_locally_bound` learned `import` in
    round twenty-four; `_rebinds_locks` and `_module_level` learned neither. The
    result was that the SAME rebinding poisoned `cid` and not `locks`:

        def put(cid, source):
            match source:
                case locks:              # binds `locks` to whatever `source` is
                    pass
            with locks.campaign_lock(cid):    # `source.campaign_lock` locks nothing
                atomic.write_text(p, x)

    That is the branch's recurring defect stated exactly: a correct rule with an
    incomplete list of the places it is applied -- except that here the list was
    of binding FORMS and there were four copies of it. One list, four callers.

    A `def` or `class` binds its name too, and is included: a module-level
    `def locks(...)` is not the locks module. `_module_level` is the one caller
    that must tell a definition from a rebinding, so it keeps its own handling
    of those two node types and uses this for everything else.
    """
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return {node.id}
    if isinstance(node, ast.arg):
        return {node.arg}
    if isinstance(node, ast.alias):
        return {(node.asname or node.name).split(".")[0]}
    # These forms store the bound name as a plain string on the node rather than
    # as an `ast.Name`, which is why a Name-only scan could not see any of them.
    if isinstance(node, ast.ExceptHandler) and node.name:
        return {node.name}
    if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
        return {node.name}
    if isinstance(node, ast.MatchMapping) and node.rest:
        return {node.rest}
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return set(node.names)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    return set()


def _locally_bound(fn: ast.AST) -> set[str]:
    """Names `fn` binds itself -- parameters, and anything its own body stores.

    `_shadowed_names` answers this module-wide and for parameters only, because
    poisoning every name any function assigns would poison half the module. But
    a mutator that writes `helper = nullcontext` in its OWN body is not calling
    the module-level `helper`, and module-wide analysis deliberately stops at
    function bodies, so nothing looked. Answered per function, where it costs
    nothing.
    """
    params = set().union(*(_bound_names(n) for n in ast.walk(fn.args))) \
        if hasattr(fn, "args") else set()
    return params.union(*(_bound_names(n) for n in _own_body(fn)), set())


def _rebinds_cid(fn: ast.AST) -> bool:
    """`fn` assigns to `cid` in its own body.

    Every rule in this guard is keyed on the NAME `cid` meaning this function's
    campaign, which is a claim about the parameter and stops being true the
    moment the body rebinds it:

        def put(cid, target):
            with locks.campaign_lock(cid):
                cid = target             # the lock is on the OLD cid
                atomic.write_text(root / cid, x)

    Two calls with different originals and the same `target` then write one
    campaign while holding two different locks. This is the same "a name is not
    a binding" rule the module-scope checks apply, pointed at the one name
    everything else here trusts.
    """
    # The BODY, not the signature: `_bound_names` reports a parameter as a
    # binding, which is right for every other caller and wrong here -- `cid`
    # arriving as a parameter is the premise, not a rebinding of it.
    # A lambda's `body` is a single expression rather than a list of statements,
    # and it reaches here through `_functions`, which binds lambdas to names.
    body = getattr(fn, "body", [])
    return any("cid" in _bound_names(node)
               for stmt in (body if isinstance(body, list) else [body])
               for node in (stmt, *_own_body(stmt)))


def _rebinds_locks(tree: ast.AST, package: str = "store") -> bool:
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
        # Assignment, `for`, `with ... as`, walrus, a parameter of any function
        # here, `except ... as`, a `match` capture, `global`, a `def` or `class`
        # of that name -- every form, because they are one list now. This scan
        # knew the first two, and `_rebinds_cid` knew the rest; see
        # `_bound_names`. An `import` is excluded and resolved below instead,
        # since that is the one binding that can name the real module.
        if not isinstance(node, ast.alias) and _bound_names(node) & watched:
            return True
    # An import binds a watched name to whatever module it came FROM, and the
    # previous version exempted any import whose source symbol happened to be
    # spelled `locks` -- so `from hooks import locks` was trusted. The spelling
    # again, one binding form further out. Trust is now the module it comes
    # from: a relative import, or an absolute one rooted in this package.
    # Package membership is not identity: `from .hooks import locks` is
    # relative and still not `store.locks`. Only the spellings that name the
    # store package are trusted -- `from . import locks` inside `store/`,
    # `from .store import locks` from a sibling package, and the absolute form.
    # Both spellings the package actually uses are here; anything else poisons.
    # The dotted path an import actually binds must name `grimoire.store` or
    # `grimoire.store.locks` -- the object, not merely a module in the package.
    # `from .hooks import locks` is relative and is not the store's lock, and
    # `from grimoire import store` is absolute and IS the store; an
    # origin-shaped rule got both wrong in opposite directions.
    # A relative import is resolved against the importing module's own package,
    # which is the whole point of one. Matching the written path treated
    # `from . import locks` as `store.locks` everywhere -- true in `store/`,
    # false in `store/weather/`, where it names a sibling that need not lock
    # anything. This package has three such subpackages.
    trusted = {("store", "locks"), ("store",)}
    here = package.split(".") if package else []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:               # `.` is this package, `..` its parent
                base = here[:len(here) - node.level + 1]
            else:
                base = []                # absolute; `grimoire.` is stripped below
            prefix = node.module.split(".") if node.module else []
            for alias in node.names:
                if (alias.asname or alias.name) not in watched:
                    continue
                path = [*base, *prefix, alias.name]
                if path[:1] == ["grimoire"]:
                    path = path[1:]
                elif not node.level:
                    return True          # an absolute import of something else
                if tuple(path) not in trusted:
                    return True          # `from .hooks import locks`
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                written = tuple(alias.name.split("."))
                # Stripping the first component let `import hooks.store.locks
                # as locks` through: its tail IS ("store", "locks"). The root
                # has to be checked, not dropped.
                if bound in watched and written not in {("grimoire", *p) for p in trusted}:
                    return True          # `import json as locks`
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
        return name == "open" and _is_write_mode(node, (1,), (1,))  # path first
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
    if namespace == "assets" and name in _ASSETS_WRITERS:
        # An ENUMERATION, unlike the `atomic` line above -- see `_ASSETS_WRITERS`
        # for why the whole namespace is not inverted here.
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


def _is_write_mode(node: ast.Call, positions=(0, 1), strict=()) -> bool:
    """`open(p, "w")` / `p.open("w")` in any writable mode -- mirrors
    `test_atomic_guard._is_write_mode`, tested for the characters that make a
    mode writable rather than enumerating literals.

    `positions` says where the mode can be, and it matters: the builtin takes
    the PATH first, so scanning both positions read `open("a")` -- a filename --
    as append mode. That is the first FALSE POSITIVE this review has produced,
    and it is the failure this guard's stated trade accepts, so it is worth
    naming rather than quietly fixing: a read-only module would have been pushed
    into the declaration, or a reader given a lock it does not need. Bare
    `open` passes `(1,)`; an attribute call keeps both, since `p.open("w")` puts
    the mode first and `codecs.open(p, "w")` puts it second.
    """
    args = [node.args[i] for i in positions if i < len(node.args)]
    keywords = [k.value for k in node.keywords if k.arg == "mode"]
    for arg in [*args, *keywords]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                and 0 < len(arg.value) <= 3 and set(arg.value) <= set("rwaxbt+") \
                and any(c in arg.value for c in "wax+"):
            return True
    # A mode this cannot read is not a mode it may assume is read-only:
    # `mode = "w"; open(path, mode)` published, and the handle's `.write()` is
    # not matched either, so the module vanished from the survey. Fail closed --
    # but only where the argument is UNAMBIGUOUSLY the mode. `strict` is empty
    # for an attribute call, because `p.open(x)` and `codecs.open(x, "w")` put
    # different things in position 0 and guessing there costs false alarms.
    unreadable = [a for a in [*(node.args[i] for i in strict if i < len(node.args)),
                              *keywords]
                  if not (isinstance(a, ast.Constant) and isinstance(a.value, str))]
    return bool(unreadable)


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
    # The keyword form is the same acquisition -- `campaign_lock` declares an
    # ordinary `cid` parameter -- and rejecting it would have forced valid code
    # to be rewritten or exempted. The second false positive this review has
    # produced, and like the first it costs a reader real code rather than
    # letting a race through.
    return (bool(call.args) and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "cid") or _passes_cid_by_keyword(call)


def _locks_its_first_param(fn: ast.AST) -> bool:
    """The lock this helper hands back is keyed on its OWN first parameter.

    What makes positional binding safe to reason about: if the helper locks its
    first parameter and the caller passes `cid` first, the two are the same
    campaign. Any other arrangement is a helper this cannot follow, and it fails
    loud rather than being assumed benign.
    """
    params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
    return bool(params) and params[0] == "cid"


# The one decorator whose semantics `_produces_lock` models: it requires the
# yield to sit inside the lock, which is exactly what `@contextmanager` means.
# Matched on the dotted spelling, in both forms this package writes. The
# residual limit, stated rather than papered over: a module that rebinds
# `contextlib` or `contextmanager` itself is not checked -- the same trust this
# guard already extends to the spellings `open` and `os`.
_CONTEXTMANAGERS = ("contextmanager", "asynccontextmanager",
                    "contextlib.contextmanager", "contextlib.asynccontextmanager")


def _dotted(expr: ast.expr) -> str | None:
    """`f`, `m.f` and `pkg.m.f` as written, or None for anything else."""
    if isinstance(expr, ast.Call):
        expr = expr.func
    parts = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name):
        return None
    parts.append(expr.id)
    return ".".join(reversed(parts))


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
    # A decorator applied to the helper replaces what the name means, and this
    # analyzed the undecorated body: `@replace_with_nullcontext def helper(cid):
    # return locks.campaign_lock(cid)` read as a lock producer. Only
    # `@contextmanager` is recognized -- it is the one whose semantics this
    # already models, by requiring the yield to sit inside the lock.
    if any(_dotted(d) not in _CONTEXTMANAGERS
           for d in getattr(fn, "decorator_list", [])):
        return False
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
    # `campaign_lock_nowait` takes the same lock on the same campaign; it only
    # declines to WAIT for it, for work that must not delay the caller when the
    # campaign is busy (`store.prompt_log.record`, on the generating path).
    #
    # What this cannot check, said plainly rather than left implied: that helper
    # yields a boolean the body is supposed to honour, and a body that writes on
    # False has taken no lock at all. So a `with` over this name proves less than
    # a `with` over `campaign_lock` -- still far narrower than the exemption
    # marker, which excuses a whole function, but not nothing. The obligation
    # lives in `store.locks.campaign_lock_nowait`'s docstring; today `record` is
    # its only caller and it returns early on False.
    return name in ("campaign_lock", "campaign_lock_nowait") and _guards_the_campaign(expr)


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
        if _is_alias_context(call, aliases):
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

    Every node is examined by the SAME step, `visit`, including the statements
    inside a `with` body. The previous shape tested a node only where it
    appeared as a child of the node being iterated, and handed `with` bodies to
    a fresh top-level call -- so a statement in a `with` body was never itself
    tested, and a `with` nested inside another `with` never had its own context
    evaluated. That made `with suppress(OSError): with campaign_lock(cid): ...`
    read as unlocked, a false alarm on legitimate code, and made `async with`
    invisible as a suspension point. One step, applied everywhere, is the same
    correction this guard has needed at four other sites.
    """
    def visit(child, locked):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                              ast.GeneratorExp)):
            return                         # deferred, not executed here
                                           # `(f(x) for x in xs)` runs when the
                                           # caller iterates it, which for a
                                           # value returned OUT of the lock is
                                           # after the lock is gone. A list or
                                           # set comprehension is eager and
                                           # deliberately still descended into.
        if want(child):
            yield locked
        if isinstance(child, (ast.With, ast.AsyncWith)):
            inner = locked or any(_is_lock_context(i.context_expr, fn)
                                  or _is_alias_context(i.context_expr, aliases)
                                  for i in child.items)
            for item in child.items:       # the context expressions themselves,
                for sub in ast.iter_child_nodes(item):   # evaluated before the
                    yield from visit(sub, locked)        # lock is held
            for stmt in child.body:
                yield from visit(stmt, inner)
            return
        for sub in ast.iter_child_nodes(child):
            yield from visit(sub, locked)

    for child in ast.iter_child_nodes(node):
        yield from visit(child, locked)


def _is_alias_context(expr: ast.expr, aliases) -> bool:
    """`with <alias>(cid, ...)` — a module-local helper that hands back this
    campaign's lock. The alias arm of `_enters_lock`, extracted so the traversal
    and the direct check agree on what an acquisition is."""
    # A bare `ast.Name`, for the reason the decorator path already gives:
    # `_called_name` reads `hooks.helper(cid)` as the local `helper`, so a
    # foreign context manager borrowed a validated local alias. Same defect,
    # the one path that had not been narrowed.
    return (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
            and expr.func.id in aliases and _guards_the_campaign(expr))


def _suspends_under_lock(fn: ast.AST, aliases=()) -> bool:
    """`fn` reaches a suspension point while holding the campaign lock.

    `_ProcessScopedLock` is a `threading.RLock` plus an OS file lock, and an
    RLock is owned by a THREAD, not by a task. So on one event-loop thread:

        async def put(cid):
            with locks.campaign_lock(cid):
                await flush()            # task A suspends, still "holding" it
                atomic.write_text(p, x)

    task B entering the same block re-enters the RLock successfully -- same
    thread -- increments `_depth`, and skips the file-lock acquisition
    entirely. Both tasks then run the read-modify-write concurrently, which is
    the lost update the lock exists to prevent, with the guard passing.

    The reentrancy that makes this possible is load-bearing elsewhere
    (`audit.apply_delta` calls `sheets.set_field` under a held lock), so this
    is not a bug in the lock; it is a shape the lock does not support. Nothing
    in this package holds a campaign lock in an `async def` today -- checked
    before adding this -- so it fails loud for the one somebody writes later.
    """
    return any(_under_lock(fn, fn, False, _is_suspension, aliases))


def _is_suspension(node: ast.AST) -> bool:
    """Every way control can leave a function while it still "holds" the lock.

    `yield` belongs here for the same reason `await` does: the generator is
    resumed by whoever iterates it, and a second generator on the same thread
    re-enters the thread-owned RLock in between. The decorated-generator shape
    was already rejected; a generator that takes the lock DIRECTLY reached this
    predicate, which listed only the async forms."""
    if isinstance(node, ast.comprehension):
        # `[f(x) async for x in src()]` suspends at every iteration, and Python
        # records that as a FLAG on the comprehension clause rather than as an
        # `AsyncFor` or an `Await` -- so a node-type list could not see it.
        return bool(node.is_async)
    return isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith,
                             ast.Yield, ast.YieldFrom))


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
    """The name a decorator applies: a module-local `safe`, or the `mod.name` of
    one reached through a module object. None for anything else.

    `_called_name` was used here and reads a trailing attribute, so `@hooks.safe`
    resolved to `safe` — and a module that happened to define its own locking
    `safe` decorator vouched for every unrelated `@hooks.safe` in the file. The
    decorated function then read as serialized while nothing locked it.

    So an attribute decorator keeps its receiver rather than being reduced to
    its tail: `@hooks.safe` is the name `"hooks.safe"`, which the local `safe`
    can never answer for. Whether that dotted name is in `decorating` at all is
    `_sibling_decorators`' question, and it answers it by resolving `hooks` to a
    file and holding that file's `safe` to the same test — anything it cannot
    resolve stays out of the set and fails loud, as before. Only a plain
    receiver is spelled out; `@a.b.safe` is None, since nothing here resolves a
    two-step attribute chain.

    A decorator *factory* (`@retry(3)`) is let through to `_locks_anywhere`,
    which rejects it on its own terms: its first parameter is the option, not
    the function, so no invocation of the target is ever found.
    """
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute) and isinstance(decorator.value, ast.Name):
        return f"{decorator.value.id}.{decorator.attr}"
    return None


def _sibling_modules(tree: ast.AST, path: pathlib.Path,
                     package: str) -> dict[str, tuple[pathlib.Path, str]]:
    """Names bound to another module of this package, as (file, its package).

    `from . import locking` then `@locking._serialized` is how `store/scenes`
    wears its lock decorator now that the package is split across files, and
    nothing here could see it: `decorating` is built from THIS file's functions,
    so no attribute decorator could ever enter it whatever it wrapped. That is a
    false negative with nothing to do with locking — it would bite any package
    that factors its decorator into its own module — which is why it is fixed
    here rather than exempted in `locks.py`.

    Only a RELATIVE import is resolved, and only to a file that exists: the
    point is to reach a module of this package whose source can be read, and an
    absolute import may name anything on `sys.path`. `node.level` is walked on
    the path and on the dotted package name together, because the two must
    agree — the package string is what `_rebinds_locks` needs on the far side,
    and resolving it from the written path instead is the bug that read
    `from . import locks` in `store/weather/` as `store.locks`.

    A name is not a binding, this file's oldest lesson (`_rebinds_locks`,
    `_module_level`): the receiver of a dotted decorator is that same spelling
    one level further out. So a name is returned only when this import is its
    ONLY binding in the module — anything assigned, imported twice, taken as a
    parameter, or bound by a loop, a `with ... as`, an `except ... as` or a
    `match` capture is dropped and fails loud.
    """
    here = package.split(".") if package else []
    resolved: dict[str, tuple[pathlib.Path, str]] = {}
    imported: dict[str, int] = {}
    rebound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.alias):
            for name in _bound_names(node):
                imported[name] = imported.get(name, 0) + 1
        else:
            # Every binding form there is, minus the import handled above --
            # see `_bound_names`. `ast.arg` is in there, so a parameter named
            # after the sibling poisons it the way `_shadowed_names` poisons an
            # alias.
            rebound |= _bound_names(node)
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue                     # absolute: it may not even be ours
        base = path.parent
        for _ in range(node.level - 1):  # `.` is this package, `..` its parent
            base = base.parent
        prefix = node.module.split(".") if node.module else []
        for part in prefix:
            base = base / part
        pkg = ".".join([*here[:len(here) - node.level + 1], *prefix])
        for alias in node.names:
            file = base / f"{alias.name}.py"
            if file.is_file():           # a package's `__init__.py` is not a
                resolved[alias.asname or alias.name] = (file, pkg)  # sibling
    return {name: value for name, value in resolved.items()
            if name not in rebound and imported.get(name) == 1}


@functools.cache
def _locking_decorators(src: str, package: str) -> frozenset[str]:
    """The module-scope names in ONE module's source that serialize what they
    decorate.

    Exactly the test `_serializing` applies to a decorator defined beside its
    mutators — `_locks_anywhere` on every definition of the name, drawn from
    that file's own module scope, and only if that file does not rebind `locks`
    — so a decorator recognized across a file boundary has been held to the
    same property, not a weaker one. In particular a sibling whose wrapper does
    not take `locks.campaign_lock` vouches for nothing, and a sibling that
    binds `locks` to something else vouches for nothing at all.

    One hop, not a traversal: `_locks_anywhere` reads the sibling's own body,
    so a sibling that re-exports a decorator it imported from a third module has
    no definition here to satisfy it and fails loud.

    Cached because every module in `store/` imports several siblings, and the
    survey would otherwise re-parse each of them once per importer. Keyed on
    the SOURCE rather than on the path, which is not a micro-optimization: a
    path-keyed cache answers for a file whose contents have since changed, so
    the probe that edits a decorator and re-asks would be told the old answer.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:                  # unparsable vouches for nothing,
        return frozenset()               # rather than for everything
    if _rebinds_locks(tree, package):
        return frozenset()
    scope = _module_level(tree)
    return frozenset(name for name, defs in _functions(tree).items()
                     if name in scope and _all(defs, _locks_anywhere))


def _sibling_decorators(tree: ast.AST, path: pathlib.Path,
                        package: str) -> frozenset[str]:
    """`mod.name` spellings this module may decorate with and have it count."""
    out: set[str] = set()
    for bound, (file, pkg) in _sibling_modules(tree, path, package).items():
        try:
            src = file.read_text(encoding="utf-8")
        except OSError:                  # unreadable, same as unparsable
            continue
        out |= {f"{bound}.{name}" for name in _locking_decorators(src, pkg)}
    return frozenset(out)


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
    rebound: set[str] = set()

    def scan(body):
        for node in body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                # A walrus anywhere in a module-level statement -- an `if` test,
                # a comprehension condition -- binds at module scope. Nothing
                # here can resolve what it binds, so it poisons.
                rebound.update(w.target.id for w in ast.walk(node)
                               if isinstance(w, ast.NamedExpr)
                               and isinstance(w.target, ast.Name))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defined.add(node.name)          # its body is a new scope; stop
            elif isinstance(node, ast.ClassDef):
                # A new scope for its BODY, and a binding for its NAME:
                # `class helper(nullcontext): pass` after a locking `def helper`
                # replaces it. The scan skipped the whole node and so recorded
                # neither -- the one module-scope binding form left after the
                # loop, with-as, import and walrus cases.
                rebound.add(node.name)
                continue
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
                                   ast.For, ast.AsyncFor, ast.While, ast.Match)):
                # `for helper in [nullcontext]:`, `with x() as helper:`,
                # `except E as helper:` and `case helper:` all bind at module
                # scope as surely as `=` does, and the scan looked only at their
                # bodies. Nothing here can resolve what they bind to, so the name
                # is poisoned. One list of binding forms -- see `_bound_names`,
                # which this reaches for everything except the `def`/`class`
                # above, the two it must record as DEFINITIONS instead.
                for sub in _own_body(node):
                    # `_own_body` rather than `ast.walk`: a `def` nested in this
                    # block binds ITS parameters and locals in its own scope, and
                    # walking into them would poison module scope with names that
                    # never reach it. The `def` and `class` names themselves are
                    # left to the recursion below, which records them as
                    # definitions -- a conditional import guard is the shape that
                    # puts one here, and it is legitimate.
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                        ast.ClassDef)):
                        continue
                    rebound.update(_bound_names(sub))
                for attr in ("body", "orelse", "finalbody", "handlers", "cases"):
                    inner = getattr(node, attr, [])
                    if attr == "handlers":
                        scan([s for h in inner for s in h.body])
                    elif attr == "cases":
                        scan([s for c in inner for s in c.body])
                    else:
                        scan(list(inner))

    scan(tree.body)
    out = set(defined)
    poisoned = set(imported) | rebound
    for _ in range(len(assigned) + 1):
        before = (len(out), len(poisoned))
        for name, value in assigned:
            # A poisoned source poisons what it is copied into. Checking only
            # membership in `out` let `safe = hooks.identity; alias = safe`
            # revive the rebound name: `safe` was in both sets, and the alias
            # resolution read the wrong one.
            if isinstance(value, ast.Name) and value.id in poisoned:
                poisoned.add(name)
            elif isinstance(value, ast.Name) and value.id in out:
                out.add(name)
            else:
                poisoned.add(name)
        if (len(out), len(poisoned)) == before:
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
        if _suspends_under_lock(w) or _rebinds_cid(w):
            return False                 # the WRAPPER suspends -- round fourteen
                                         # rejected an async decorated target and
                                         # left the async wrapper accepted -- or
                                         # reassigns the id it locked, which
                                         # round eighteen checked only on the
                                         # ordinary path
        is_target = _is_call_to(_target_aliases(w, target))
        # Checked over every syntactic invocation, not only the ones
        # `_every_one_locked` walks: a mis-bound call this cannot reach is not
        # a call this may assume is bound correctly.
        if not all(_guards_the_campaign(c) or _passes_cid_by_keyword(c)
                   for c in ast.walk(w) if is_target(c)):
            return False
        # `_every_one_locked` deliberately does not descend into nested defs,
        # because code there runs later -- which means a target invocation
        # registered as a callback is invisible to it while the one direct call
        # answers for both. Anything the traversal cannot reach fails closed.
        deferred = [n for nested in ast.walk(w)
                    if isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef,
                                           ast.Lambda)) and nested is not w
                    for n in ast.walk(nested) if is_target(n)]
        if deferred:
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
    locks, the write is inside the lock. It was then claimed that anything
    further out "wraps an already-serialized callable and cannot un-serialize
    it", and that is FALSE. `scenes._serialized` uses `functools.wraps`, which
    sets `__wrapped__` on the wrapper, so an outer decorator can reach straight
    past the lock to the raw body:

        @bypass          # `return fn.__wrapped__`
        @_serialized
        def put(cid): atomic.write_text(p, x)

    `put` is now the undecorated function under a name this vouched for. A
    stated reason, and wrong again -- the fifth on this branch, and the same
    shape each time: the rule was right and the sentence justifying its reach
    was not checked against the code it described.

    So the chain is not walked at all: exactly ONE decorator, and it must lock.
    Anything longer fails loud and needs a marker naming what the outer
    decorators do. No function in this package has more than one decorator
    today, so the strict form costs nothing now.

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
    decorators = getattr(fn, "decorator_list", [])
    if len(decorators) != 1 or _decorator_name(decorators[0]) not in decorating:
        return False
    if isinstance(fn, ast.AsyncFunctionDef) or any(_is_yield(n) for n in _own_body(fn)):
        return False                     # a deferred body; the wrapper's lock is
                                         # released before the writes happen
    params = [a.arg for a in [*fn.args.posonlyargs, *fn.args.args]]
    return not _takes_cid(fn) or params[:1] == ["cid"]


def _is_deferred(defs) -> bool:
    """Calling this name only CONSTRUCTS something; its body runs later.

    An `async def` returns a coroutine and a generator function returns a
    generator, so `with lock: return _write(cid)` publishes nothing inside the
    block -- the caller awaits or iterates the result once the lock is gone.
    The decorator path has rejected this shape since round fourteen; delegation
    classified the call by its syntactic POSITION alone and never asked what it
    was calling.
    """
    return any(isinstance(d, ast.AsyncFunctionDef)
               or any(_is_yield(n) for n in _own_body(d)) for d in defs)


def _delegates_deferred(fn: ast.AST, funcs: dict, writing: set[str]) -> bool:
    """`fn` hands its mutation to a callee whose body runs later.

    `return _write(cid)` where `_write` is an `async def` or a generator function
    CONSTRUCTS a coroutine or a generator and returns it; the writes happen when
    somebody awaits or iterates the result, by which point any lock held around
    the call is gone. Round twenty-three added this check to the undecorated
    path, and round twenty-four's review found the decorator path returning
    above it -- so `@safe def put(cid): return _write(cid)` read as serialized.

    Extracted for that reason rather than repeated at the second site: the two
    paths asking the same question separately is what let one of them keep an
    older answer, and this file has now been fixed six times for exactly that.
    """
    return any(_is_deferred(funcs.get(_local_call_name(c), []))
               for c in _own_calls(fn) if _local_call_name(c) in writing)


def _defers_a_write(fn: ast.AST, surface: _Surface = _Surface()) -> bool:
    """`fn` publishes, but no publication runs while the wrapper's lock is held.

    A decorator's lock is held for exactly one call: the one that runs the
    decorated body. Anything the body only CONSTRUCTS -- a generator expression,
    a lambda, a nested `def` handed back to the caller -- writes later, with the
    lock long released:

        @_serialized
        def put(cid):
            return lambda: atomic.write_text(p, x)   # runs after the lock

    Round twenty-one wrote this as a search for a genexp holding a write, which
    answered "is it deferred THIS way?" -- and the answer was one construct out
    of three, so the lambda above read as serialized. The question is inverted
    here to the one that has no list behind it: this function writes, so does any
    of its writing run DIRECTLY? `_under_lock` already refuses to descend into
    every deferred construct, so asking it with `locked=True` and taking `any`
    yields exactly the writes that execute in the body itself. Nothing new has to
    be enumerated when the fourth deferral form is invented.

    Still narrow where it was narrow: a body with a direct write and a harmless
    genexp beside it is unaffected, which is what `scenes.append_reply` is.
    """
    if not _writes_directly(fn, surface):
        return False
    return not any(_under_lock(fn, fn, True,
                               lambda n: isinstance(n, ast.Call)
                               and _is_write_call(n, surface)))


def _serializing(funcs: dict[str, ast.AST], surface=_Surface(),
                 module_level: set[str] | None = None,
                 imported: frozenset[str] = frozenset()) -> set[str]:
    """Names whose bodies establish the campaign lock.

    Three forms, all present in the package today: a direct
    ``with locks.campaign_lock(cid)``; a module-local alias like
    ``audit._lock``; and a decorator that wraps the body in one, which is how
    every ``scenes`` mutator does it (``@locking._serialized``).

    Delegation counts too -- ``scenes.create_scene`` does its work in the
    ``@_serialized`` ``_create_scene`` -- but only when EVERY write the wrapper
    reaches is covered, and only when the caller's ``cid`` lands on the
    parameter the callee locks. Three ways that fails: the wrapper publishes
    something itself, it calls a second unlocked helper alongside the locked
    one, or it passes a different id into the locked position.

    ``module_level`` is the set of names allowed to vouch for a lock -- see
    `_module_level`. ``None`` means "every name", which is only for callers
    that have no tree to draw it from; every real caller passes it.

    ``imported`` is the same permission for the dotted spellings a decorator
    reached through a sibling module wears -- ``locking._serialized`` -- and it
    is EMPTY by default, so a caller that hands over no resolved sibling gets
    exactly today's module-local answer. `_sibling_decorators` is what fills it,
    and it fills it only with names it resolved to a file and put through
    `_locks_anywhere` itself.

    It does not pass through ``module_level``, which is a set of bare names and
    has nothing to say about a dotted one; the equivalent discipline -- the
    receiver must be bound by that import and by nothing else in the module --
    is `_sibling_modules`' job instead.
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
                  if n in scope and _all(ds, _locks_anywhere)} | set(imported)
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
                # An alias this function rebinds is not the module's alias.
                local = aliases - _locally_bound(fn)
                # BEFORE the decorator branch. The wrapper locks the id it was
                # handed, so a body that reassigns `cid` writes a campaign the
                # wrapper never locked -- exactly the round-eighteen defect, at
                # the one path that returned above the check. Two calls with
                # different originals and the same target then write one campaign
                # while holding two different locks.
                if _rebinds_cid(fn):
                    return False          # the lock is keyed on a name the body
                                          # reassigns -- see `_rebinds_cid`
                if _innermost_decorator_locks(fn, decorating):
                    # The wrapper covers the body's own execution, so what is
                    # left to ask is what the body only CONSTRUCTS: a write
                    # deferred into a genexp, a lambda or a returned nested def,
                    # or a call to a delegate whose body runs later. Both are
                    # checks the undecorated path already makes below, and this
                    # branch returned above both of them.
                    return not (_defers_a_write(fn, surface)
                                or _delegates_deferred(fn, funcs, writing))
                if _suspends_under_lock(fn, local):
                    return False          # an RLock is thread-owned, not task-
                                          # owned -- see `_suspends_under_lock`
                # A function mutates in two ways -- by writing, and by calling
                # something that writes -- and BOTH have to be positioned inside
                # the lock. Checking the writes and then RETURNING was the
                # round-fifteen fix's own gap: a locked `atomic.write_text(...)`
                # followed by an unlocked `_unsafe(cid)` was accepted, and since
                # `_analyze` skips private helpers the delegate had nowhere left
                # to be reported either.
                writes = _writes_directly(fn, surface)
                if writes and not _every_one_locked(
                        fn, fn, lambda n: isinstance(n, ast.Call)
                        and _is_write_call(n, surface), local):
                    return False          # a write outside the block -- and "at
                                          # least one" catches a write that only
                                          # a nested def performs
                delegated = [c for c in _own_calls(fn)
                             if _local_call_name(c) in writing]

                def unprotected(node):
                    """A call that mutates and is not covered by the CALLEE.

                    A delegate that serializes on this campaign is an atomic
                    unit wherever it is called. One that does not is a mutation
                    of this function's own, so it has to sit inside this
                    function's lock -- which is how `with lock: _helper(cid)`
                    stays legal while the same call after the block does not.
                    """
                    name = _local_call_name(node)
                    if name is None or name not in writing:
                        return False
                    return not (name in grown
                                and _binds_the_campaign(node, funcs.get(name, [])))

                if not all(_under_lock(fn, fn, False, unprotected, local)):
                    return False
                # ...and position cannot save a call whose body runs later.
                if _delegates_deferred(fn, funcs, writing):
                    return False
                # ...and something must actually establish the lock, rather than
                # the function simply doing nothing that needs one.
                return writes or bool(delegated) or _enters_lock(fn, local)

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
        name = _local_call_name(call)     # a foreign `hooks._write(cid)` is not
        return (name is not None and name in serializing  # this module's helper
                and _binds_the_campaign(call, funcs.get(name, [])))

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
    package = ".".join(_module_name(path).split(".")[:-1])
    surface = _Surface.of(tree, package)
    serializing = _serializing(funcs, surface, _module_level(tree),
                               _sibling_decorators(tree, path, package))
    mutators = _mutators(funcs, serializing, surface)

    every = [d for ds in funcs.values() for d in ds]
    campaign_mutators, unserialized = set(), set()
    for name in mutators:
        defs = funcs[name]
        if not _any(defs, _takes_cid):
            continue
        if name.startswith("_") and not (name.startswith("__")
                                         and name.endswith("__")):
            # ...but a dunder is a PUBLIC entry point wearing underscores.
            # `obj[cid] = value` reaches `__setitem__`, so a module whose only
            # writer is an unlocked one disappeared from the survey entirely.
            #
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
#
# Two entries are RE-KEYED, not added. `store.appearances` and `store.modules`
# each became a package after this landed, and the entry names a module, so the
# unreviewed code moved to `store.appearances.{paths,transitions,versions}` and
# `store.modules.binding` without one line of it being reviewed. Re-keying is a
# rename of the baseline; it is not the growth this test forbids, and the
# distinction is checkable: the survey's mutators for the new names are a subset
# of the ones the old names carried before the split, so no function that was
# outside this backlog is inside it now. The original entries are named beside
# each replacement so the substitution stays auditable rather than becoming the
# swap `test_the_unreviewed_backlog_only_shrinks` exists to catch.
_UNREVIEWED_AT_LANDING = frozenset({
    "store.appearances.paths",         # was store.appearances
    "store.appearances.transitions",   # was store.appearances
    "store.appearances.versions",      # was store.appearances
    "store.assets", "store.campaign_climate",
    "store.changes", "store.characters", "store.chronicle", "store.commits",
    "store.dossiers",
    "store.modules.binding",           # was store.modules
    "store.overlay", "store.playing",
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

def _probe(src: str, package: str = "store"):
    tree = ast.parse(src)
    funcs = _functions(tree)
    surface = _Surface.of(tree, package)
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


def test_assets_put_in_and_delete_in_are_recognized_as_publication():
    """`store.covers` (and any future module built the same way) mutates
    campaign state purely by calling `assets.put_in`/`delete_in` -- neither
    touches `atomic`/`os`/`shutil` itself, so without this the survey would
    never see the module at all, and a `DOMAIN_MODULES` entry for it would be
    a phantom (`test_the_declaration_has_no_phantom_modules`)."""
    for src in ("from . import assets\n"
                "def put_cover(cid):\n"
                "    assets.put_in(d, 'cover', data, ext)\n",
                "from . import assets\n"
                "def delete_cover(cid):\n"
                "    assets.delete_in(d, 'cover')\n"):
        _f, _s, mutators = _probe(src)
        assert mutators, f"a delegated assets write was invisible: {src!r}"

    # An assignment alias resolves the same way `publish = atomic.write_text`
    # already does -- `_fs_namespaces` is one mechanism for both.
    aliased = ("from . import assets\n"
               "art = assets\n"
               "def put_cover(cid):\n"
               "    art.put_in(d, 'cover', data, ext)\n")
    _f, _s, mutators = _probe(aliased)
    assert mutators, "an assets alias bound by assignment was invisible"


def test_assets_reads_and_other_names_stay_invisible():
    """The recognition is a narrow enumeration, not an inverted namespace --
    unlike `atomic`, most of `assets` reads, and a module that only reads
    through it (as `covers.cover_path`/`cover_version` do) must not read as an
    unlocked mutator that needs a lock it has no business taking."""
    for src in ("from . import assets\n"
                "def cover_path(cid):\n"
                "    return assets.path_in(d, 'cover')\n",
                "from . import assets\n"
                "def cover_version(cid):\n"
                "    return assets.image_version(p)\n",
                "def noop(cid):\n"
                "    return None\n"):
        _f, _s, mutators = _probe(src)
        assert not mutators, f"a non-writing call read as a mutation: {src!r}"


def test_assets_put_image_is_not_recognized_as_publication():
    """`put_image`/`delete_image`/`promote_image` publish too, but their real
    callers (`characters.py`, `localize.py`, `image_subjects.py`) pass a
    CHARACTER id into the `cid` parameter this guard reads as "campaign id".
    Recognizing them would newly survey those three modules and force a
    campaign-lock classification onto mutators that were never campaign-scoped
    -- so the enumeration must stop at `put_in`/`delete_in` and go no further,
    even though the receiver resolves to `assets` exactly the same way."""
    for src in ("from . import assets\n"
                "def put_image(cid):\n"
                "    assets.put_image(root, cid, vid, name, data, ext)\n",
                "from . import assets\n"
                "def delete_image(cid):\n"
                "    assets.delete_image(root, cid, vid, name)\n",
                "from . import assets\n"
                "def promote_image(cid):\n"
                "    assets.promote_image(root, cid, vid, name)\n"):
        _f, _s, mutators = _probe(src)
        assert not mutators, f"put_image/delete_image/promote_image leaked in: {src!r}"


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


# `@locking._serialized` -- a decorator reached through a sibling module. The
# receiver has to be resolved to a FILE for any of this to mean anything, so
# these probes write one instead of parsing a string; `_probe` above cannot
# reach the code below.
SIBLING_LOCK = ("from .. import locks\n"
                "def _serialized(fn):\n"
                "    def locked(cid, *a, **kw):\n"
                "        with locks.campaign_lock(cid):\n"
                "            return fn(cid, *a, **kw)\n"
                "    return locked\n")
SIBLING_USER = ("from . import locking\n"
                "@locking._serialized\n"
                "def put(cid):\n"
                "    atomic.write_text(p, x)\n")


def _probe_sibling(tmp_path, src, sibling=SIBLING_LOCK, name="locking",
                   package="store.scenes"):
    """`_probe` for a module whose decorator lives in a sibling file."""
    (tmp_path / f"{name}.py").write_text(sibling, encoding="utf-8")
    path = tmp_path / "write.py"
    path.write_text(src, encoding="utf-8")
    tree = ast.parse(src)
    funcs = _functions(tree)
    surface = _Surface.of(tree, package)
    serializing = _serializing(funcs, surface, _module_level(tree),
                               _sibling_decorators(tree, path, package))
    return funcs, serializing, _mutators(funcs, serializing, surface)


def test_a_decorator_reached_through_a_sibling_module_is_resolved(tmp_path):
    """`decorating` is built from THIS file's functions, so once `store/scenes`
    became a package and its `@_serialized` moved into `scenes/locking.py`, all
    seventeen of its mutators read as unserialized — a decorator in another file
    could not enter the set whatever it did. The lock never changed; only which
    file it was written in did.

    Resolution is by relative import to a real file, and the sibling's `def` is
    then held to `_locks_anywhere` exactly as a local one is. The second half
    is the whole point: a sibling whose wrapper does NOT take the campaign lock
    vouches for nothing, so this recognizes another spelling of the property
    rather than trusting a name."""
    _f, serializing, mutators = _probe_sibling(tmp_path, SIBLING_USER)
    assert "put" in mutators
    assert "put" in serializing, "a decorator in a sibling module was invisible"

    unlocked = SIBLING_LOCK.replace("        with locks.campaign_lock(cid):\n", "")
    unlocked = unlocked.replace("            return", "        return")
    _f, serializing, _m = _probe_sibling(tmp_path, SIBLING_USER, unlocked)
    assert "put" not in serializing, "a sibling decorator that locks nothing vouched anyway"


def test_a_sibling_decorator_must_bind_this_campaign_to_its_target(tmp_path):
    """Everything a local decorator must satisfy, the sibling must too — this is
    `test_a_decorator_must_bind_this_campaign_to_its_target` one file over.
    `fn(sid, cid, *a)` sends the caller's `sid` to the target's first parameter,
    so two decorated calls for one campaign run under two different locks."""
    swapped = SIBLING_LOCK.replace("def locked(cid, *a, **kw):",
                                   "def locked(sid, cid, *a, **kw):") \
                          .replace("return fn(cid, *a, **kw)", "return fn(sid, cid, *a, **kw)")
    _f, serializing, mutators = _probe_sibling(
        tmp_path, SIBLING_USER.replace("def put(cid):", "def put(cid, sid):"), swapped)
    assert "put" in mutators
    assert "put" not in serializing, "a sibling decorator bound the wrong id to its target"


def test_a_sibling_that_rebinds_locks_vouches_for_nothing(tmp_path):
    """`_rebinds_locks` is asked of the SIBLING's tree, not only of the importer.
    A module whose `locks` is a parameter, an assignment or an import from
    somewhere that is not `grimoire.store.locks` has no known campaign lock to
    lend, and the importer cannot see that from its own file."""
    for sibling, why in [
        (SIBLING_LOCK.replace("from .. import locks\n", "from .hooks import locks\n"),
         "an import that is not the store's locks"),
        (SIBLING_LOCK.replace("def locked(cid, *a, **kw):", "def locked(cid, locks, *a, **kw):"),
         "a parameter shadowing the module"),
    ]:
        _f, serializing, mutators = _probe_sibling(tmp_path, SIBLING_USER, sibling)
        assert "put" in mutators
        assert "put" not in serializing, f"a sibling with {why} still vouched"


def test_only_a_resolved_sibling_may_vouch(tmp_path):
    """The extension resolves a receiver to a file; it does not trust a dotted
    spelling. Nothing that fails to resolve — no import at all, an absolute one
    that may name anything on `sys.path`, a name this module also binds itself —
    may put its decorator in `decorating`, which is the same "a name is not a
    binding" rule the rest of this file is built out of (`_rebinds_locks`,
    `_module_level`, `_shadowed_names`)."""
    for src, why in [
        (SIBLING_USER.replace("from . import locking\n", ""), "no import at all"),
        (SIBLING_USER.replace("from . import locking\n", "import locking\n"),
         "an absolute import"),
        (SIBLING_USER.replace("from . import locking\n",
                              "from . import locking\nlocking = contextlib\n"),
         "a module-scope rebinding"),
        (SIBLING_USER.replace("def put(cid):", "def put(cid, locking=None):"),
         "a parameter of that name"),
        (SIBLING_USER.replace("from . import locking\n",
                              "from . import locking\nfrom .other import locking\n"),
         "a second import of the same name"),
    ]:
        _f, serializing, mutators = _probe_sibling(tmp_path, src)
        assert "put" in mutators
        assert "put" not in serializing, f"{why} still resolved to the sibling"

    # ...and a sibling that merely RE-EXPORTS a decorator has no definition here
    # for `_locks_anywhere` to read, so resolution stops after one hop rather
    # than chaining through files this never opened.
    _f, serializing, _m = _probe_sibling(
        tmp_path, SIBLING_USER, "from .deeper import _serialized\n")
    assert "put" not in serializing, "a re-exported decorator was followed a second hop"


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


def test_an_import_of_locks_must_come_from_this_package():
    """`from hooks import locks` binds the name to a foreign module. The rule
    exempted any import whose source symbol was spelled `locks`, which is the
    spelling standing in for the binding one form further out — the same defect
    at its fourth site."""
    _f, serializing, mutators = _probe(
        "from hooks import locks\n"
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a foreign `locks` import was trusted"

    for src, why in [
        ("from . import locks\n", "a relative import"),
        ("from grimoire.store import locks\n", "an absolute import from this package"),
    ]:
        _f, serializing, _m = _probe(
            src + "def put(cid):\n"
            "    with locks.campaign_lock(cid):\n"
            "        atomic.write_text(p, x)\n")
        assert "put" in serializing, f"{why} was rejected"


def test_a_lambda_bound_to_a_name_is_a_mutator():
    """`put = lambda cid: atomic.write_text(p, x)` defines a public mutator
    with no `def` anywhere. The collector was keyed on `FunctionDef`, so this
    was not an unlocked mutator — it was no function at all, and potentially no
    module in the survey either."""
    funcs, serializing, mutators = _probe(
        "put = lambda cid: atomic.write_text(path, data)\n")
    assert "put" in funcs and "put" in mutators
    assert "put" not in serializing


def test_a_filename_is_not_a_mode():
    """The first FALSE POSITIVE this review produced, and worth a test of its
    own because it is the failure the guard's trade accepts. The builtin takes
    the PATH first, so scanning both positions read `open("a")` as append mode
    and reported a reader as an unlocked mutator."""
    for src, why in [
        ("def read(cid):\n    return open('a').read()\n", "a filename that spells a mode"),
        ("def read(cid):\n    return open('w').read()\n", "a filename that spells `w`"),
        ("def read(cid):\n    return open(p).read()\n", "an ordinary read"),
    ]:
        _f, _s, mutators = _probe(src)
        assert not mutators, f"{why} was read as a write ({src!r})"

    # ...while the real write forms still register, in both spellings.
    for src, why in [
        ("def put(cid):\n    open(p, 'w').write(x)\n", "builtin with a mode"),
        ("def put(cid):\n    p.open('w').write(x)\n", "Path.open, mode first"),
        ("def put(cid):\n    open(p, mode='a').write(x)\n", "keyword mode"),
    ]:
        _f, _s, mutators = _probe(src)
        assert mutators, f"{why} was missed ({src!r})"


def test_a_suspension_under_the_lock_is_not_serialization():
    """`_ProcessScopedLock` wraps a `threading.RLock`, which is owned by a
    thread, not a task. Two coroutines on one event-loop thread both "acquire"
    it across an await — the second re-enters, increments the depth, and skips
    the file lock — so the writes overlap while the guard passes.

    Nothing in this package holds a campaign lock inside an `async def` today;
    this fails loud for the one somebody writes later."""
    for src, why in [
        ("async def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        await flush()\n"
         "        atomic.write_text(p, x)\n", "await"),
        ("async def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        async with session() as s:\n"
         "            atomic.write_text(p, x)\n", "async with"),
        ("async def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        async for row in rows():\n"
         "            atomic.write_text(p, row)\n", "async for"),
    ]:
        _f, serializing, mutators = _probe(src)
        assert "put" in mutators
        assert "put" not in serializing, f"a {why} under the lock read as serialized"

    # An async function whose suspension is OUTSIDE the lock is still fine --
    # the rule is about the critical section, not about being async.
    _f, serializing, _m = _probe(
        "async def put(cid):\n"
        "    await flush()\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in serializing, "an await outside the lock was rejected"


ALIAS_SRC = ("from contextlib import contextmanager\n"
             "@contextmanager\n"
             "def helper(cid):\n"
             "    with locks.campaign_lock(cid):\n"
             "        yield\n")
DECORATOR_SRC = ("def safe(fn):\n"
                 "    def locked(cid, *a):\n"
                 "        with locks.campaign_lock(cid):\n"
                 "            return fn(cid, *a)\n"
                 "    return locked\n")


def test_an_async_comprehension_suspends():
    """`[f(x) async for x in src()]` suspends at every iteration, and Python
    records that as a FLAG on the comprehension clause rather than as an
    `AsyncFor` or an `Await` — so a list of node types could not see it. The
    suspension rule was right; the list of forms carrying a suspension was
    short, which is this branch's recurring shape."""
    _f, serializing, mutators = _probe(
        "async def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        [atomic.write_text(p, x) async for x in source()]\n")
    assert "put" in mutators
    assert "put" not in serializing, "an async comprehension read as uninterrupted"

    # A synchronous comprehension is not a suspension and stays covered.
    _f, serializing, _m = _probe(
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        [atomic.write_text(p, x) for x in source()]\n")
    assert "put" in serializing


def test_a_wrapper_may_not_rebind_the_campaign_it_locked():
    """Round eighteen stopped a body reassigning the id its lock was keyed on,
    on the ordinary path. A decorator's returned wrapper is a body too, and the
    check was not applied there — so the wrapper could lock `cid`, remap it, and
    hand the target a different campaign."""
    _f, serializing, mutators = _probe(
        "def safe(fn):\n"
        "    def locked(cid, *a):\n"
        "        with locks.campaign_lock(cid):\n"
        "            cid = remap(cid)\n"
        "            return fn(cid, *a)\n"
        "    return locked\n"
        "@safe\n"
        "def put(cid):\n"
        "    atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "the wrapper remapped the id it locked"


def test_delegation_to_a_deferred_callee_is_not_covered():
    """`with lock: return _write(cid)` where `_write` is an `async def` or a
    generator publishes nothing inside the block — the caller awaits or iterates
    the result once the lock is gone. The decorator path has rejected this shape
    since round fourteen; delegation classified the call by its POSITION alone
    and never asked what it was calling."""
    for callee, why in [
        ("async def _write(cid):\n    atomic.write_text(q, y)\n", "an async callee"),
        ("def _write(cid):\n    atomic.write_text(q, y)\n    yield 1\n", "a generator callee"),
    ]:
        _f, serializing, mutators = _probe(
            callee + "def put(cid):\n"
            "    with locks.campaign_lock(cid):\n"
            "        return _write(cid)\n")
        assert "put" in mutators
        assert "put" not in serializing, f"delegation to {why} read as covered"

    # An ordinary callee under the lock is still covered -- the rule is about
    # deferral, not about delegation.
    _f, serializing, _m = _probe(
        "def _write(cid):\n    atomic.write_text(q, y)\n"
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        return _write(cid)\n")
    assert "put" in serializing, "an ordinary delegated callee was rejected"


def test_a_decorated_body_may_not_delegate_to_a_deferred_callee():
    """The rule above, at the path that returned above it. `@safe def put(cid):
    return _write(cid)` with a deferred `_write` constructs a coroutine under the
    wrapper's lock and writes after it is released. Round twenty-three added the
    check to the undecorated path only — the sixth time on this branch that the
    defect was one path keeping an older answer to a question two paths ask."""
    dec = ("def safe(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n")
    for callee, why in [
        ("async def _write(cid):\n    atomic.write_text(q, y)\n", "an async callee"),
        ("def _write(cid):\n    atomic.write_text(q, y)\n    yield 1\n", "a generator callee"),
    ]:
        _f, serializing, mutators = _probe(
            dec + callee + "@safe\ndef put(cid):\n    return _write(cid)\n")
        assert "put" in mutators
        assert "put" not in serializing, f"a decorated body delegating to {why} was covered"

    # Merely binding the result is the same thing; it is the call that defers.
    _f, serializing, _m = _probe(
        dec + "async def _write(cid):\n    atomic.write_text(q, y)\n"
        "@safe\ndef put(cid):\n    task = _write(cid)\n    return task\n")
    assert "put" not in serializing, "a bound deferred delegate was covered"

    # An ordinary callee under the decorator is still covered.
    _f, serializing, _m = _probe(
        dec + "def _write(cid):\n    atomic.write_text(q, y)\n"
        "@safe\ndef put(cid):\n    _write(cid)\n")
    assert "put" in serializing, "an ordinary delegated callee was rejected"


def test_a_decorated_body_may_not_rebind_the_campaign_it_locked():
    """Round eighteen's rule, at the same path. The wrapper locks the id it was
    handed, so `@safe def put(cid, target): cid = target; write(...)` writes a
    campaign the wrapper never locked — two calls with different originals and
    one target write it while holding two different locks. The decorator branch
    returned above the check that says so."""
    dec = ("def safe(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n")
    for body, why in [
        ("    cid = target\n", "an assignment"),
        ("    match target:\n        case cid:\n            pass\n", "a match capture"),
    ]:
        _f, serializing, mutators = _probe(
            dec + "@safe\ndef put(cid, target):\n" + body
            + "    atomic.write_text(root / cid, x)\n")
        assert "put" in mutators
        assert "put" not in serializing, f"a decorated body rebinding cid by {why} was covered"

    _f, serializing, _m = _probe(
        dec + "@safe\ndef put(cid, target):\n    atomic.write_text(root / cid, x)\n")
    assert "put" in serializing, "a decorated body that rebinds nothing was rejected"


def test_one_list_of_binding_forms_serves_every_scan():
    """Four scans asked "what does this bind?" and each grew its own answer, so
    the SAME rebinding poisoned `cid` and not `locks`. `_rebinds_cid` knew
    `except ... as`, `match` captures and `global`; `_locally_bound` knew
    `import`; `_rebinds_locks` and `_module_level` knew neither. One list now,
    four callers — the branch's recurring defect, except that here the
    incomplete list was of binding FORMS and there were four copies of it."""
    write = "    with locks.campaign_lock(cid):\n        atomic.write_text(p, x)\n"
    for prefix, why in [
        ("def put(cid, source):\n    match source:\n        case locks:\n            pass\n",
         "a match capture"),
        ("def put(cid):\n    try:\n        pass\n    except Exception as locks:\n        pass\n",
         "an except-as"),
        ("def put(cid):\n    global locks\n", "a global declaration"),
    ]:
        _f, serializing, mutators = _probe(prefix + write)
        assert "put" in mutators
        assert "put" not in serializing, f"`locks` rebound by {why} was still trusted"

    # A `def` or `class` of that name binds it as surely as `=` does.
    for defn, why in [("def locks():\n    pass\n", "a def"),
                      ("class locks:\n    pass\n", "a class")]:
        _f, serializing, _m = _probe(defn + "def put(cid):\n" + write)
        assert "put" not in serializing, f"`locks` shadowed by {why} was still trusted"

    # The same forms against a module-level alias, which is the other whitelist
    # they were invisible to.
    for rebind, why in [
        ("try:\n    pass\nexcept Exception as helper:\n    pass\n", "an except-as"),
        ("match source:\n    case helper:\n        pass\n", "a match capture"),
    ]:
        _f, serializing, _m = _probe(
            ALIAS_SRC + rebind + "def put(cid):\n"
            "    with helper(cid):\n        atomic.write_text(p, x)\n")
        assert "put" not in serializing, f"the alias survived {why} at module scope"

    # ...and a nested `def` of the alias's name inside a body shadows it there.
    _f, serializing, _m = _probe(
        ALIAS_SRC + "def put(cid):\n    def helper(c):\n        return nullcontext()\n"
        "    with helper(cid):\n        atomic.write_text(p, x)\n")
    assert "put" not in serializing, "a nested def did not shadow the module alias"

    # The loud side of the trade has a bound: legitimate module-level control
    # flow around definitions and unrelated names must still resolve.
    for benign, why in [
        ("if True:\n    def other(cid):\n        pass\n", "a conditional definition"),
        ("try:\n    import json\nexcept ImportError:\n    json = None\n", "an import guard"),
        ("match source:\n    case other:\n        pass\n", "an unrelated capture"),
    ]:
        _f, serializing, _m = _probe(
            ALIAS_SRC + benign + "def put(cid):\n"
            "    with helper(cid):\n        atomic.write_text(p, x)\n")
        assert "put" in serializing, f"{why} poisoned an unrelated alias"


def test_a_class_rebinds_its_name():
    """`class helper(nullcontext): pass` after a locking `def helper` replaces
    it. The scan skipped the whole `ClassDef` — correctly for its body, which is
    a new scope, and wrongly for its NAME. The last module-scope binding form
    after the loop, with-as, import and walrus cases."""
    _f, serializing, mutators = _probe(
        "def helper(cid):\n"
        "    return locks.campaign_lock(cid)\n"
        "class helper(contextlib.nullcontext):\n"
        "    pass\n"
        "def put(cid):\n"
        "    with helper(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a class did not poison the name it binds"


def test_a_dunder_is_public_whatever_its_underscores():
    """`obj[cid] = value` reaches `__setitem__`. The private-helper skip is a
    prefix test, so a module whose only writer was an unlocked protocol method
    disappeared from the survey entirely."""
    src = ("class Store:\n"
           "    def __setitem__(self, cid, value):\n"
           "        atomic.write_text(p, value)\n")
    probe_file = PACKAGE / "store" / "_lock_domain_probe.py"
    probe_file.write_text(src, encoding="utf-8")
    try:
        unserialized, mutators = _analyze(probe_file)
    finally:
        probe_file.unlink()
    assert "__setitem__" in mutators, "a public protocol writer was skipped as private"
    assert "__setitem__" in unserialized


def test_a_decorated_body_may_not_defer_its_write_into_a_genexp():
    """`@_serialized def put(cid): return (write(x) for x in xs)` constructs the
    generator under the lock and writes after it. `_under_lock` learned this in
    round twenty-one, but the decorator path returns before that traversal ever
    runs — and a `GeneratorExp` carries no `Yield` for the generator check."""
    dec = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n")
    _f, serializing, mutators = _probe(
        dec + "@_serialized\ndef put(cid):\n"
        "    return (atomic.write_text(p, x) for x in xs)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a write deferred into a genexp read as locked"

    # Narrow on purpose: `scenes.append_reply` is `@_serialized` and uses a
    # generator expression for something harmless, so only a genexp CONTAINING
    # a write is rejected.
    _f, serializing, _m = _probe(
        dec + "@_serialized\ndef put(cid):\n"
        "    if any(x for x in xs):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in serializing, "a harmless genexp under the decorator was rejected"


def test_a_decorated_body_may_not_defer_its_write_at_all():
    """A generator expression was one deferral form out of three. A lambda and a
    nested `def` handed back to the caller do the same thing and were missed,
    because round twenty-one asked "is it deferred THIS way?" rather than "does
    any of this writing run under the lock?" — the recurring shape on this
    branch, an enumeration standing in for the property."""
    dec = ("def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n")
    for body, why in [
        ("    return lambda: atomic.write_text(p, x)\n", "a lambda"),
        ("    def later():\n        atomic.write_text(p, x)\n    return later\n",
         "a nested def"),
    ]:
        _f, serializing, mutators = _probe(dec + "@_serialized\ndef put(cid):\n" + body)
        assert "put" in mutators
        assert "put" not in serializing, f"a write deferred into {why} read as locked"

    # A body that writes directly is untouched, whatever it also constructs.
    _f, serializing, _m = _probe(
        dec + "@_serialized\ndef put(cid):\n"
        "    atomic.write_text(p, x)\n"
        "    return lambda: read(p)\n")
    assert "put" in serializing, "a direct write beside a harmless lambda was rejected"


def test_an_outer_decorator_may_unwrap_the_lock():
    """Round fourteen accepted a chain whose innermost decorator locks, on the
    reasoning that anything further out "wraps an already-serialized callable and
    cannot un-serialize it". `scenes._serialized` uses `functools.wraps`, which
    sets `__wrapped__`, so an outer decorator reaches straight past the lock to
    the raw body. The rule was right and the sentence justifying its reach was
    never checked against the code — the fifth defect of that shape here."""
    src = ("def bypass(fn):\n"
           "    return fn.__wrapped__\n"
           "def _serialized(fn):\n"
           "    def locked(cid, *a):\n"
           "        with locks.campaign_lock(cid):\n"
           "            return fn(cid, *a)\n"
           "    return locked\n"
           "@bypass\n@_serialized\ndef put(cid):\n"
           "    atomic.write_text(p, x)\n")
    _f, serializing, mutators = _probe(src)
    assert "put" in mutators
    assert "put" not in serializing, "an outer decorator unwrapped the lock unnoticed"

    # The single decorator still resolves; no `scenes` mutator has more than one.
    _f, serializing, _m = _probe(src.replace("@bypass\n", ""))
    assert "put" in serializing, "the lone locking decorator was rejected"


def test_a_module_scope_walrus_rebinds():
    """`if (helper := nullcontext): pass` at module level rebinds `helper` for
    the whole module. The comment excluding walrus said it "binds inside an
    expression, never at the module scope these callers care about" — a factual
    claim of mine, and wrong. The fourth time on this branch that the defect was
    a sentence rather than the code."""
    _f, serializing, mutators = _probe(
        ALIAS_SRC + "if (helper := nullcontext):\n    pass\n"
        "def put(cid):\n"
        "    with helper(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a module-scope walrus did not poison the alias"


def test_a_generator_expression_defers_its_writes():
    """`return (write(x) for x in xs)` runs when the caller iterates it — after
    the function returned and the lock went away. A generator expression has no
    `Yield` node for the suspension check to see, so the traversal has to treat
    it as deferred, exactly as it treats a lambda."""
    _f, serializing, mutators = _probe(
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        return (atomic.write_text(p, i) for i in items)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a write deferred into a genexp read as locked"

    # A list comprehension is EAGER and must still be descended into -- both
    # directions, since round fifteen relies on catching an unlocked one.
    _f, serializing, _m = _probe(
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        [atomic.write_text(p, i) for i in items]\n")
    assert "put" in serializing, "an eager comprehension inside the lock was rejected"
    _f, serializing, _m = _probe(
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        pass\n"
        "    [atomic.write_text(p, i) for i in items]\n")
    assert "put" not in serializing, "an eager comprehension outside the lock passed"


def test_the_campaign_id_may_be_passed_by_keyword():
    """`campaign_lock` declares an ordinary `cid` parameter, so
    `campaign_lock(cid=cid)` is the same acquisition. Rejecting it would have
    forced valid code to be rewritten or exempted — the second false positive
    this review produced, and like the first it costs a reader real code rather
    than letting a race through."""
    for src, why in [
        ("def put(cid):\n"
         "    with locks.campaign_lock(cid=cid):\n"
         "        atomic.write_text(p, x)\n", "the direct acquisition"),
        ("from contextlib import contextmanager\n"
         "@contextmanager\n"
         "def helper(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        yield\n"
         "def put(cid):\n"
         "    with helper(cid=cid):\n"
         "        atomic.write_text(p, x)\n", "an alias"),
    ]:
        _f, serializing, _m = _probe(src)
        assert "put" in serializing, f"{why} by keyword was rejected"

    # ...and the keyword must still carry THIS campaign.
    _f, serializing, _m = _probe(
        "def put(cid, sid):\n"
        "    with locks.campaign_lock(cid=sid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" not in serializing, "a keyword bound to another id was accepted"


def test_destructuring_binds_a_public_name():
    """`put, other = _put, helper` binds through an `ast.Tuple` target, which
    `_bindings` did not read — so the public name was never collected, the
    private writer was skipped as private, and the module could leave the
    survey entirely."""
    funcs, _s, mutators = _probe(
        "def _put(cid):\n    atomic.write_text(p, x)\n"
        "def helper(cid):\n    pass\n"
        "put, other = _put, helper\n")
    assert "put" in funcs and "put" in mutators

    # An unpacking this cannot pair resolves to nothing, which poisons rather
    # than silently aliasing.
    _f, serializing, _m = _probe(
        ALIAS_SRC + "helper, spare = pair()\n"
        "def put(cid):\n"
        "    with helper(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" not in serializing


def test_cid_may_be_rebound_without_an_ast_name():
    """`except Redirect as cid` and a `match` capture store the bound name as a
    plain string on the node, so the Name-only scan could not see them. The
    binding forms named as still-unenumerated two rounds ago."""
    for src, why in [
        ("def put(cid):\n"
         "    with locks.campaign_lock(cid):\n"
         "        try:\n"
         "            go()\n"
         "        except Redirect as cid:\n"
         "            atomic.write_text(root / cid, x)\n", "except-as"),
        ("def put(cid, msg):\n"
         "    with locks.campaign_lock(cid):\n"
         "        match msg:\n"
         "            case {'to': cid}:\n"
         "                atomic.write_text(root / cid, x)\n", "a match capture"),
        ("def put(cid):\n"
         "    global cid\n"
         "    with locks.campaign_lock(cid):\n"
         "        atomic.write_text(p, x)\n", "a global declaration"),
    ]:
        _f, serializing, _m = _probe(src)
        assert "put" not in serializing, f"{why} rebound `cid` unnoticed"


def test_a_mode_this_cannot_read_is_not_read_only():
    """`mode = "w"; open(path, mode)` published while the guard saw no write,
    and the handle's `.write()` is not matched either — so the module vanished
    from the survey. The builtin's second argument is unambiguously the mode,
    so a non-literal there fails closed."""
    for src, why in [
        ("def put(cid):\n    mode = 'w'\n    open(path, mode).write(x)\n", "a variable"),
        ("def put(cid):\n    open(path, mode=chosen()).write(x)\n", "a computed keyword"),
    ]:
        _f, _s, mutators = _probe(src)
        assert mutators, f"{why} mode was assumed read-only ({src!r})"

    # An attribute call keeps the literal-only rule: `p.open(x)` and
    # `codecs.open(x, "w")` put different things in position 0, and guessing
    # there costs false alarms -- the cost this guard already paid once.
    _f, _s, mutators = _probe("def read(cid):\n    return p.open(chosen()).read()\n")
    assert not mutators


def test_a_target_invoked_from_a_callback_is_not_covered():
    """`_every_one_locked` does not descend into nested defs, because code there
    runs later — so a target invocation registered as a callback was invisible
    while the one direct call answered for both."""
    _f, serializing, mutators = _probe(
        "def safe(fn):\n"
        "    def locked(cid, *a):\n"
        "        with locks.campaign_lock(cid):\n"
        "            fn(cid, *a)\n"
        "        def later():\n"
        "            fn(cid, *a)\n"
        "        register(later)\n"
        "    return locked\n"
        "@safe\n"
        "def put(cid):\n"
        "    atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a deferred target invocation was invisible"


def test_a_decorated_alias_is_not_its_undecorated_body():
    """A decorator replaces what the name means, and this analyzed the body it
    was applied to. Only `@contextmanager` is recognized — the one decorator
    whose semantics `_produces_lock` already models."""
    _f, serializing, mutators = _probe(
        "@replace_with_nullcontext\n"
        "def helper(cid):\n"
        "    return locks.campaign_lock(cid)\n"
        "def put(cid):\n"
        "    with helper(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a transformed alias kept its old meaning"

    # Both spellings of the recognized decorator still qualify.
    for dec in ("from contextlib import contextmanager\n@contextmanager\n",
                "import contextlib\n@contextlib.contextmanager\n"):
        _f, serializing, _m = _probe(
            dec + "def helper(cid):\n"
            "    with locks.campaign_lock(cid):\n"
            "        yield\n"
            "def put(cid):\n"
            "    with helper(cid):\n"
            "        atomic.write_text(p, x)\n")
        assert "put" in serializing, f"the real @contextmanager alias regressed ({dec!r})"


def test_a_function_local_rebinding_poisons_the_alias():
    """`_shadowed_names` answers module-wide and for parameters only, because
    poisoning every name any function assigns would poison half the module. A
    mutator writing `helper = nullcontext` in its OWN body is still not calling
    the module-level `helper`, and module-scope analysis deliberately stops at
    function bodies — so nothing looked. Answered per function, where it is
    free."""
    _f, serializing, mutators = _probe(
        ALIAS_SRC +
        "def put(cid):\n"
        "    helper = nullcontext\n"
        "    with helper(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a local rebinding kept the module alias"


def test_the_locks_import_must_name_the_store_module():
    """Package membership is not identity. `from .hooks import locks` is
    relative and is not the store's lock; `from grimoire import store` is
    absolute and IS the store. An origin-shaped rule got both wrong in opposite
    directions, so the rule is the dotted path the import binds."""
    for src, why in [
        ("from .hooks import locks\n", "a package-local module that is not store"),
        ("from grimoire.hooks import locks\n", "an absolute path inside the package"),
        ("import json as locks\n", "an unrelated module"),
    ]:
        _f, serializing, mutators = _probe(
            src + "def put(cid):\n"
            "    with locks.campaign_lock(cid):\n"
            "        atomic.write_text(p, x)\n")
        assert "put" in mutators
        assert "put" not in serializing, f"`locks` from {why} was trusted"

    # ...and each spelling is judged against the package doing the importing,
    # which is what makes `from . import locks` right in `store/` and wrong in
    # `store/weather/`. Both real spellings in this package are here.
    for src, package, why in [
        ("from . import locks\n", "store", "the store-internal form"),
        ("from .store import locks\n", "", "a module above the store package"),
        ("from ..store import locks\n", "routes", "a sibling package"),
        ("from grimoire.store import locks\n", "store", "the absolute form"),
    ]:
        _f, serializing, _m = _probe(
            src + "def put(cid):\n"
            "    with locks.campaign_lock(cid):\n"
            "        atomic.write_text(p, x)\n", package)
        assert "put" in serializing, f"{why} was rejected"

    # The same relative spelling in a NESTED package names a sibling module,
    # not the store's lock.
    _f, serializing, _m = _probe(
        "from . import locks\n"
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n", "store.weather")
    assert "put" not in serializing, "a relative import was not resolved against its package"


def test_a_dotted_import_bound_as_locks_is_still_judged_by_its_path():
    """`import a.b as locks` binds the name through `Import`, not `ImportFrom`,
    and that branch only asked whether the bound name was `locks` — it never
    asked what module it named. So `import hooks.store.locks as locks` was
    trusted on the strength of its own alias, which is exactly the "package
    membership is not identity" defect the `ImportFrom` branch had fixed one
    statement form along."""
    _f, serializing, mutators = _probe(
        "import hooks.store.locks as locks\n"
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a foreign dotted module was trusted as `locks`"

    _f, serializing, _m = _probe(
        "import grimoire.store.locks as locks\n"
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in serializing, "the real module's absolute path was rejected"


def test_a_function_local_import_shadows_the_alias():
    """`_locally_bound` read parameters and `Name` stores, so a body rebinding a
    validated alias by ASSIGNMENT was caught while the same rebinding by IMPORT
    was not — `from contextlib import nullcontext as helper` binds through
    `ast.alias`, which nothing looked at. One rule, one binding form short."""
    _f, serializing, mutators = _probe(
        ALIAS_SRC +
        "def put(cid):\n"
        "    from contextlib import nullcontext as helper\n"
        "    with helper(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a local import kept the module alias"


def test_a_delegated_call_must_be_module_local():
    """`hooks._write(cid)` is not this module's `_write`, and reading the
    trailing attribute made it count as delegation to a serialized helper, an
    atomic unit that stops mutation propagation, and exempt from the position
    check — all three at once. The caller was then reported by neither guard:
    the foreign module skips its own private helper as private."""
    _f, serializing, mutators = _probe(
        "def _write(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(q, y)\n"
        "def put(cid):\n"
        "    hooks._write(cid)\n")
    assert "put" not in serializing, "a foreign call borrowed the local helper's lock"

    _f, serializing, _m = _probe(
        "def _write(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(q, y)\n"
        "def put(cid):\n"
        "    _write(cid)\n")
    assert "put" in serializing, "the module-local call was rejected"


def test_a_decorator_wrapper_may_not_suspend_under_the_lock():
    """Round fourteen rejected an async decorated TARGET. A decorator whose
    returned WRAPPER awaits inside the lock has the same defect — the RLock is
    thread-owned, so another coroutine re-enters during the await — and reached
    the check from the other side."""
    _f, serializing, mutators = _probe(
        "def safe(fn):\n"
        "    async def locked(cid, *a):\n"
        "        with locks.campaign_lock(cid):\n"
        "            await flush()\n"
        "            return fn(cid, *a)\n"
        "    return locked\n"
        "@safe\n"
        "def put(cid):\n"
        "    atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "an async wrapper suspending under the lock passed"


def test_a_body_may_not_rebind_the_campaign_it_locked():
    """Every rule here is keyed on the NAME `cid` meaning this function's
    campaign — a claim about the parameter that stops being true the moment the
    body reassigns it. Two calls with different originals and the same target
    then write one campaign under two different locks."""
    _f, serializing, mutators = _probe(
        "def put(cid, target):\n"
        "    with locks.campaign_lock(cid):\n"
        "        cid = target\n"
        "        atomic.write_text(root / cid, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "the body rebound the id the lock was keyed on"

    _f, serializing, _m = _probe(
        "def put(cid, target):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(root / cid, x)\n")
    assert "put" in serializing


def test_loop_and_with_targets_rebind_module_scope():
    """`=`, `import` and parameters were treated as bindings; `for x in ...`
    and `with ... as x` were not, though they bind at module scope just as
    surely. The predicted next binding form, at the predicted place."""
    for rebind, why in [
        ("for helper in [nullcontext]:\n    pass\n", "a loop target"),
        ("with ctx() as helper:\n    pass\n", "a with-as target"),
    ]:
        _f, serializing, mutators = _probe(
            ALIAS_SRC + rebind +
            "def put(cid):\n"
            "    with helper(cid):\n"
            "        atomic.write_text(p, x)\n")
        assert "put" in mutators
        assert "put" not in serializing, f"{why} did not poison the alias"


def test_poisoning_propagates_along_an_alias_chain():
    """`safe = hooks.identity; alias = safe` revived the rebound name: `safe`
    was in both the resolved set and the poisoned one, and the alias resolution
    read the wrong one."""
    _f, serializing, mutators = _probe(
        DECORATOR_SRC +
        "safe = hooks.identity\n"
        "alias = safe\n"
        "@alias\n"
        "def put(cid):\n"
        "    atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a poisoned name was revived by aliasing it"


def test_an_alias_reached_through_an_attribute_is_not_the_local_one():
    """`hooks.helper(cid)` is not this module's `helper`. The decorator path was
    narrowed to bare names in round ten; the alias path was not."""
    _f, serializing, mutators = _probe(
        ALIAS_SRC +
        "def put(cid):\n"
        "    with hooks.helper(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a foreign context manager borrowed the local alias"

    _f, serializing, _m = _probe(
        ALIAS_SRC +
        "def put(cid):\n"
        "    with helper(cid):\n"
        "        atomic.write_text(p, x)\n")
    assert "put" in serializing, "the local alias was rejected"


def test_a_yield_under_the_lock_is_a_suspension():
    """A generator resumes when its caller iterates it, and a second generator
    on the same thread re-enters the thread-owned RLock in between. The
    decorated-generator shape was rejected in round fourteen; one that takes the
    lock directly reached the suspension check, which listed only async forms."""
    _f, serializing, mutators = _probe(
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        data = read(p)\n"
        "        yield data\n"
        "        atomic.write_text(p, data)\n")
    assert "put" in mutators
    assert "put" not in serializing, "a yield under the lock read as serialized"


def test_a_namespace_bound_by_assignment_is_resolved():
    """Round sixteen resolved `import shutil as fs`; `import shutil; fs = shutil`
    binds the module OBJECT, which the import scan cannot see. Same consequence
    — `copytree` is in no enumeration, so the module left the survey."""
    for src, why in [
        ("import shutil\nfs = shutil\ndef put(cid):\n    fs.copytree(a, b)\n", "shutil"),
        ("import os\nsys = os\nalias = sys\ndef put(cid):\n    alias.truncate(p, 0)\n",
         "a chain of assignments"),
    ]:
        _f, _s, mutators = _probe(src)
        assert mutators, f"a namespace bound from {why} was invisible ({src!r})"


def test_a_delegated_write_must_be_positioned_too():
    """A function mutates two ways — by writing, and by calling something that
    writes — and both have to sit inside the lock.

    Round fifteen made the WRITES' position matter and then returned, so a
    locked `atomic.write_text(...)` followed by an unlocked `_unsafe(cid)` was
    accepted. And because `_analyze` skips private helpers, the delegate had
    nowhere left to be reported: neither check named it. The finding is in the
    code the previous round added, for the fourth round running."""
    _f, serializing, mutators = _probe(
        "def _unsafe(cid):\n"
        "    atomic.write_text(q, y)\n"
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n"
        "    _unsafe(cid)\n")
    assert "put" in mutators, "reported by neither check"
    assert "put" not in serializing, "an unlocked delegate after a locked write passed"

    # The SAME helper called inside the block is covered by the caller's lock --
    # position is the rule, not the callee's own serialization.
    _f, serializing, _m = _probe(
        "def _unsafe(cid):\n"
        "    atomic.write_text(q, y)\n"
        "def put(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(p, x)\n"
        "        _unsafe(cid)\n")
    assert "put" in serializing, "a delegate inside the lock was rejected"

    # A delegate that serializes on this campaign itself is an atomic unit
    # wherever it is called, which is how `scenes.create_scene` works.
    _f, serializing, _m = _probe(
        "def _w(cid):\n"
        "    with locks.campaign_lock(cid):\n"
        "        atomic.write_text(q, y)\n"
        "def put(cid):\n"
        "    _w(cid)\n")
    assert "put" in serializing, "delegation to a locked helper was rejected"


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
        # The lock nested INSIDE another `with`. This was a false alarm on
        # legitimate code until the traversal was made uniform: a `with` reached
        # as a body statement never had its own context evaluated.
        ("def put(cid):\n"
         "    with suppress(OSError):\n"
         "        with locks.campaign_lock(cid):\n"
         "            atomic.write_text(p, x)\n", "a lock inside an unrelated with"),
        ("def put(cid):\n"
         "    with open(log) as f, locks.campaign_lock(cid):\n"
         "        atomic.write_text(p, x)\n", "a lock beside another context manager"),
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

    # Swapping them puts the locking decorator innermost, which round fourteen
    # accepted on the reasoning that an outer decorator "cannot un-serialize"
    # what it wraps. It can -- see `test_an_outer_decorator_may_unwrap_the_lock`
    # -- so a chain of any length is now refused whichever way it is ordered.
    _f, serializing, _m = _probe(chain.replace("@safe\n@defer\n", "@defer\n@safe\n"))
    assert "put" not in serializing, "a two-decorator chain was resolved"

    # The single decorator is still recognized; the rule narrowed, not broke.
    _f, serializing, _m = _probe(chain.replace("@safe\n@defer\n", "@safe\n"))
    assert "put" in serializing, "a locking decorator applied alone was rejected"


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
