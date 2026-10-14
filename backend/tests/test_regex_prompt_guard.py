"""Guard: every LLM reader of transcript text reads the prompt view (regex spec 5.2).

Output-processing rules (`store/regex/`) change what a model is shown, never
what is stored, so each reader has to ask for the view itself:
`chronicle.transcript_text` deliberately applies no rules, because the exports
call it too and want the display phase. A reader that skips the view does not
fail -- it quietly shows the model the `<think>` block a rule was written to
strip, and an absorb then judges its citations against text the model never
saw. Held against the AST of `routes/`, `store/context/`, `store/searchdocs/`
and `store/history/` (roadmap 08 and 09; absent until 09, scanned harmlessly),
in the style of `test_routing_guard.py`:

- "a call to `view(`" below means the regex view by its attribute chain --
  `regex_view.view(...)` or `<...>regex.view.view(...)` -- with the keyword
  `phase="prompt"`. Another module's `view` (`overlay.view`) is not it, and
  neither is the display phase, which a reader could otherwise ask for and
  pass while showing the model the screen's text;
- every `transcript_text(...)` call renders a list bound by a call to `view(`
  in the same function, a slice of one (`shown[start:end]`), or the
  `view(...)` call itself;
- every `absorb.materialize(...)` call judges citations against a viewed list
  (a name bound by `view(`, or a `.shown` snapshot), and the absorb snapshot's
  `shown` is bound by `view(`;
- the readers that build a prompt from posts without `transcript_text` -- the
  turn prompt, the response selector, the tracker update, the passage draft --
  are pinned by name and must reach `view(` directly or through a helper in
  their own module; a pinned name that no longer exists fails, so a rename
  cannot drop one silently;
- any other function that reads a scene, reads a message's `content` and
  renders a prompt template in the same body must reach `view(` too. That is
  how a NEW reader is caught;
- every `expand(...)` call (`store/searchdocs/expand.py`, roadmap 08-C3b) in a
  function that renders a prompt template, or in a pinned reader, passes the
  constant `phase="prompt"`. `expand` reads the transcript itself and takes
  its phase from the caller, so none of the rules above can see a reader that
  renders a prompt from its excerpts: the display phase, or a phase held in a
  variable, would pass all of them.

Honest about its reach:

- **It sees names, not data flow.** A list bound by `view(` and then never
  passed on, or a viewed list sliced back to the raw one, passes. What it
  catches is the reader that never asked.
- **The last rule is a heuristic.** A reader that reads the scene in one
  function and renders the prompt in another is invisible to it unless it is
  pinned -- the tracker update is exactly that shape, which is why it is pinned.
- **`store/export.py` is not scanned**: exports read the display phase, not
  this one.

A reader that genuinely must see raw text carries `# regex-ok: <reason>` on the
call (or, for a function rule, on its `def`). A marker with no reason fails,
and at most three exist.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire

from . import guard_markers

PACKAGE = pathlib.Path(grimoire.__file__).parent
MARKER = "regex-ok"
MAX_MARKERS = 3

#: Readers that turn posts into prompt text without `transcript_text`.
PINNED = {
    "store/context/assemble.py": ("_assemble",),
    "store/history/query.py": ("prepare",),
    "routes/character_turns.py": ("_selector_inputs", "_turn_inputs"),
    "routes/tracker.py": ("_locate", "_context_posts"),
    "routes/passage_characters.py": ("draft_character",),
}

# Readers whose transcript view belongs to 08, rather than to their own body.
EXPANSION_PINNED = {"store/history/expand.py": ("excerpts",)}

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCENE_READS = {"read_scene", "_require_scene"}
_PROMPT_BUILDS = {"render"}


#: Scanned directories, relative to the package. `store/history/` is 09's and
#: does not exist yet; a missing directory contributes nothing.
SCANNED = ("routes", "store/context", "store/searchdocs", "store/history")


def _sources():
    files = [f for d in SCANNED for f in sorted((PACKAGE / d).glob("*.py"))]
    for path in files:
        yield path.relative_to(PACKAGE).as_posix(), path.read_text(encoding="utf-8")


def _name(call: ast.Call) -> str:
    func = call.func
    return (func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute) else "")


def _calls(node: ast.AST, name: str | None = None):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and (name is None or _name(sub) == name):
            yield sub


def _is_prompt_view(node: ast.AST) -> bool:
    """`node` is `regex_view.view(..., phase="prompt")` or
    `<...>regex.view.view(..., phase="prompt")`."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "view"):
        return False
    owner = node.func.value
    regex_view = (isinstance(owner, ast.Name) and owner.id == "regex_view") or (
        isinstance(owner, ast.Attribute) and owner.attr == "view"
        and ((isinstance(owner.value, ast.Name) and owner.value.id == "regex")
             or (isinstance(owner.value, ast.Attribute) and owner.value.attr == "regex")))
    phase = next((k.value for k in node.keywords if k.arg == "phase"), None)
    return regex_view and isinstance(phase, ast.Constant) and phase.value == "prompt"


def _views(node: ast.AST):
    return (sub for sub in ast.walk(node) if _is_prompt_view(sub))


def _functions(tree: ast.AST):
    return [n for n in ast.walk(tree) if isinstance(n, _FUNCTIONS)]


def _owner(fns, node: ast.AST):
    """The innermost function whose body holds `node`."""
    best = None
    for fn in fns:
        if fn.lineno <= node.lineno <= fn.end_lineno and (best is None or fn.lineno >= best.lineno):
            best = fn
    return best


def _bound_by_view(fn) -> set[str]:
    """Names assigned from a `view(...)` call in `fn`."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None \
                and _is_prompt_view(node.value):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            out |= {t.id for t in targets if isinstance(t, ast.Name)}
    return out


def _viewed(arg, fn, *, snapshot: bool = False) -> bool:
    if _is_prompt_view(arg):
        return True
    if isinstance(arg, ast.Name) and fn is not None and arg.id in _bound_by_view(fn):
        return True
    # A slice of a viewed list is still the view: `shown[start:end]`.
    if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name) \
            and fn is not None and arg.value.id in _bound_by_view(fn):
        return True
    return snapshot and isinstance(arg, ast.Attribute) and arg.attr == "shown"


def _reads_content(fn) -> bool:
    """`fn` reads a message's text: `m["content"]` or `m.get("content")`."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                and node.slice.value == "content":
            return True
        if isinstance(node, ast.Call) and _name(node) == "get" and node.args \
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "content":
            return True
    return False


def _reaches_view(fn, helpers: dict[str, ast.AST]) -> bool:
    """`fn` calls `view(`, or calls a function of its own module that does."""
    if any(True for _ in _views(fn)):
        return True
    return any(_name(c) in helpers and any(True for _ in _views(helpers[_name(c)]))
               for c in _calls(fn))


def _expand_bindings(tree: ast.AST) -> tuple[set[str], set[str]]:
    """(names bound to the `searchdocs.expand` module, names bound to its
    `expand` function), resolved from the file's own imports so an alias
    (`from ..searchdocs import expand as history_expand`) is held like the
    plain spelling. `expand` itself always counts as the module's name."""
    modules, functions = {"expand"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = node.module or ""
            for alias in node.names:
                bound = alias.asname or alias.name
                if source.split(".")[-1] == "searchdocs" and alias.name == "expand":
                    modules.add(bound)
                elif source.endswith("searchdocs.expand") and alias.name == "expand":
                    functions.add(bound)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("searchdocs.expand") and alias.asname:
                    modules.add(alias.asname)
    return modules, functions


def _is_searchdocs_expand(call: ast.Call, bindings: tuple[set[str], set[str]]) -> bool:
    """A call of `searchdocs.expand.expand`: through a name bound to the module
    (`expand.expand(...)`, `history_expand.expand(...)`), through the
    attribute chain (`<...>.searchdocs.expand.expand(...)`), or through a name
    bound to the function itself. Any other bare `expand(...)` is someone
    else's -- the context builder's macro expansion is a local callable of
    that name."""
    modules, functions = bindings
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in functions
    if not (isinstance(func, ast.Attribute) and func.attr == "expand"):
        return False
    owner = func.value
    return (isinstance(owner, ast.Name) and owner.id in modules) or (
        isinstance(owner, ast.Attribute) and owner.attr == "expand"
        and isinstance(owner.value, ast.Attribute) and owner.value.attr == "searchdocs")


def _is_absorb_materialize(call: ast.Call) -> bool:
    # `store.absorb.materialize`, not `store.audit.materialize` (no citations).
    return (isinstance(call.func, ast.Attribute) and call.func.attr == "materialize"
            and isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "absorb")


class _File:
    """One scanned module, and the rule checks run over it."""

    def __init__(self, rel: str, text: str, markers: list[str]):
        self.rel, self.text, self.markers = rel, text, markers
        self.tree = ast.parse(text)
        self.fns = _functions(self.tree)
        self.helpers = {f.name: f for f in self.fns}

    def cleared(self, node, others=()) -> bool:
        """Whether a `# regex-ok:` marker exempts `node` (recorded either way,
        so an empty reason still counts against the cap and fails)."""
        reason = guard_markers.marker_reason(MARKER, self.text, node, others)
        if reason is None:
            return False
        self.markers.append(reason)
        return bool(reason)

    def offences(self):
        yield from self._transcripts()
        yield from self._citations()
        yield from self._pinned()
        yield from self._expansion_pinned()
        yield from self._new_readers()
        yield from self._expansions()

    def _transcripts(self):
        flagged = list(_calls(self.tree, "transcript_text"))
        for call in flagged:
            if call.args and _viewed(call.args[0], _owner(self.fns, call)):
                continue
            if not self.cleared(call, [c for c in flagged if c is not call]):
                yield (f"{self.rel}:{call.lineno}: transcript_text renders a list "
                       "that did not come from regex view()")

    def _citations(self):
        for call in filter(_is_absorb_materialize, _calls(self.tree, "materialize")):
            arg = call.args[3] if len(call.args) > 3 else next(
                (k.value for k in call.keywords if k.arg == "messages"), None)
            if arg is not None and _viewed(arg, _owner(self.fns, call), snapshot=True):
                continue
            if not self.cleared(call):
                yield (f"{self.rel}:{call.lineno}: absorb citations are judged against "
                       "messages that did not come from regex view()")
        for call in _calls(self.tree, "_Prepared"):
            shown = next((k.value for k in call.keywords if k.arg == "shown"), None)
            if shown is not None and _viewed(shown, _owner(self.fns, call)):
                continue
            if not self.cleared(call):
                yield (f"{self.rel}:{call.lineno}: the absorb snapshot's `shown` "
                       "is not bound by regex view()")

    def _pinned(self):
        for name in PINNED.get(self.rel, ()):
            fn = self.helpers.get(name)
            if fn is None:
                yield (f"{self.rel}: pinned reader {name} no longer exists -- "
                       "re-pin the function that now reads the posts")
            elif not _reaches_view(fn, self.helpers) and not self.cleared(fn):
                yield (f"{self.rel}:{fn.lineno}: {name} reads posts into a prompt "
                       "without regex view()")

    def _expansion_pinned(self):
        # No fake view call: the adapter must reach the actual 08 door, and
        # _expansions below independently checks every call's prompt phase.
        bindings = _expand_bindings(self.tree)
        for name in EXPANSION_PINNED.get(self.rel, ()):
            fn = self.helpers.get(name)
            if fn is None:
                yield f"{self.rel}: pinned expansion reader {name} no longer exists"
            elif not any(_is_searchdocs_expand(c, bindings) for c in _calls(fn)):
                yield f"{self.rel}:{fn.lineno}: {name} never calls searchdocs.expand"

    def _new_readers(self):
        for fn in self.fns:
            if fn.name in PINNED.get(self.rel, ()):
                continue
            reads = any(_name(c) in _SCENE_READS for c in _calls(fn)) and _reads_content(fn)
            builds = any(_name(c) in _PROMPT_BUILDS for c in _calls(fn))
            if reads and builds and not _reaches_view(fn, self.helpers) \
                    and not self.cleared(fn):
                yield (f"{self.rel}:{fn.lineno}: {fn.name} reads posts and renders a "
                       "prompt without regex view()")


    def _expansions(self):
        bindings = _expand_bindings(self.tree)
        for call in _calls(self.tree):
            if not _is_searchdocs_expand(call, bindings):
                continue
            fn = _owner(self.fns, call)
            if fn is None:
                continue
            renders = any(_name(c) in _PROMPT_BUILDS for c in _calls(fn))
            if not (renders or fn.name in PINNED.get(self.rel, ())
                    or fn.name in EXPANSION_PINNED.get(self.rel, ())):
                continue
            phase = next((k.value for k in call.keywords if k.arg == "phase"), None)
            if isinstance(phase, ast.Constant) and phase.value == "prompt":
                continue
            if not self.cleared(call):
                yield (f"{self.rel}:{call.lineno}: expand() feeds a prompt without the "
                       "constant phase=\"prompt\"")


def _scan():
    """(offences, markers): each offence a `file:line` description, each marker
    the reason it was cleared with."""
    offences: list[str] = []
    markers: list[str] = []
    for rel, text in _sources():
        offences += _File(rel, text, markers).offences()
    offences += ["a `# regex-ok:` marker with no reason"] * markers.count("")
    return offences, markers


def test_every_prompt_reader_reads_the_prompt_view():
    offences, _ = _scan()
    assert not offences, (
        "these read transcript text into a prompt without the prompt phase "
        "(store.regex.view.view(..., phase=\"prompt\")), so output rules never "
        f"reach what the model is shown: {offences}")


def test_markers_are_capped():
    _, markers = _scan()
    assert len(markers) <= MAX_MARKERS, (
        f"{len(markers)} `# regex-ok:` exemptions; at most {MAX_MARKERS} -- route "
        "the reader through the view instead")


def test_the_walk_finds_the_readers():
    """A guard that passed because it found nothing is the failure mode here."""
    seen = sum(1 for _, text in _sources() for _ in _calls(ast.parse(text), "transcript_text"))
    absorbs = sum(1 for _, text in _sources()
                  for c in _calls(ast.parse(text), "materialize") if _is_absorb_materialize(c))
    assert seen >= 5, f"only {seen} transcript_text calls found; the walk is not finding them"
    assert absorbs >= 1, "no absorb.materialize call found; the walk is not finding it"


def test_the_guard_catches_a_raw_reader(tmp_path, monkeypatch):
    """The rules fire on the shapes they describe, read through the same scan."""
    routes = tmp_path / "routes"
    (tmp_path / "store" / "context").mkdir(parents=True)
    routes.mkdir()
    (routes / "bad.py").write_text(
        "def summary(cid, sid):\n"
        "    scene = store.scenes.read_scene(cid, sid)\n"
        "    return chronicle.transcript_text(scene['messages'])\n"
        "\n"
        "def selector(cid, sid):\n"
        "    messages = store.scenes.read_scene(cid, sid)['messages']\n"
        "    return prompts.render('x.j2', posts=[m['content'] for m in messages])\n"
        "\n"
        "def absorbed(cid, sid, parsed, prepared):\n"
        "    return store.absorb.materialize(cid, sid, parsed, prepared.scene['messages'])\n"
        "\n"
        "def good(cid, sid):\n"
        "    scene = store.scenes.read_scene(cid, sid)\n"
        "    shown = store.regex.view.view(scene['messages'], cid=cid, phase='prompt')\n"
        "    return prompts.render('x.j2', t=chronicle.transcript_text(shown))\n"
        "\n"
        "def displayed(cid, sid):\n"
        "    scene = store.scenes.read_scene(cid, sid)\n"
        "    shown = store.regex.view.view(scene['messages'], cid=cid, phase='display')\n"
        "    return chronicle.transcript_text(shown)\n"
        "\n"
        "def overlaid(cid, sid):\n"
        "    shown = store.overlay.view(cid)\n"
        "    return chronicle.transcript_text(shown)\n"
        "\n"
        "def unphased(cid, sid, messages):\n"
        "    return chronicle.transcript_text(regex_view.view(messages, cid=cid))\n"
        "\n"
        "def also_good(cid, sid, messages):\n"
        "    a = chronicle.transcript_text(regex_view.view(messages, cid=cid, phase='prompt'))\n"
        "    return a + chronicle.transcript_text(regex.view.view(messages, cid=cid, phase='prompt'))\n",
        encoding="utf-8")
    monkeypatch.setitem(globals(), "PACKAGE", tmp_path)
    offences, _ = _scan()
    # Only `regex_view.view` / `regex.view.view` with `phase="prompt"` counts:
    # another module's `view` (the overlay's) and the display phase do not.
    assert [o.split(":")[1] for o in offences if o.startswith("routes/bad.py:")] == \
        ["3", "20", "24", "27", "10", "5"]


def test_the_guard_catches_an_expansion_rendered_into_a_prompt(tmp_path, monkeypatch):
    """The `expand(...)` rule (roadmap 08-C3b): a prompt rendered from excerpts in
    the display phase, in a phase the guard cannot read, or with none, fails,
    and so does one reached through an aliased module or a function imported by
    value; the constant prompt phase passes, and so do a display read that
    renders no prompt and a local callable that merely shares the name."""
    history = tmp_path / "store" / "history"
    history.mkdir(parents=True)
    (tmp_path / "routes").mkdir()
    (history / "bad.py").write_text(
        "from ..searchdocs import expand as history_expand\n"
        "from ..searchdocs.expand import expand as excerpts_of\n"
        "\n"
        "def displayed(cid, sid, terms):\n"
        "    found = expand.expand(cid, sid, phase='display', terms=terms)\n"
        "    return prompts.render('x.j2', excerpts=found.excerpts)\n"
        "\n"
        "def variable(cid, sid, terms, phase):\n"
        "    found = expand.expand(cid, sid, phase=phase, terms=terms)\n"
        "    return prompts.render('x.j2', excerpts=found.excerpts)\n"
        "\n"
        "def unnamed(cid, sid, terms):\n"
        "    found = store.searchdocs.expand.expand(cid, sid, terms=terms)\n"
        "    return prompts.render('x.j2', excerpts=found.excerpts)\n"
        "\n"
        "def good(cid, sid, terms):\n"
        "    found = expand.expand(cid, sid, phase='prompt', terms=terms)\n"
        "    return prompts.render('x.j2', excerpts=found.excerpts)\n"
        "\n"
        "def shown_to_a_reader(cid, sid, terms):\n"
        "    return expand.expand(cid, sid, phase='display', terms=terms)\n"
        "\n"
        "def macro(text, expand):\n"
        "    return prompts.render('x.j2', body=expand(text))\n"
        "\n"
        "def aliased(cid, sid, terms):\n"
        "    found = history_expand.expand(cid, sid, phase='display', terms=terms)\n"
        "    return prompts.render('x.j2', excerpts=found.excerpts)\n"
        "\n"
        "def by_value(cid, sid, terms):\n"
        "    found = excerpts_of(cid, sid, phase='display', terms=terms)\n"
        "    return prompts.render('x.j2', excerpts=found.excerpts)\n",
        encoding="utf-8")
    monkeypatch.setitem(globals(), "PACKAGE", tmp_path)
    offences, _ = _scan()
    assert sorted(int(o.split(":")[1]) for o in offences
                  if o.startswith("store/history/bad.py:")) == [5, 9, 13, 27, 31]


def test_searchdocs_is_scanned():
    """`expand` renders its excerpts with `transcript_text` beside its own view;
    the scan has to actually reach it for that to be held."""
    rels = [rel for rel, _ in _sources()]
    assert "store/searchdocs/expand.py" in rels

def test_history_adapter_pin_requires_real_prompt_expansion():
    source = ("from ..searchdocs import expand as searchdocs_expand\n"
              "def excerpts(cid, scene, query, max_bytes):\n"
              "    return searchdocs_expand.expand(cid, scene.sid, phase='prompt', terms=query.terms)\n")
    assert not list(_File("store/history/expand.py", source, []).offences())
    for bad in (source.replace("phase='prompt'", "phase='display'"),
                source.replace("phase='prompt'", "phase=phase"),
                source.replace("def excerpts(", "def renamed("),
                "def excerpts(cid, scene, query, max_bytes):\n    return ()\n"):
        assert list(_File("store/history/expand.py", bad, []).offences())
