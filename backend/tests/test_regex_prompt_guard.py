"""Guard: every LLM reader of transcript text reads the prompt view (regex spec 5.2).

Output-processing rules (`store/regex/`) change what a model is shown, never
what is stored, so each reader has to ask for the view itself:
`chronicle.transcript_text` deliberately applies no rules, because the exports
call it too and want the display phase. A reader that skips the view does not
fail -- it quietly shows the model the `<think>` block a rule was written to
strip, and an absorb then judges its citations against text the model never
saw. Held against the AST of `routes/` and `store/context/`, in the style of
`test_routing_guard.py`:

- every `transcript_text(...)` call renders a list bound by a call to `view(`
  in the same function (or the `view(...)` call itself);
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
  how a NEW reader is caught.

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
    "routes/character_turns.py": ("_selector_messages",),
    "routes/tracker.py": ("_locate", "_context_posts"),
    "routes/passage_characters.py": ("draft_character",),
}

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCENE_READS = {"read_scene", "_require_scene"}
_PROMPT_BUILDS = {"render"}


def _sources():
    files = sorted((PACKAGE / "routes").glob("*.py")) + \
        sorted((PACKAGE / "store" / "context").glob("*.py"))
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
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call) \
                and _name(node.value) == "view":
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            out |= {t.id for t in targets if isinstance(t, ast.Name)}
    return out


def _viewed(arg, fn, *, snapshot: bool = False) -> bool:
    if isinstance(arg, ast.Call) and _name(arg) == "view":
        return True
    if isinstance(arg, ast.Name) and fn is not None and arg.id in _bound_by_view(fn):
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
    if any(True for _ in _calls(fn, "view")):
        return True
    return any(_name(c) in helpers and any(True for _ in _calls(helpers[_name(c)], "view"))
               for c in _calls(fn))


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
        yield from self._new_readers()

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
        "    return prompts.render('x.j2', t=chronicle.transcript_text(shown))\n",
        encoding="utf-8")
    monkeypatch.setitem(globals(), "PACKAGE", tmp_path)
    offences, _ = _scan()
    assert [o.split(":")[1] for o in offences if o.startswith("routes/bad.py:")] == \
        ["3", "10", "5"]
